"""Structured Trace projection only; no raw server descriptions or tool output."""

from app.services.trace.scope import current_trace, current_span


def emit(event, *, server_id=None, tool_id=None, skill_id=None, code=None, **metadata):
    trace = current_trace.get()
    if trace and not trace.finished:
        trace.send(
            "event",
            event_type=event,
            phase="tool",
            span_id=current_span.get() or trace.root_span_id,
            error_code=code,
            metadata={
                **metadata,
                "server_id": server_id,
                "external_tool_id": tool_id,
                "skill_id": skill_id,
            },
        )
