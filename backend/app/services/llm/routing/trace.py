"""Allowlisted routing evidence in the original Trace; never inputs or endpoints."""

from app.services.trace.scope import current_trace, current_span


def emit(event, decision, entry):
    trace = current_trace.get()
    if trace is None or trace.finished:
        return
    trace.send(
        "event",
        event_type=event,
        phase="model",
        span_id=current_span.get() or trace.root_span_id,
        metadata={
            "routing_model_id": entry.record.model_id,
            "routing_config_version": entry.version,
            "routing_mode": decision.mode,
            "routing_task": decision.task,
            "routing_fallback": event == "MODEL_ROUTING_FALLBACK",
            "routing_reason": "TRANSIENT_SAME_TARGET"
            if event == "MODEL_ROUTING_FALLBACK"
            else decision.reason,
        },
    )
