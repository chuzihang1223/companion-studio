"""Context-local spans survive awaits/to_thread without crossing task owners."""

from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import time
from uuid import uuid4

from app.models.agent_trace import utc_now
from app.services.trace import privacy

current_trace = ContextVar("agent_trace", default=None)
current_span = ContextVar("agent_trace_span", default=None)
active_span = ContextVar("agent_trace_active_span", default=None)


class NullSpan:
    def __init__(self):
        self.metadata = {}
        self.verification_status = "not_provided"
        self.evidence = []

    def result(self, _value):
        pass


class Span:
    def __init__(
        self,
        context,
        kind,
        start_event,
        end_event,
        *,
        metadata=None,
        tool_name=None,
        model_name=None,
        phase=None,
    ):
        self.context, self.kind, self.end_event = context, kind, end_event
        self.id, self.started = str(uuid4()), time.perf_counter()
        self.phase = phase or kind
        self.metadata = privacy.metadata(metadata)
        self.evidence = []
        self.code = None
        self.source_work_event_id = None
        self.verification_status = "not_provided"
        self.finished = False
        context.send(
            "span_start",
            span_id=self.id,
            parent_span_id=current_span.get() or context.root_span_id,
            kind=kind,
            phase=self.phase,
            tool_name=tool_name,
            model_name=model_name,
            metadata=self.metadata,
            event_type=start_event,
        )

    def result(self, value):
        if not isinstance(value, dict):
            return
        self.evidence = privacy.result_evidence(value)
        self.code = privacy.error_code(result=value)
        if value.get("error") and self.code is None:
            self.code = "TOOL_ERROR"
        flag = value.get("verified")
        self.verification_status = (
            "passed" if flag is True else "failed" if flag is False else "not_provided"
        )
        self.metadata.update(verified=flag) if isinstance(flag, bool) else None

    def finish(self, error=None, *, event_type=None, status=None):
        if self.finished:
            return
        self.finished = True
        if error is not None:
            self.code = (
                "CANCELLED"
                if isinstance(error, asyncio.CancelledError)
                or type(error).__name__ == "TaskCancelled"
                else privacy.error_code(error)
            )
            if self.code == "TOOL_ERROR" and self.kind in {"model", "planning"}:
                self.code = "MODEL_ERROR" if self.kind == "model" else "PLANNING_ERROR"
        status = status or (
            "cancelled" if self.code == "CANCELLED" else "failed" if self.code else "completed"
        )
        end_event = event_type or self.end_event
        if self.code and end_event in {
            "TOOL_COMPLETED",
            "OPERATION_COMPLETED",
            "MODEL_REQUEST_COMPLETED",
            "VERIFICATION_PASSED",
        }:
            end_event = {
                "TOOL_COMPLETED": "TOOL_FAILED",
                "OPERATION_COMPLETED": "OPERATION_FAILED",
                "MODEL_REQUEST_COMPLETED": "MODEL_REQUEST_FAILED",
                "VERIFICATION_PASSED": "VERIFICATION_FAILED",
            }[end_event]
        self.context.send(
            "span_finish",
            span_id=self.id,
            kind=self.kind,
            phase=self.phase,
            status=status,
            duration_ms=(time.perf_counter() - self.started) * 1000,
            error_code=self.code,
            verification_status=self.verification_status,
            metadata=self.metadata,
            evidence=self.evidence,
            event_type=end_event,
            source_work_event_id=self.source_work_event_id,
        )


@contextmanager
def span_scope(kind, start_event, end_event, **options):
    context = current_trace.get()
    span = None
    token = None
    active_token = None
    try:
        if context and not context.finished:
            if kind == "operation" and context.recovery:
                # Retry waiting ends when the next actual attempt starts; its
                # execution and final model reply are not recovery wait time.
                context.recovery.finish()
                context.recovery = None
            span = Span(context, kind, start_event, end_event, **options)
            token = current_span.set(span.id)
            active_token = active_span.set(span)
    except Exception:
        if context:
            context.incomplete = True
    try:
        yield span or NullSpan()
    except BaseException as error:
        if span:
            try:
                span.finish(error)
            except Exception:
                context.incomplete = True
        raise
    else:
        if span:
            try:
                span.finish()
            except Exception:
                context.incomplete = True
    finally:
        if token is not None:
            current_span.reset(token)
        if active_token is not None:
            active_span.reset(active_token)


class TraceContext:
    def __init__(
        self, recorder, task_id, conversation_id=None, *, trace_id=None, work_session_id=None
    ):
        self.recorder, self.task_id = recorder, task_id
        self.trace_id = trace_id or str(uuid4())
        self.conversation_id, self.work_session_id = conversation_id, work_session_id
        self.root_span_id = str(uuid4())
        self.started = time.perf_counter()
        self.base_duration = 0.0
        self.finished = self.executing = self.incomplete = False
        self.terminal = None
        self.permission = self.recovery = None
        self.queue_span = None

    def send(self, command, **data):
        try:
            accepted = self.recorder.store.enqueue(
                command,
                {
                    "trace_id": self.trace_id,
                    "task_id": self.task_id,
                    "conversation_id": self.conversation_id,
                    "work_session_id": self.work_session_id,
                    "created_at": utc_now(),
                    "incomplete": self.incomplete,
                    **data,
                },
            )
            if not accepted:
                self.incomplete = True
        except Exception:
            self.incomplete = True

    def bind(self, conversation_id, session_id):
        conversation_id, session_id = (
            privacy.uuid_value(conversation_id),
            privacy.uuid_value(session_id),
        )
        if not conversation_id or not session_id or self.work_session_id not in {None, session_id}:
            return
        self.conversation_id, self.work_session_id = conversation_id, session_id
        self.recorder.bindings[session_id] = self.task_id
        self.recorder.bindings.move_to_end(session_id)
        while len(self.recorder.bindings) > 1024:
            self.recorder.bindings.popitem(last=False)
        self.send("bind")

    def finish(self, error=None):
        if self.finished:
            return
        status, source = self.terminal or ("completed", None)
        code = None
        if error is not None:
            status = (
                "cancelled"
                if isinstance(error, asyncio.CancelledError)
                or type(error).__name__ == "TaskCancelled"
                else "failed"
            )
            if self.terminal and self.terminal[0] == "interrupted":
                status = "interrupted"
            code = (
                "CANCELLED"
                if status == "cancelled"
                else "TIMEOUT"
                if isinstance(error, TimeoutError)
                else "TASK_ERROR"
            )
        if status == "failed" and code is None:
            code = "VERIFICATION_FAILED"
        for pending in (self.permission, self.recovery, self.queue_span):
            if pending:
                pending.finish(error, status=status if error else "interrupted")
        elapsed = self.base_duration + (time.perf_counter() - self.started) * 1000
        self.send(
            "span_finish",
            span_id=self.root_span_id,
            kind="task",
            phase="task",
            status=status,
            duration_ms=elapsed,
            error_code=code,
        )
        self.send(
            "finish",
            span_id=self.root_span_id,
            phase="task",
            status=status,
            duration_ms=elapsed,
            error_code=code,
            source_work_event_id=source,
            event_type={
                "completed": "TASK_COMPLETED",
                "failed": "TASK_FAILED",
                "cancelled": "TASK_CANCELLED",
                "interrupted": "TASK_INTERRUPTED",
            }[status],
        )
        self.finished = True


def planning_task_id():
    context = current_trace.get()
    return context.task_id if context else None
