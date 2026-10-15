"""Scoped, paginated reads. Replay has no dependency on an executor."""

from contextlib import contextmanager
from datetime import timezone
import json
import time

from sqlalchemy import delete, func, select

from app.models import Conversation, WorkSession
from app.models.agent_trace import AgentTrace, TraceEvent, TraceSpan
from app.services.trace import privacy
from app.services.trace.types import ERRORS, PHASES, STATUSES, TraceEventType


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def decoded(value, fallback):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def trace_dto(row):
    return {
        "trace_id": row.id,
        "task_id": row.task_id,
        "conversation_id": row.conversation_id,
        "work_session_id": row.work_session_id,
        "status": row.status if row.status in STATUSES else "interrupted",
        "started_at": iso(row.started_at),
        "completed_at": iso(row.completed_at),
        "duration_ms": row.duration_ms,
        "event_count": row.event_count,
        "span_count": row.span_count,
        "dropped_events": row.dropped_events,
        "incomplete": row.incomplete,
        "schema_version": row.schema_version,
    }


def span_dto(row):
    code = row.error_code if row.error_code in ERRORS else None
    return {
        "span_id": row.id,
        "parent_span_id": row.parent_span_id,
        "kind": row.kind if row.kind in PHASES else "task",
        "phase": row.phase if row.phase in PHASES else "task",
        "tool_name": privacy.identifier(row.tool_name),
        "model_name": privacy.identifier(row.model_name),
        "started_at": iso(row.started_at),
        "completed_at": iso(row.completed_at),
        "duration_ms": row.duration_ms,
        "status": row.status if row.status in STATUSES | {"denied"} else "interrupted",
        "error_code": code,
        "error_summary": ERRORS.get(code),
        "retry_count": row.retry_count,
        "verification_status": row.verification_status
        if row.verification_status in {"passed", "rule_passed", "failed", "not_provided"}
        else "not_provided",
        "metadata": privacy.metadata(decoded(row.metadata_json, {})),
        "evidence": privacy.evidence(decoded(row.evidence_json, [])),
    }


class TraceRepository:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def read(self):
        started = time.perf_counter()
        try:
            with self.store.factory() as db:
                yield db
        finally:
            self.store.metrics["last_query_ms"] = (time.perf_counter() - started) * 1000

    @staticmethod
    def scope(db, conversation_id, work_session_id=None):
        conversation = db.get(Conversation, conversation_id)
        if (
            not conversation
            or conversation.is_deleted
            or conversation.conversation_type not in {"work", "project"}
        ):
            raise LookupError("工作会话不存在")
        if work_session_id:
            work = db.get(WorkSession, work_session_id)
            if not work or work.conversation_id != conversation_id:
                raise LookupError("工作会话不存在")

    def owned(self, db, trace_id, conversation_id, work_session_id=None):
        self.scope(db, conversation_id, work_session_id)
        row = db.get(AgentTrace, trace_id)
        if (
            not row
            or row.conversation_id != conversation_id
            or work_session_id
            and row.work_session_id != work_session_id
        ):
            raise LookupError("执行轨迹不存在")
        if row.work_session_id:
            self.scope(db, conversation_id, row.work_session_id)
        if row.schema_version != 1:
            raise ValueError("该轨迹版本暂不受支持")
        return row

    def listing(
        self,
        conversation_id,
        *,
        work_session_id=None,
        task_id=None,
        status=None,
        tool_name=None,
        since=None,
        until=None,
        cursor=0,
        limit=50,
    ):
        with self.read() as db:
            self.scope(db, conversation_id, work_session_id)
            query = select(AgentTrace).where(AgentTrace.conversation_id == conversation_id)
            if work_session_id:
                query = query.where(AgentTrace.work_session_id == work_session_id)
            if task_id:
                query = query.where(AgentTrace.task_id == task_id)
            if status:
                query = query.where(AgentTrace.status == status)
            if tool_name:
                query = query.where(
                    AgentTrace.id.in_(
                        select(TraceSpan.trace_id).where(TraceSpan.tool_name == tool_name)
                    )
                )
            if since:
                query = query.where(AgentTrace.started_at >= since)
            if until:
                query = query.where(AgentTrace.started_at <= until)
            rows = db.scalars(
                query.order_by(AgentTrace.started_at.desc(), AgentTrace.id)
                .offset(cursor)
                .limit(limit + 1)
            ).all()
            more = len(rows) > limit
            return {
                "items": [trace_dto(row) for row in rows[:limit]],
                "has_more": more,
                "next_cursor": cursor + limit if more else None,
                "limit": limit,
            }

    def detail(self, trace_id, conversation_id, work_session_id=None):
        with self.read() as db:
            return trace_dto(self.owned(db, trace_id, conversation_id, work_session_id))

    def events(self, trace_id, conversation_id, *, work_session_id=None, after=0, limit=200):
        with self.read() as db:
            trace = self.owned(db, trace_id, conversation_id, work_session_id)
            rows = db.scalars(
                select(TraceEvent)
                .where(TraceEvent.trace_id == trace_id, TraceEvent.sequence > after)
                .order_by(TraceEvent.sequence)
                .limit(limit + 1)
            ).all()
            more = len(rows) > limit
            span_ids = {row.span_id for row in rows[:limit] if row.span_id}
            spans = (
                {
                    row.id: span_dto(row)
                    for row in db.scalars(
                        select(TraceSpan).where(
                            TraceSpan.trace_id == trace_id, TraceSpan.id.in_(span_ids)
                        )
                    )
                }
                if span_ids
                else {}
            )
            result = []
            for row in rows[:limit]:
                data = decoded(row.data_json, {})
                if not isinstance(data, dict):
                    data = {}
                code = data.get("error_code")
                code = code if isinstance(code, str) and code in ERRORS else None
                result.append(
                    {
                        "sequence": row.sequence,
                        "trace_id": trace.id,
                        "task_id": trace.task_id,
                        "conversation_id": trace.conversation_id,
                        "work_session_id": trace.work_session_id,
                        "span_id": row.span_id,
                        "event_type": row.event_type
                        if row.event_type in TraceEventType._value2member_map_
                        else "OBSERVATION_CREATED",
                        "phase": row.phase if row.phase in PHASES else "task",
                        "status": row.status
                        if row.status in STATUSES | {"denied"}
                        else "interrupted",
                        "created_at": iso(row.created_at),
                        "duration_ms": privacy.metadata(
                            {"duration_ms": data.get("duration_ms")}
                        ).get("duration_ms"),
                        "error_code": code,
                        "error_summary": ERRORS.get(code),
                        "verification_status": data.get("verification_status")
                        if isinstance(data.get("verification_status"), str)
                        and data.get("verification_status")
                        in {"passed", "rule_passed", "failed", "not_provided"}
                        else "not_provided",
                        "metadata": privacy.metadata(data.get("metadata")),
                        "evidence": privacy.evidence(data.get("evidence")),
                        "span": spans.get(row.span_id),
                        "source_work_event_id": row.source_work_event_id,
                    }
                )
            return {
                "items": result,
                "has_more": more,
                "next_cursor": rows[limit - 1].sequence if more else None,
                "limit": limit,
            }

    def summary(self, trace_id, conversation_id, work_session_id=None):
        with self.read() as db:
            trace = self.owned(db, trace_id, conversation_id, work_session_id)
            spans = db.scalars(
                select(TraceSpan)
                .where(TraceSpan.trace_id == trace_id)
                .limit(self.store.event_limit)
            ).all()
            totals = {
                kind: sum(row.duration_ms or 0 for row in spans if row.kind == kind)
                for kind in PHASES
            }
            tool_rows = [row for row in spans if row.kind == "tool"]
            executed = sum(
                privacy.metadata(decoded(row.metadata_json, {})).get("executed") is True
                for row in tool_rows
            )
            counts = dict(
                db.execute(
                    select(TraceEvent.event_type, func.count())
                    .where(TraceEvent.trace_id == trace_id)
                    .group_by(TraceEvent.event_type)
                ).all()
            )
            return {
                "trace_id": trace_id,
                "task_total_ms": trace.duration_ms,
                "model_ms": totals["model"],
                "tool_ms": totals["operation"] or totals["tool"],
                "scheduler_ms": totals["scheduler"],
                "permission_wait_ms": totals["permission"],
                "recovery_ms": totals["recovery"],
                "model_requests": sum(row.kind == "model" for row in spans),
                "tool_requests": len(tool_rows),
                "tools_executed": executed,
                "verification_passes": counts.get("VERIFICATION_PASSED", 0),
                "verification_failures": counts.get("VERIFICATION_FAILED", 0),
                "retries": counts.get("RETRY_STARTED", 0),
                "incomplete": trace.incomplete,
            }

    def remove(self, trace_id, conversation_id, work_session_id=None):
        with self.read() as db:
            row = self.owned(db, trace_id, conversation_id, work_session_id)
            if row.status not in {"completed", "failed", "cancelled", "interrupted"}:
                raise ValueError("请等任务结束后再删除轨迹")
            db.execute(delete(TraceEvent).where(TraceEvent.trace_id == trace_id))
            db.execute(delete(TraceSpan).where(TraceSpan.trace_id == trace_id))
            db.delete(row)
            db.commit()
            return {"deleted": True}
