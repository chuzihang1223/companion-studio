"""Observe WorkState and RuntimeRegistry; never own or schedule their work."""

import asyncio
from collections import OrderedDict
from contextlib import contextmanager, nullcontext

from sqlalchemy import select

from app.models.agent_trace import AgentTrace
from app.services.trace import privacy
from app.services.trace.scope import Span, TraceContext, active_span, current_span, current_trace
from app.services.trace.types import TERMINAL


class TraceRecorder:
    def __init__(self, store):
        self.store = store
        self.contexts = OrderedDict()
        self.bindings = OrderedDict()

    def create(self, task_id, conversation_id=None):
        context = TraceContext(self, task_id, privacy.uuid_value(conversation_id))
        self.remember(context)
        context.send(
            "create",
            span_id=context.root_span_id,
            event_type="TASK_CREATED",
            phase="task",
            status="planning",
        )
        context.send("span_start", span_id=context.root_span_id, kind="task", phase="task")
        return context

    def remember(self, context):
        self.contexts[context.task_id] = context
        while len(self.contexts) > 512:
            _, evicted = self.contexts.popitem(last=False)
            evicted.incomplete = True
        while len(self.bindings) > 1024:
            self.bindings.popitem(last=False)

    async def execution_scope(self, task_id, conversation_id, session_id):
        try:
            context = self.contexts.get(task_id)
            if context is None:

                def load():
                    with self.store.factory() as db:
                        return db.scalar(select(AgentTrace).where(AgentTrace.task_id == task_id))

                row = await asyncio.to_thread(load)
                if row and row.status in TERMINAL:
                    return nullcontext()
                if row:
                    context = TraceContext(
                        self, task_id, conversation_id, trace_id=row.id, work_session_id=session_id
                    )
                    context.incomplete = (
                        True  # Monotonic time cannot continue across restart/eviction.
                    )
                    context.send("segment")
                    context.send(
                        "span_start",
                        span_id=context.root_span_id,
                        kind="task",
                        phase="task",
                        metadata={"legacy": True, "incomplete": True},
                        event_type="TASK_RESUMED",
                    )
                    self.remember(context)
                else:
                    context = self.create(task_id, conversation_id)
                    context.incomplete = True  # Legacy plan: do not fabricate its planning history.
            if context.finished:
                return nullcontext()
            context.bind(conversation_id, session_id)
            return self._execution(context)
        except Exception:
            return nullcontext()  # Trace read failures never reject the actual task.

    @contextmanager
    def _execution(self, context):
        trace_token, span_token = current_trace.set(context), current_span.set(context.root_span_id)
        context.executing = True
        if context.permission:
            context.permission.metadata["decision"] = "submitted"
            context.permission.finish(event_type="PERMISSION_RESOLVED")
            context.permission = None
        try:
            yield
        except BaseException as error:
            context.finish(error)
            raise
        else:
            context.finish()
        finally:
            context.executing = False
            current_span.reset(span_token)
            current_trace.reset(trace_token)

    def project_work_event(self, session_id, conversation_id, kind, detail, event_id):
        try:
            context = self.contexts.get(self.bindings.get(session_id))
            current = current_trace.get()
            if context is None and current and current.work_session_id in {None, session_id}:
                context = current
                context.bind(conversation_id, session_id)
            if context is None or context.finished:
                return
            common = {"phase": "task", "source_work_event_id": event_id}
            if kind == "tool_started":
                handle = active_span.get()
                if handle:
                    handle.metadata["executed"] = True
                context.send(
                    "event",
                    event_type="OBSERVATION_CREATED",
                    span_id=handle.id if handle else None,
                    phase="tool",
                    metadata={"executed": True},
                    source_work_event_id=event_id,
                )
                return
            if kind == "tool_completed":
                handle = active_span.get()
                if handle:
                    handle.source_work_event_id = event_id
                return
            if kind in {
                "task_completed",
                "work_completed",
                "task_failed",
                "task_cancelled",
                "task_interrupted",
                "work_interrupted",
                "work_stopped",
                "task_cancel",
            }:
                status = {
                    "task_completed": "completed",
                    "work_completed": "completed",
                    "task_failed": "failed",
                    "task_interrupted": "interrupted",
                    "work_interrupted": "interrupted",
                }.get(kind, "cancelled")
                context.terminal = (status, event_id)
                if not context.executing:
                    context.finish()
                return
            if kind == "permission_requested":
                if context.permission is None:
                    context.permission = Span(
                        context,
                        "permission",
                        "PERMISSION_REQUESTED",
                        "PERMISSION_RESOLVED",
                        metadata={"wait_kind": "permission", "requires_permission": True},
                    )
                context.send("state", status="waiting_permission")
            elif kind == "permission_resolved":
                if context.permission:
                    context.permission.finish()
                    context.permission = None
            elif kind == "retrying":
                handle = active_span.get()
                if handle:
                    handle.metadata["attempt"] = detail.get("attempt", 0)
                if context.recovery:
                    context.recovery.finish()
                context.recovery = Span(
                    context,
                    "recovery",
                    "RETRY_STARTED",
                    "OBSERVATION_CREATED",
                    metadata={"attempt": detail.get("attempt", 0)},
                )
            elif kind in {"task_pause", "task_takeover", "user_interruption"}:
                context.send(
                    "state",
                    status="paused",
                    event_type="TASK_PAUSED",
                    metadata={"wait_kind": "user"},
                    **common,
                )
                if not context.recovery:
                    context.recovery = Span(
                        context,
                        "recovery",
                        None,
                        "OBSERVATION_CREATED",
                        metadata={"wait_kind": "user"},
                    )
            elif kind == "task_resume":
                if context.recovery:
                    context.recovery.finish()
                    context.recovery = None
                context.send("state", status="running", event_type="TASK_RESUMED", **common)
            elif kind in {"step_started", "verification", "plan_updated"}:
                if kind == "step_started" and context.recovery:
                    context.recovery.finish()
                    context.recovery = None
                context.send(
                    "event",
                    event_type="OBSERVATION_CREATED",
                    metadata={
                        "verified": detail.get("verified"),
                        "step_count": len(detail.get("steps", []))
                        if isinstance(detail.get("steps"), list)
                        else 0,
                    },
                    **common,
                )
        except Exception:
            # The hook is called AFTER the canonical WorkEvent transaction.
            return

    def lifecycle(self, task_id, stage):
        try:
            context = self.contexts.get(task_id)
            if not context or context.finished:
                return
            if context.queue_span:
                error = (
                    TimeoutError()
                    if stage == "timeout"
                    else asyncio.CancelledError()
                    if stage == "cancelled"
                    else None
                )
                context.queue_span.finish(error, event_type="OBSERVATION_CREATED")
                context.queue_span = None
            if stage in {"queued", "capacity"}:
                context.queue_span = Span(
                    context,
                    "scheduler",
                    "TASK_QUEUED",
                    "OBSERVATION_CREATED",
                    metadata={
                        "wait_kind": "conversation_lock"
                        if stage == "queued"
                        else "global_capacity",
                        "max_concurrency": 3,
                    },
                )
                context.send("state", status="queued")
            elif stage == "running":
                context.send(
                    "state", status="running", event_type="TASK_RUNNING", phase="scheduler"
                )
        except Exception:
            return
