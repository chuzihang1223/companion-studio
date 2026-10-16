"""Only fixed workflow identity/status/count metadata enters the existing Trace projection."""

from app.services.trace.scope import current_trace, current_span
from contextlib import contextmanager
import asyncio


def emit(kind, run, node=None, **metadata):
    trace = current_trace.get()
    if trace and not trace.finished:
        trace.send(
            "event",
            event_type=kind,
            phase="workflow",
            span_id=current_span.get(),
            metadata={
                "workflow_run_id": run["run_id"],
                "workflow_version_id": run["version_id"],
                "node_execution_id": node.get("node_execution_id") if node else None,
                **metadata,
            },
        )


@contextmanager
def execution_trace(scope, coordinator, run):
    with scope:
        try:
            yield
        except BaseException as error:
            trace = current_trace.get()
            if trace and coordinator.closing:
                trace.terminal = ("interrupted", None)
            emit(
                "WORKFLOW_CANCELLED"
                if isinstance(error, asyncio.CancelledError) and not coordinator.closing
                else "WORKFLOW_FAILED",
                run,
            )
            raise
