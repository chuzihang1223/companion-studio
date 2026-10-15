"""Recovery events reuse Trace's bounded writer and WorkState's scoped SSE."""

from app.services.trace.scope import current_trace


def emit(
    event_type,
    *,
    step_id=None,
    checkpoint_id=None,
    recovery_id=None,
    strategy=None,
    code=None,
    evidence=None,
):
    context = current_trace.get()
    if context:
        context.send(
            "event",
            event_type=event_type,
            phase="recovery",
            error_code=code,
            metadata={
                "step_id": step_id,
                "checkpoint_id": checkpoint_id,
                "recovery_id": recovery_id,
                "strategy": strategy,
            },
            evidence=[{"name": k, "value": v} for k, v in (evidence or {}).items()],
        )
