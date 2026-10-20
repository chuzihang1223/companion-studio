"""Silent output must terminate without inventing a playback event or model call."""

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.realtime_voice.contracts import Scope, StartRequest, TurnState
from app.services.realtime_voice.sessions import VoiceSessionManager
from app.services.trace.recorder import TraceRecorder
from app.services.voice.continuous_pipeline import RealtimeVoicePipeline


@pytest.mark.parametrize(
    "text,tts_failure,expected",
    [
        ('```python\nprint("private code")\n```', False, "COMPLETED"),
        ('{"arguments":["private data"]}', False, "COMPLETED"),
        ("https://example.invalid/private", False, "COMPLETED"),
        ("Valid prose but the provider returns no audio.", False, "FAILED"),
        ("Valid prose with a failing provider.", True, "FAILED"),
    ],
)
def test_silent_generation_finishes_its_trace_and_allows_another_turn(text, tts_failure, expected):
    async def run():
        rows = []

        class Store:
            def enqueue(self, command, data):
                rows.append((command, data))
                return True

        class Local:
            name = "synthetic-local"
            config = SimpleNamespace(speed=1)

            async def synthesize_live(self, queue, speed, on_pcm):
                while await queue.get() is not None:
                    pass
                if tts_failure:
                    raise RuntimeError("Synthetic TTS error")
                return [], 16000, "synthetic"

            async def cancel(self):
                pass

            async def close(self):
                pass

        app = SimpleNamespace(state=SimpleNamespace(trace_recorder=TraceRecorder(Store())))
        manager = app.state.realtime_voice = VoiceSessionManager(app, lambda **_: Local())
        options = StartRequest(owner_id=uuid4(), conversation_id=uuid4(), character_id="assistant")
        session_id = (await manager.start(options))["voice_session_id"]
        result = await manager.begin(Scope(owner_id=options.owner_id, voice_session_id=session_id))
        scope = Scope.model_validate({key: result[key] for key in Scope.model_fields})
        session = manager.owned(scope, turn=True)
        session.turn.transition(TurnState.FINAL_TRANSCRIPT)
        manager.claim(scope, str(options.conversation_id), "assistant")
        payload = SimpleNamespace(
            voice_context=scope.model_copy(),
            renderer_id=options.owner_id,
            voice_enabled=True,
            expression="natural",
            message="synthetic input",
            speed_factor=1,
        )
        # The public context omits owner_id; ownership remains in renderer_id.
        payload.voice_context = SimpleNamespace(
            model_dump=lambda **_: {
                key: value for key, value in scope.model_dump().items() if key != "owner_id"
            }
        )
        app.state.voice_client = Local()

        @contextmanager
        def factory():
            yield SimpleNamespace(get=lambda *_: None)

        async def deltas():
            for character in text:
                yield character

        async def complete(visible):
            assert visible == text
            return {"content": visible}

        events = [
            (event, data)
            async for event, data in RealtimeVoicePipeline(
                app, payload, str(uuid4()), factory
            ).events(deltas(), [], complete)
        ]
        assert session.turn.state.value == expected and session.turn.trace.finished
        assert session.state.value == ("IDLE" if expected == "COMPLETED" else "ERROR")
        assert (
            next(data for event, data in events if event == "voice_state")["turn_state"] == expected
        )
        assert all(data.get("event_type") != "AUDIO_PLAYBACK_COMPLETED" for _, data in rows)
        metadata = [data.get("metadata", {}) for _, data in rows]
        assert any(item.get("actual_playback") is False for item in metadata)
        await manager.begin(scope.model_copy(update={"turn_id": None, "generation_id": None}))
        await manager.close()

    asyncio.run(run())
