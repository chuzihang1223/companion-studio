"""Only fixed event names, identifiers and counts enter the original trace store."""

from app.services.trace.scope import current_span, current_trace


def event(name, **metadata):
    context = current_trace.get()
    if context and not context.finished:
        context.send(
            "event",
            event_type=name,
            span_id=current_span.get() or context.root_span_id,
            phase="knowledge",
            status="running",
            metadata=metadata,
        )
