"""Atomic SQLite reservations survive restart; unknown requests retain their reservation.

Prices and context capacities are user declarations. Money limits constrain conservative
reservations/estimates, not provider invoices. Unknown prices cannot pass a money cap.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import func, select, text, update
from app.models.model_routing import ModelRequestRow as Row
from app.services.llm.routing.contracts import RoutingError


def estimate_micros(entry, policy):
    record = entry.record
    if record.price_source != "USER" or record.context_window is None:
        return None
    if record.cost_currency != policy.cost_currency:
        return None
    amount = (
        Decimal(str(record.input_cost)) * record.context_window
        + Decimal(str(record.output_cost)) * policy.max_output_tokens
    )
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def money_micros(value):
    return (
        None
        if value is None
        else int((Decimal(str(value)) * 1000000).to_integral_value(rounding=ROUND_CEILING))
    )


class BudgetLedger:
    def __init__(self, factory, today=None):
        self.factory = factory
        self.today = today or (lambda: datetime.now(timezone.utc).date().isoformat())

    def reserve(self, entry, policy, task_id, workflow_id=None, decision=None):
        cost = estimate_micros(entry, policy)
        provider_ref = sha256(
            (entry.service.config.provider + "\n" + entry.service.config.base_url).encode()
        ).hexdigest()
        money_caps = [
            getattr(policy, "max_cost_per_" + kind)
            for kind in ("task", "workflow", "day", "provider")
        ]
        if policy.budget_mode == "STRICT" and (
            policy.max_cost_per_request is not None
            or any(value is not None for value in money_caps)
        ):
            # Generic compatible endpoints have no trusted input-token counter or
            # validated tokenizer. Do not sell a context declaration as a hard cap.
            raise RoutingError("INPUT_TOKEN_BOUND_UNAVAILABLE")
        if policy.max_cost_per_request is not None:
            if cost is None:
                raise RoutingError("PRICE_OR_CONTEXT_UNKNOWN")
            if cost > money_micros(policy.max_cost_per_request):
                raise RoutingError("REQUEST_COST_LIMIT")
        with self.factory() as db:
            # Serialize check + insert across both threads and independent processes.
            db.execute(text("BEGIN IMMEDIATE"))
            # Take the day once, after obtaining the write lock. Requests queued
            # across midnight must check and insert into the same UTC budget day.
            current_day = self.today()
            conditions = [
                ("task", Row.task_id == task_id),
                ("workflow", Row.workflow_id == workflow_id),
                ("day", Row.day == current_day),
                ("provider", (Row.day == current_day) & (Row.provider_ref == provider_ref)),
            ]
            for (kind, condition), money in zip(conditions, money_caps):
                cap = getattr(policy, "max_requests_per_" + kind)
                if kind == "workflow" and workflow_id is None:
                    if cap is not None or money is not None:
                        raise RoutingError("WORKFLOW_SCOPE_REQUIRED")
                    continue
                count, total, unknown, other_currency = db.execute(
                    select(
                        func.count(),
                        func.coalesce(func.sum(Row.reserved_micros), 0),
                        func.sum(Row.reserved_micros.is_(None)),
                        func.sum(Row.currency != policy.cost_currency),
                    ).where(condition)
                ).one()
                if cap is not None and count >= cap:
                    raise RoutingError(kind.upper() + "_REQUEST_LIMIT")
                if money is not None:
                    if cost is None or unknown or other_currency:
                        raise RoutingError("PRICE_OR_CONTEXT_UNKNOWN")
                    if total + cost > money_micros(money):
                        raise RoutingError(kind.upper() + "_COST_LIMIT")
            request_id = str(uuid4())
            db.add(
                Row(
                    id=request_id,
                    task_id=task_id,
                    workflow_id=workflow_id,
                    day=current_day,
                    provider_ref=provider_ref,
                    model_id=entry.record.model_id,
                    config_version=entry.version,
                    currency=policy.cost_currency,
                    reserved_micros=cost,
                    estimated_micros=cost,
                    status="RESERVED",
                    decision_json=json.dumps(decision, allow_nan=False) if decision else None,
                )
            )
            db.commit()
        return request_id

    def finish(self, request_id, status, usage=None):
        with self.factory() as db:
            # Keep the maximum reservation even when actual usage is absent, the
            # request failed, or cancellation happened after network dispatch.
            db.execute(
                update(Row)
                .where(Row.id == request_id)
                .values(
                    status=status, usage_json=json.dumps(usage, allow_nan=False) if usage else None
                )
            )
            db.commit()

    def recent(self, limit=30):
        with self.factory() as db:
            rows = db.scalars(select(Row).order_by(text("rowid DESC")).limit(limit)).all()
            return [
                {
                    "request_id": row.id,
                    "model_id": row.model_id,
                    "config_version": row.config_version,
                    "status": row.status,
                    "day": row.day,
                    "currency": row.currency,
                    "reserved_cost": row.reserved_micros / 1000000
                    if row.reserved_micros is not None
                    else None,
                    "cost_status": "USER_ESTIMATED"
                    if row.reserved_micros is not None
                    else "UNKNOWN",
                    "usage": json.loads(row.usage_json) if row.usage_json else None,
                    "decision": json.loads(row.decision_json) if row.decision_json else None,
                }
                for row in rows
            ]
