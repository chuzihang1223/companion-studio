"""Actual orchestration/cancellation and crash recovery, synthetic speech only."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from unified_skill_fixture import SyntheticSpeech, unified_host
from app.database.session import SessionLocal
from app.schemas.chat import VoiceContext
from app.services.evaluation.voice_cases import signal
from app.services.realtime_voice.contracts import Frame, Scope, StartRequest
from app.services.voice.continuous_pipeline import RealtimeVoicePipeline


def test_E2E_005_final_agent_tool_tts_manual_interrupt_rejects_late_generation(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            callbacks = []

            class Speech(SyntheticSpeech):
                async def synthesize_live(self, queue, speed, on_pcm):
                    callbacks.append(on_pcm)
                    return await super().synthesize_live(queue, speed, on_pcm)

            manager = h.app.state.realtime_voice
            manager.provider_factory = Speech
            options = StartRequest(
                owner_id=uuid4(), conversation_id=h.owner, character_id="assistant"
            )
            identity = await manager.start(options)
            identity = await manager.begin(
                Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
            )
            scope = Scope.model_validate({key: identity[key] for key in Scope.model_fields})
            for sequence in range(3):
                manager.frame(Frame(**scope.model_dump(), **signal(sequence), timestamp=0))
            final = await manager.finalize(scope)
            manager.claim(scope, h.owner, "assistant")
            plan, reply = await h.agent(
                final["text"], manager.owned(scope, turn=True).turn.model_routes
            )
            assert len(reply["tools"]) == 1 and plan["conversation_id"] == h.owner
            emitted = asyncio.Event()
            waiting = asyncio.Event()
            events = []

            async def source():
                yield "已经读到了项目文件，这段是合成语音测试，现在检查手动停止能否及时生效。"
                await waiting.wait()
                yield "不应在停止后继续播放。"

            async def complete(text):
                return {"content": text}

            payload = SimpleNamespace(
                voice_context=VoiceContext.model_validate(
                    {key: value for key, value in scope.model_dump().items() if key != "owner_id"}
                ),
                renderer_id=options.owner_id,
                voice_enabled=True,
                expression="natural",
                message=final["text"],
                speed_factor=1,
            )

            async def consume():
                async for event, data in RealtimeVoicePipeline(
                    h.app, payload, reply["message_id"], SessionLocal
                ).events(source(), [], complete):
                    events.append((event, data))
                    if event == "audio_pcm":
                        emitted.set()

            task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(emitted.wait(), 5)
                await manager.interrupt(scope)
                await asyncio.wait_for(task, 5)
                before = len([event for event, _ in events if event == "audio_pcm"])
                # `done` terminates the SSE envelope, not an audio-playback claim.
                assert before > 0 and not any(event == "voice_saved" for event, _ in events)
                assert not h.app.state.live_voice_sessions
                replacement = await manager.begin(
                    scope.model_copy(update={"turn_id": None, "generation_id": None})
                )
                assert replacement["turn_id"] != str(scope.turn_id)
                with pytest.raises(asyncio.CancelledError):
                    await callbacks[0](b"\x00\x00" * 400, 16000, 1)
                assert len([event for event, _ in events if event == "audio_pcm"]) == before
                with pytest.raises(LookupError):
                    manager.owned(scope, turn=True)
            finally:
                waiting.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_E2E_008_owned_hard_exit_reopen_migration_and_sqlite_integrity(tmp_path):
    from test_unified_skill_routing import (
        test_INT_014_actual_process_exit_recovers_scheduler_workflow_trace_and_budget,
    )

    test_INT_014_actual_process_exit_recovers_scheduler_workflow_trace_and_budget(tmp_path)
    profile = tmp_path / "hard-exit/profile"
    databases = list(profile.glob("*.db"))
    assert databases
    for path in databases:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as database:
            assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    evidence = json.loads((tmp_path / "hard-exit/result.json").read_text(encoding="utf-8"))
    assert evidence["automatic_replay_count"] == 0
    assert evidence["observed_dispatches"] == 1 and evidence["trace_rows"] > 0
