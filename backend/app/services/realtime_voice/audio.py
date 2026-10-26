"""Bounded PCM16 capture, real stateful resampling and conservative activity hints.

This is an energy detector, not a speech/content classifier. Endpoint hints never
dispatch tools. Hands-free remains disabled until acoustic echo tests pass.
"""

import audioop
import base64
from dataclasses import dataclass
from io import BytesIO
import math
import wave

RATES = {16000, 24000, 44100, 48000}
MAX_TURN_MS = 30000
MAX_FRAME_MS = 200


def pcm_bytes(value: str, rate: int, duration_ms: float) -> bytes:
    if rate not in RATES or not math.isfinite(duration_ms):
        raise ValueError("AUDIO_FORMAT_INVALID")
    if not 10 <= duration_ms <= MAX_FRAME_MS or len(value) > 26000:
        raise ValueError("AUDIO_FORMAT_INVALID")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("AUDIO_FORMAT_INVALID") from exc
    if not raw or len(raw) % 2 or raw.startswith((b"RIFF", b"OggS")):
        raise ValueError("AUDIO_FORMAT_INVALID")
    if abs(len(raw) / 2 / rate * 1000 - duration_ms) > 1:
        raise ValueError("AUDIO_FORMAT_INVALID")
    return raw


def wav_bytes(raw: bytes, rate: int = 16000) -> bytes:
    if not raw or len(raw) % 2 or rate not in RATES:
        raise ValueError("AUDIO_FORMAT_INVALID")
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(raw)
    return output.getvalue()


@dataclass(frozen=True)
class ActivityConfig:
    speech_start_threshold: int = 550
    speech_end_silence_ms: int = 1400
    minimum_speech_ms: int = 200
    maximum_utterance_ms: int = MAX_TURN_MS
    pre_roll_ms: int = 200


class ActivityDetector:
    def __init__(self, config: ActivityConfig | None = None):
        self.config = config or ActivityConfig()
        self.speech_ms = self.silence_ms = self.total_ms = 0.0
        self.detected = False

    def feed(self, pcm: bytes, rate: int) -> dict:
        ms = len(pcm) / (rate * 2) * 1000
        self.total_ms += ms
        level = audioop.rms(pcm, 2)
        loud = level >= self.config.speech_start_threshold
        if loud:
            self.speech_ms += ms
            self.silence_ms = 0
        else:
            self.silence_ms += ms
            if not self.detected and self.silence_ms >= self.config.pre_roll_ms:
                self.speech_ms = 0
        start = not self.detected and self.speech_ms >= self.config.minimum_speech_ms
        self.detected |= start
        return {
            "speech_started": start,
            "speech_detected": self.detected,
            "endpoint_candidate": self.detected
            and self.silence_ms >= self.config.speech_end_silence_ms,
            "limit_reached": self.total_ms >= self.config.maximum_utterance_ms,
            "level": level,
            "duration_ms": round(self.total_ms),
        }


class CaptureBuffer:
    """One turn has one sample format and at most 30 seconds of RAM-only audio.

    audioop.ratecv uses the locked audioop-lts C resampler on Python 3.13. Its filter state
    continues across packets; changing a WAV rate label is never resampling.
    Keep the complete manual utterance including pre-roll/pauses for batch ASR.
    """

    def __init__(self):
        self.audio = bytearray()
        self.rate = None
        self.sequence = 0
        self.resampler = None
        self.activity = ActivityDetector()
        self.duration_ms = 0.0
        self.dropped_frames = 0

    def feed(self, sequence: int, rate: int, duration_ms: float, value: str) -> dict:
        if sequence != self.sequence:
            raise ValueError("STREAM_OUT_OF_ORDER")
        if self.rate is not None and self.rate != rate:
            raise ValueError("AUDIO_FORMAT_INVALID")
        raw = pcm_bytes(value, rate, duration_ms)
        if self.duration_ms + duration_ms > MAX_TURN_MS + 1:
            self.dropped_frames += 1
            raise ValueError("CAPTURE_LIMIT_REACHED")
        converted, resampler = audioop.ratecv(raw, 2, 1, rate, 16000, self.resampler)
        if len(self.audio) + len(converted) > 960000:
            raise ValueError("CAPTURE_LIMIT_REACHED")
        self.resampler, self.rate = resampler, rate
        self.audio.extend(converted)
        self.duration_ms += duration_ms
        self.sequence += 1
        return self.activity.feed(raw, rate)

    def final(self) -> bytes:
        if not self.activity.detected or self.duration_ms < 200:
            raise ValueError("NO_VALID_SPEECH")
        return wav_bytes(bytes(self.audio))

    def clear(self):
        # No recording files, file paths or cache copies are created by capture.
        self.audio.clear()
        self.resampler = None
