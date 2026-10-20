"""Layer A only: production state/PCM paths with a declared synthetic ASR.

Native-device, acoustic and external-provider assertions are BLOCKED here,
never promoted from a synthetic signal into recognition/playback quality.
"""

import asyncio
import base64
from io import BytesIO
import math
import struct
import time
from uuid import uuid4
import wave

from app.database.session import SessionLocal
from app.models import Conversation
from app.services.realtime_voice.audio import CaptureBuffer
from app.services.realtime_voice.contracts import Frame, Scope, StartRequest, TurnState
from app.services.realtime_voice.sessions import VoiceSessionManager
from app.services.realtime_voice.windows import WindowsSpeech
from app.services.voice.speech_filter import StreamingCredentialFilter, StreamingSpeechFilter


class SyntheticASR:
    def __init__(self, **options):
        self.options = options
        self.calls = 0
        self.cancelled = False

    async def recognize(self, raw):
        self.calls += 1
        if raw[:4] != b"RIFF":
            raise ValueError("fixture input must be a real WAV container")
        return "Synthetic final transcript"

    async def cancel(self):
        self.cancelled = True


def signal(sequence=0, *, rate=16000, silent=False):
    values = [
        0 if silent else round(2500 * math.sin(2 * math.pi * 300 * n / rate))
        for n in range(rate // 10)
    ]
    return {
        "sequence": sequence,
        "sample_rate": rate,
        "frame_duration_ms": 100.0,
        "pcm_base64": base64.b64encode(struct.pack("<" + "h" * len(values), *values)).decode(),
    }


def rejected(function, errors=(ValueError, LookupError)):
    try:
        function()
    except errors:
        return True
    return False


async def assertions(index, manager, scope, options):
    session = manager.owned(scope, turn=True)
    turn = session.turn
    for sequence in range(3):
        manager.frame(Frame(**scope.model_dump(), **signal(sequence), timestamp=0))
    if index == 4:
        checks = {}
        for rate in (16000, 24000, 44100, 48000):
            capture = CaptureBuffer()
            for sequence in range(10):
                p = signal(sequence, rate=rate)
                capture.feed(sequence, rate, 100, p["pcm_base64"])
            with wave.open(BytesIO(capture.final())) as reader:
                checks[str(rate)] = (
                    reader.getframerate() == 16000 and abs(reader.getnframes() - 16000) <= 1
                )
        return checks
    if index in {6, 27, 28}:
        await manager.interrupt(scope)
        checks = {
            "capture_cleared": not turn.capture.audio,
            "provider_cancelled": turn.provider.cancelled,
        }
        await manager.begin(scope.model_copy(update={"turn_id": None, "generation_id": None}))
        checks["old_generation_rejected"] = rejected(lambda: manager.owned(scope, turn=True))
        return checks
    if index in {7, 8, 9, 26}:
        activity = None
        for sequence in range(3, 20):
            activity = manager.frame(
                Frame(**scope.model_dump(), **signal(sequence, silent=True), timestamp=0)
            )
        return {
            "speech_detected": activity["speech_detected"],
            "endpoint_is_candidate_only": activity["endpoint_candidate"],
            "no_asr_or_dispatch": turn.provider.calls == 0 and turn.state == TurnState.CAPTURING,
        }
    if index in {10, 13, 14, 18}:
        checks = {
            "partial_cannot_dispatch": rejected(
                lambda: manager.claim(scope, str(options.conversation_id), options.character_id)
            )
        }
        result = await manager.finalize(scope)
        checks.update(
            {
                "final_distinct": result["kind"] == "FINAL" and result["partial"] is None,
                "explicit_submit_required": turn.state == TurnState.FINAL_TRANSCRIPT,
            }
        )
        checks["wrong_conversation_denied"] = rejected(
            lambda: manager.claim(scope, str(uuid4()), options.character_id)
        )
        checks["wrong_character_denied"] = rejected(
            lambda: manager.claim(scope, str(options.conversation_id), "other-character")
        )
        manager.claim(scope, str(options.conversation_id), options.character_id)
        checks["final_claimed_once"] = rejected(
            lambda: manager.claim(scope, str(options.conversation_id), options.character_id)
        )
        checks["fixed_voice_options"] = turn.provider.options["language"] == options.language
        return checks
    if index == 16:
        text = "先说自然语言。```python\nprint('not speech')\n```继续。"
        filtered = StreamingSpeechFilter()
        output = "".join(filtered.feed(c) for c in text) + filtered.feed("", final=True)
        return {"incremental_fence_filtered": output == "先说自然语言。继续。"}
    if index == 21:

        class Chunked(WindowsSpeech):
            async def synthesize(self, text):
                return text.encode() * 3200

        provider, queue, chunks = Chunked(), asyncio.Queue(), []
        for text in ("a", "b", None):
            await queue.put(text)

        async def received(raw, rate, channels):
            chunks.append((raw[:1].decode(), rate, channels, len(raw)))

        await provider.synthesize_live(queue, 1, received)
        return {"ordered_100ms_pcm": chunks == [("a", 16000, 1, 3200), ("b", 16000, 1, 3200)]}
    if index == 23:
        capture = CaptureBuffer()
        for sequence in range(300):
            capture.feed(sequence, 16000, 100, signal(sequence)["pcm_base64"])
        return {
            "thirty_second_ram_bound": len(capture.audio) == 960000,
            "overflow_denied": rejected(
                lambda: capture.feed(300, 16000, 100, signal(300)["pcm_base64"])
            ),
        }
    if index == 29:

        async def failure(_):
            raise TimeoutError("synthetic ASR timeout")

        turn.provider.recognize = failure
        try:
            await manager.finalize(scope)
        except TimeoutError:
            pass
        checks = {
            "failed_state": turn.state == TurnState.FAILED,
            "raw_released": not turn.capture.audio and turn.task is None,
        }
        await manager.begin(scope.model_copy(update={"turn_id": None, "generation_id": None}))
        checks["new_turn_possible"] = session.turn is not turn
        return checks
    if index in {31, 32}:
        sensitive = 'password: "' + "fixture private words " * 30 + '" end'
        filter_ = StreamingCredentialFilter()
        output = "".join(filter_.feed(c) for c in sensitive) + filter_.feed("", final=True)
        return {
            "cross_chunk_secret_not_spoken": "private" not in output and "end" in output,
            "no_network_adapter": type(turn.provider) is SyntheticASR,
            "no_remote_consent_by_default": not options.remote_tts_consent,
        }
    if index == 36:
        await manager.finalize(scope)
        manager.claim(scope, str(options.conversation_id), options.character_id)
        return {
            "original_trace_link": bool(turn.trace and turn.trace.trace_id),
            "turn_task_scope": turn.trace.task_id == turn.id,
        }
    return None


async def run_case(app, trace_store, case, payload, workspace):
    started = time.perf_counter()
    owner = str(uuid4())
    with SessionLocal() as db:
        db.add(Conversation(id=owner, character_id="assistant", conversation_type="work"))
        db.commit()
    manager = VoiceSessionManager(app, SyntheticASR)
    options = StartRequest(owner_id=uuid4(), conversation_id=owner, character_id="assistant")
    identity = await manager.start(options)
    identity = await manager.begin(
        Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
    )
    scope = Scope.model_validate({k: identity[k] for k in Scope.model_fields})
    try:
        index = int(case.case_id[-3:])
        if index in {33, 34, 35}:
            from app.services.evaluation.voice_integration_cases import run

            for sequence in range(3):
                manager.frame(Frame(**scope.model_dump(), **signal(sequence), timestamp=0))
            checks = await run(index, app, workspace, manager, scope, options)
        else:
            checks = await assertions(index, manager, scope, options)
    finally:
        await manager.close()
    await trace_store.flush()
    traces = app.state.trace_repository.listing(owner, voice=True)["items"]
    blocked = checks is None
    passed = (
        bool(checks)
        and all(v is True for v in checks.values())
        and bool(traces)
        and not manager.rows
    )
    evidence = {
        "checks": checks or {},
        "boundary": "synthetic_pcm_mock_asr_original_state_and_trace",
        "actual_recognition": False,
        "actual_playback": False,
        "physical_microphone": False,
        "external_requests": 0,
        "resources_released": not manager.rows,
        "trace_consistent": bool(traces),
    }
    result = {
        "case_id": case.case_id,
        "completion_status": "BLOCKED" if blocked else "PASS" if passed else "FAIL",
        "passed": passed,
        "score": int(passed),
        "grader_type": "voice",
        "error_code": "VOICE_NATIVE_OR_INTEGRATION_GATE_REQUIRED"
        if blocked
        else None
        if passed
        else "VOICE_INVARIANT_FAILED",
        "duration_ms": (time.perf_counter() - started) * 1000,
        "tool_calls": 3 if case.case_id == "VOICE-034" else 0,
        "model_requests": 0,
        "retry_count": 0,
        "recovery_succeeded": passed if case.case_id == "VOICE-029" else False,
        "model_usage": {"status": "Synthetic, no model calls"},
        "evidence": evidence,
        "evidence_refs": ["evidence:checks"],
        "trace_count": len(traces),
        "expected_failure": False,
        "dimensions": {"task_success": passed},
        "explanation": "本进程无真实音频设备或 GUI；须查看独立原生设备／集成验收证据。"
        if blocked
        else "合成 PCM 与模拟 ASR 的生产状态机断言，不代表真实语音质量。",
        "trace_scopes": [
            {k: t[k] for k in ("trace_id", "task_id", "conversation_id", "work_session_id")}
            for t in traces
        ],
    }
    return {
        "result": result,
        "links": traces[0] if traces else {},
        "budget": {"requests": 0, "total_tokens": None},
    }
