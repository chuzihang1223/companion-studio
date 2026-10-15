"""A small execution ledger. Commits precede external actions; failures fail closed.

Transactions never span a tool, await, or user interaction. The unique operation
ID and CAS lease prevent local duplicate owners, not external Exactly Once.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID, uuid4, uuid5

from sqlalchemy import delete, select, update

from app.database.session import SessionLocal
from app.models import AgentCheckpoint, Conversation, RecoveryAttempt, RecoveryTask, WorkSession
from app.services.recovery.policy import side_effect_class
from app.services.recovery.types import RecoveryBudget, TERMINAL, TRANSITIONS


class RecoveryError(RuntimeError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def decoded(value):
    try:
        result = json.loads(value)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (TypeError, ValueError) as exc:
        raise RecoveryError("CHECKPOINT_INVALID") from exc


def plan_hash(context):
    return digest({"calls": context.calls, "mode": context.coding_mode})


def workspace_hash(context, work):
    snapshot = work.snapshot(context.work_session_id)
    return digest(
        {
            "selected": snapshot.get("workspace_root"),
            "effective": str(context.executor.workspace.resolve()),
            "mode": context.coding_mode,
        }
    )


def checkpoint_dto(row):
    if row.schema_version != 1 or row.state not in {
        "PREPARED",
        "EXECUTING",
        "OBSERVED",
        "VERIFIED",
        "COMMITTED",
        "FAILED",
        "DENIED",
    }:
        raise RecoveryError("CHECKPOINT_INVALID")
    # Intent contains private local hashes/relative file identity, never bodies.
    intent, result = decoded(row.intent_json), decoded(row.result_json)
    return {
        "checkpoint_id": row.id,
        "schema_version": row.schema_version,
        "task_id": row.task_id,
        "step_id": row.id,
        "step_index": row.step_index,
        "tool_call_id": row.tool_call_id,
        "tool_name": row.tool_name,
        "operation_id": row.operation_id,
        "idempotency_key": row.idempotency_key,
        "side_effect_class": row.side_effect_class,
        "step_state": row.state,
        "permission_state": row.permission_state,
        "execution_intent": intent,
        "result_summary": result,
        "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "updated_at": row.updated_at.replace(tzinfo=timezone.utc).isoformat(),
    }


class CheckpointManager:
    def __init__(self, factory=SessionLocal):
        self.factory = factory
        self.instance_id = str(uuid4())
        self.on_change = None

    def scope(self, db, task_id, conversation_id, session_id=None):
        row = db.get(RecoveryTask, task_id)
        conversation = db.get(Conversation, conversation_id)
        work = db.get(WorkSession, row.work_session_id) if row else None
        if (
            not row
            or not conversation
            or conversation.is_deleted
            or conversation.conversation_type not in {"work", "project"}
            or row.conversation_id != conversation_id
            or not work
            or work.conversation_id != conversation_id
            or (session_id and row.work_session_id != session_id)
        ):
            raise RecoveryError("RECOVERY_NOT_FOUND", 404)
        if row.schema_version != 1:
            raise RecoveryError("CHECKPOINT_INVALID")
        return row

    def begin(self, context, work, trace_id=None):
        root = context.recovery_task_id or context.task_id
        with self.factory() as db:
            row = db.get(RecoveryTask, root)
            if row:
                self.scope(db, root, context.conversation_id, context.work_session_id)
                if row.plan_hash != plan_hash(context) or row.workspace_hash != workspace_hash(
                    context, work
                ):
                    raise RecoveryError("CHECKPOINT_CONFLICT")
                if row.state in TERMINAL:
                    raise RecoveryError("TASK_ALREADY_TERMINAL")
            else:
                if not context.conversation_id or not context.work_session_id:
                    raise RecoveryError("CHECKPOINT_SCOPE_REQUIRED")
                db.add(
                    RecoveryTask(
                        id=root,
                        conversation_id=context.conversation_id,
                        work_session_id=context.work_session_id,
                        renderer_id=context.renderer_id,
                        trace_id=trace_id,
                        execution_id=context.task_id,
                        plan_hash=plan_hash(context),
                        workspace_hash=workspace_hash(context, work),
                        state="RECOVERING",
                        budget_json=RecoveryBudget().model_dump_json(),
                    )
                )
                db.commit()
        return root

    def prepare(self, root, call, index, intent):
        step_id = str(uuid5(UUID(root), call["id"]))
        with self.factory() as db:
            task = db.get(RecoveryTask, root)
            self.budget_check(task)
            counters = decoded(task.counters_json)
            counters["total_steps"] = counters.get("total_steps", 0) + 1
            task.counters_json = encoded(counters)
            row = db.get(AgentCheckpoint, step_id)
            key = digest({"task_id": root, "call": call})
            if row:
                if row.idempotency_key != key or row.state not in {"PREPARED", "FAILED", "DENIED"}:
                    raise RecoveryError("SIDE_EFFECT_UNCERTAIN")
                row.intent_json = encoded(intent)
                row.state, row.updated_at = "PREPARED", datetime.now(timezone.utc)
            else:
                row = AgentCheckpoint(
                    id=step_id,
                    task_id=root,
                    operation_id=str(uuid4()),
                    tool_call_id=call["id"],
                    tool_name=call["name"],
                    step_index=index,
                    side_effect_class=side_effect_class(call["name"]),
                    idempotency_key=key,
                    intent_json=encoded(intent),
                )
                db.add(row)
            db.commit()
        return step_id

    def step(self, step_id, state, *, result=None, permission=None):
        order = {
            "PREPARED": {"EXECUTING", "DENIED"},
            "EXECUTING": {"OBSERVED", "FAILED"},
            "OBSERVED": {"VERIFIED", "FAILED"},
            "VERIFIED": {"COMMITTED"},
            "COMMITTED": set(),
            "FAILED": set(),
            "DENIED": set(),
        }
        with self.factory() as db:
            row = db.get(AgentCheckpoint, step_id)
            if not row or state not in order.get(row.state, set()):
                raise RecoveryError("CHECKPOINT_CONFLICT")
            row.state, row.updated_at = state, datetime.now(timezone.utc)
            if result is not None:
                row.result_json = encoded(
                    {
                        key: result[key]
                        for key in (
                            "verified",
                            "reconciled",
                            "running",
                            "installed",
                            "window_ready",
                            "virtual",
                            "exit_code",
                        )
                        if key in result
                        and (isinstance(result[key], (bool, int)) or result[key] is None)
                    }
                )
            if permission:
                row.permission_state = permission
            db.commit()

    def intent(self, step_id):
        with self.factory() as db:
            row = db.get(AgentCheckpoint, step_id)
            if not row or row.schema_version != 1:
                raise RecoveryError("CHECKPOINT_INVALID")
            return decoded(row.intent_json)

    def state(self, root, state, failure=None):
        with self.factory() as db:
            row = db.get(RecoveryTask, root)
            if not row:
                return
            if row.state != state and state not in TRANSITIONS.get(row.state, set()):
                raise RecoveryError("INVALID_RECOVERY_TRANSITION")
            values = {
                "state": state,
                "updated_at": datetime.now(timezone.utc),
                "revision": row.revision + 1,
            }
            if failure:
                values["failure_json"] = failure.model_dump_json()
            elif state == "COMPLETED":
                values["failure_json"] = "{}"
            changed = db.execute(
                update(RecoveryTask)
                .where(
                    RecoveryTask.id == root,
                    RecoveryTask.state == row.state,
                    RecoveryTask.revision == row.revision,
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                raise RecoveryError("RECOVERY_CONFLICT")
            db.commit()
            session_id = row.work_session_id
        if self.on_change:
            self.on_change(session_id)

    def recover(self):
        """No old coroutine or operation is resurrected on startup."""
        with self.factory() as db:
            for row in db.scalars(
                select(RecoveryTask).where(
                    RecoveryTask.state.in_(
                        ["RECOVERING", "RESUMING", "REVALIDATING", "WAITING_USER"]
                    )
                )
            ):
                row.state, row.lease = "INTERRUPTED", None
                row.revision += 1
                row.updated_at = datetime.now(timezone.utc)
            for row in db.scalars(
                select(RecoveryAttempt).where(RecoveryAttempt.status == "STARTED")
            ):
                row.status = "INTERRUPTED"  # duration unknown after a process crash
            db.commit()

    def settle(self, root, state):
        """Late exceptions cannot resurrect a cancelled or already finished ledger."""
        with self.factory() as db:
            row = db.get(RecoveryTask, root)
            current = row.state if row else None
        if current in {"RECOVERING", "RESUMING", "WAITING_USER", "REVALIDATING"}:
            self.state(root, state)

    def reuse(self, root, context, call):
        from app.services.recovery.reconciliation import resource_snapshot

        with self.factory() as db:
            step_id = str(uuid5(UUID(root), call["id"]))
            row = db.get(AgentCheckpoint, step_id)
            if not row:
                raise RecoveryError("CHECKPOINT_INVALID")
            if row.state != "COMMITTED":
                intent = decoded(row.intent_json)
                current = resource_snapshot(context, call)
                if (
                    not intent.get("expected_hash")
                    or current.get("before_hash") != intent["expected_hash"]
                ):
                    raise RecoveryError("CHECKPOINT_CONFLICT")
                row.state, row.updated_at = "COMMITTED", datetime.now(timezone.utc)
                row.result_json = encoded({"verified": True, "reconciled": True})
                db.commit()

    def detail(self, root, conversation_id, session_id=None):
        with self.factory() as db:
            row = self.scope(db, root, conversation_id, session_id)
            work = db.get(WorkSession, row.work_session_id)
            steps = list(
                db.scalars(
                    select(AgentCheckpoint)
                    .where(AgentCheckpoint.task_id == root)
                    .order_by(AgentCheckpoint.step_index)
                )
            )
            budget = RecoveryBudget.model_validate(decoded(row.budget_json))
            checkpoints = [checkpoint_dto(s) for s in steps]
            return {
                "task_id": row.id,
                "conversation_id": row.conversation_id,
                "work_session_id": row.work_session_id,
                "renderer_id": row.renderer_id,
                "execution_id": row.execution_id,
                "trace_id": row.trace_id,
                "schema_version": row.schema_version,
                "plan_version": row.plan_version,
                "state": row.state,
                "revision": row.revision,
                "goal": work.goal[:1000],
                "failure": decoded(row.failure_json),
                "counters": decoded(row.counters_json),
                "budget": budget.model_dump(),
                "checkpoints": checkpoints,
                "completed_steps": sum(s.state == "COMMITTED" for s in steps),
                "updated_at": row.updated_at.replace(tzinfo=timezone.utc).isoformat(),
                "request_id": row.request_id,
            }

    def listing(self, conversation_id, session_id=None, limit=30):
        with self.factory() as db:
            query = select(RecoveryTask.id).where(RecoveryTask.conversation_id == conversation_id)
            if session_id:
                query = query.where(RecoveryTask.work_session_id == session_id)
            ids = list(db.scalars(query.order_by(RecoveryTask.updated_at.desc()).limit(limit)))
        return [self.detail(root, conversation_id, session_id) for root in ids]

    def claim(self, root, conversation_id, session_id, request_id, revision):
        with self.factory() as db:
            row = self.scope(db, root, conversation_id, session_id)
            if row.request_id == request_id:
                return None  # persistent request deduplication, including completed requests
            self.budget_check(row, resume=True)
            counters = decoded(row.counters_json)
            counters["resume_count"] = counters.get("resume_count", 0) + 1
            if row.state not in {
                "INTERRUPTED",
                "RECOVERY_AVAILABLE",
                "NEEDS_REVIEW",
                "FAILED",
                "WAITING_USER",
            }:
                raise RecoveryError("RECOVERY_BUSY_OR_TERMINAL")
            lease, execution_id = str(uuid4()), str(uuid4())
            changed = db.execute(
                update(RecoveryTask)
                .where(
                    RecoveryTask.id == root,
                    RecoveryTask.revision == revision,
                    RecoveryTask.lease.is_(None),
                )
                .values(
                    lease=lease,
                    state="REVALIDATING",
                    request_id=request_id,
                    execution_id=execution_id,
                    revision=revision + 1,
                    counters_json=encoded(counters),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            if changed.rowcount != 1:
                db.rollback()
                current = db.get(RecoveryTask, root)
                if current and current.request_id == request_id:
                    return None
                raise RecoveryError("RECOVERY_CONFLICT")
            db.commit()
        return lease, execution_id

    def release(self, root, lease):
        with self.factory() as db:
            db.execute(
                update(RecoveryTask)
                .where(
                    RecoveryTask.id == root,
                    RecoveryTask.lease == lease,
                )
                .values(lease=None)
            )
            db.commit()

    def attempt(self, root, step_id, failure, decision):
        with self.factory() as db:
            row = db.get(RecoveryTask, root)
            self.budget_check(row)
            counters = decoded(row.counters_json)
            budget = RecoveryBudget.model_validate(decoded(row.budget_json))
            if counters.get("recovery_attempts", 0) >= budget.max_recovery_cycles:
                raise RecoveryError("MAX_RETRIES_EXCEEDED")
            counters["recovery_attempts"] = counters.get("recovery_attempts", 0) + 1
            counters["retry_count"] = counters.get("retry_count", 0) + int(decision.retry)
            counters["total_steps"] = counters.get("total_steps", 0) + int(decision.retry)
            counters["replanning_count"] = counters.get("replanning_count", 0) + int(
                decision.level == 2
            )
            counters["human_interventions"] = counters.get("human_interventions", 0) + int(
                decision.approval
            )
            row.counters_json, row.failure_json = encoded(counters), failure.model_dump_json()
            attempt_id = str(uuid4())
            db.add(
                RecoveryAttempt(
                    id=attempt_id,
                    task_id=root,
                    step_id=step_id,
                    strategy=decision.strategy,
                    error_code=failure.error_code,
                )
            )
            db.commit()
        return attempt_id

    @staticmethod
    def budget_check(row, resume=False):
        budget = RecoveryBudget.model_validate(decoded(row.budget_json))
        counters = decoded(row.counters_json)
        age = (
            datetime.now(timezone.utc) - row.created_at.replace(tzinfo=timezone.utc)
        ).total_seconds()
        if (
            age >= budget.max_task_duration
            or counters.get("total_steps", 0) >= budget.max_total_steps
            or (resume and counters.get("resume_count", 0) >= budget.max_recovery_cycles)
        ):
            raise RecoveryError("MAX_RETRIES_EXCEEDED")

    def record_checks(self, root, checks, validated):
        with self.factory() as db:
            row = db.get(RecoveryTask, root)
            counters = decoded(row.counters_json)
            counters["checkpoint_checks"] = counters.get("checkpoint_checks", 0) + checks
            counters["validated_checkpoints"] = counters.get("validated_checkpoints", 0) + validated
            row.counters_json = encoded(counters)
            db.commit()

    def finish_attempt(self, attempt_id, status, duration_ms, observation=None):
        with self.factory() as db:
            row = db.get(RecoveryAttempt, attempt_id)
            if not row or row.status != "STARTED":
                return
            row.status, row.duration_ms = status, max(0, int(duration_ms))
            row.evidence_json = encoded(
                {k: v for k, v in (observation or {}).items() if isinstance(v, bool)}
            )
            task = db.get(RecoveryTask, row.task_id)
            counters = decoded(task.counters_json)
            counters["recovery_duration_ms"] = (
                counters.get("recovery_duration_ms", 0) + row.duration_ms
            )
            counters["recovery_successes"] = counters.get("recovery_successes", 0) + int(
                status == "SUCCEEDED"
            )
            task.counters_json = encoded(counters)
            db.commit()

    def prune(self, days=30):
        """Only terminal recovery ledgers; never WorkSession, chat, Trace or files."""
        with self.factory() as db:
            ids = list(
                db.scalars(
                    select(RecoveryTask.id)
                    .where(
                        RecoveryTask.state.in_(TERMINAL),
                        RecoveryTask.updated_at
                        < datetime.now(timezone.utc) - timedelta(days=max(1, days)),
                        RecoveryTask.lease.is_(None),
                    )
                    .limit(100)
                )
            )
            for model in (AgentCheckpoint, RecoveryAttempt):
                db.execute(delete(model).where(model.task_id.in_(ids)))
            db.execute(delete(RecoveryTask).where(RecoveryTask.id.in_(ids)))
            db.commit()
        return len(ids)
