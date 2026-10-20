"""Synthetic PCM/providers only. Local engine/audio-device acceptance is separate."""

import asyncio
import base64
from io import BytesIO
import math
import struct
from types import SimpleNamespace
from uuid import uuid4
import wave

import pytest

from app.services.realtime_voice.audio import CaptureBuffer, pcm_bytes
from app.services.realtime_voice.contracts import Frame, Scope, StartRequest, TurnState
from app.services.realtime_voice.sessions import VoiceSessionManager


def packet(rate=16000, ms=100, *, sequence=0, silence=False):
    samples = [
        0 if silence else round(2500 * math.sin(2 * math.pi * 300 * n / rate))
        for n in range(round(rate * ms / 1000))
    ]
    return dict(
        sequence=sequence,
        sample_rate=rate,
        frame_duration_ms=float(ms),
        pcm_base64=base64.b64encode(struct.pack("<" + "h" * len(samples), *samples)).decode(),
    )


@pytest.mark.parametrize("rate", [16000, 24000, 44100, 48000])
def test_real_resampling_preserves_duration_and_frequency(rate):
    capture = CaptureBuffer()
    for sequence in range(10):
        p = packet(rate, sequence=sequence)
        capture.feed(sequence, rate, p["frame_duration_ms"], p["pcm_base64"])
    with wave.open(BytesIO(capture.final())) as reader:
        assert reader.getframerate() == 16000
        assert reader.getnchannels() == 1
        assert abs(reader.getnframes() - 16000) <= 1
        raw = reader.readframes(reader.getnframes())
    values = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    crossings = sum(a <= 0 < b for a, b in zip(values, values[1:]))
    assert abs(crossings - 300) <= 1
    capture.clear()
    assert not capture.audio and capture.resampler is None


def test_invalid_containers_alignment_rate_duration_and_sequence():
    for raw in (b"RIFF" * 1600, b"OggS" * 1600, b"1", b""):
        with pytest.raises(ValueError, match="AUDIO_FORMAT_INVALID"):
            pcm_bytes(base64.b64encode(raw).decode(), 16000, 100)
    capture = CaptureBuffer()
    p = packet()
    with pytest.raises(ValueError, match="STREAM_OUT_OF_ORDER"):
        capture.feed(1, 16000, 100, p["pcm_base64"])
    with pytest.raises(ValueError):
        pcm_bytes(p["pcm_base64"], 24000, 100)
    with pytest.raises(ValueError):
        pcm_bytes(p["pcm_base64"], 16000, float("nan"))


def test_capture_limit_and_silence_do_not_dispatch():
    capture = CaptureBuffer()
    for sequence in range(300):
        p = packet(sequence=sequence, silence=True)
        capture.feed(sequence, 16000, 100, p["pcm_base64"])
    assert len(capture.audio) == 960000
    p = packet(sequence=300)
    with pytest.raises(ValueError, match="CAPTURE_LIMIT_REACHED"):
        capture.feed(300, 16000, 100, p["pcm_base64"])
    with pytest.raises(ValueError, match="NO_VALID_SPEECH"):
        capture.final()


class MockASR:
    def __init__(self, **_):
        self.cancelled = False

    async def recognize(self, raw):
        assert raw[:4] == b"RIFF"
        return "Synthetic final transcript"

    async def cancel(self):
        self.cancelled = True


def setup(factory=MockASR):
    manager = VoiceSessionManager(SimpleNamespace(state=SimpleNamespace()), factory)
    options = StartRequest(owner_id=uuid4(), conversation_id=uuid4(), character_id="assistant")
    return manager, options


async def begin(manager, options):
    value = await manager.start(options)
    scope = Scope(owner_id=options.owner_id, voice_session_id=value["voice_session_id"])
    value = await manager.begin(scope)
    return Scope.model_validate({k: value[k] for k in Scope.model_fields})


def test_final_once_owner_generation_and_capture_lifecycle():
    async def scenario():
        manager, options = setup()
        scope = await begin(manager, options)
        for sequence in range(3):
            reply = manager.frame(
                Frame(**scope.model_dump(), **packet(sequence=sequence), timestamp=0)
            )
        assert reply["state"] == "SPEECH_DETECTED"
        result = await manager.finalize(scope)
        assert result["kind"] == "FINAL" and result["partial"] is None
        assert result["state"] == "PAUSED"  # no recording/speaking claim
        session = manager.claim(scope, str(options.conversation_id), "assistant")
        assert session.turn.state == TurnState.GENERATING
        with pytest.raises(ValueError, match="TURN_STATE_CONFLICT"):
            manager.claim(scope, str(options.conversation_id), "assistant")
        with pytest.raises(LookupError):
            manager.owned(scope.model_copy(update={"owner_id": uuid4()}), turn=True)
        await manager.interrupt(scope)
        with pytest.raises(LookupError):
            manager.owned(scope, turn=True)
        new = await manager.begin(scope.model_copy(update={"turn_id": None, "generation_id": None}))
        assert new["turn_id"] != str(scope.turn_id)
        with pytest.raises(LookupError):
            manager.owned(scope, turn=True)
        await manager.close()
        assert not manager.rows

    asyncio.run(scenario())


def test_cancel_actual_pending_asr_task_and_discard_stale_result():
    class SlowASR(MockASR):
        async def recognize(self, _raw):
            await asyncio.sleep(60)
            return "stale"

    async def scenario():
        manager, options = setup(SlowASR)
        scope = await begin(manager, options)
        for sequence in range(3):
            manager.frame(Frame(**scope.model_dump(), **packet(sequence=sequence), timestamp=0))
        pending = asyncio.create_task(manager.finalize(scope))
        await asyncio.sleep(0)
        session = manager.owned(scope)
        await manager.interrupt(scope)
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert session.turn.provider.cancelled
        assert not session.turn.capture.audio and not session.turn.transcript
        await manager.close()

    asyncio.run(scenario())
