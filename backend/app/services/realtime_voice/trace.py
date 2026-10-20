"""Voice metadata in the original bounded, privacy-filtered Trace store."""

import time


def record(session, event: str, **values):
    turn = session.turn
    if turn is None or turn.trace is None or turn.trace.finished:
        return
    turn.trace.send(
        "event",
        event_type=event,
        phase="voice",
        status="running",
        metadata={
            "voice_session_id": session.id,
            "turn_id": turn.id,
            "generation_id": turn.generation_id,
            "provider_id": session.options.tts_provider,
            **values,
        },
        duration_ms=(time.perf_counter() - turn.trace.started) * 1000,
    )
