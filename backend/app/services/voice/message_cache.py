"""Persist complete per-message WAVs without adding audio blobs to SQLite."""

from array import array
import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import wave

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Message, MessageVoice


PROFILE_VERSION = "studio-indextts-v1"
PLAN_VERSION = "speech-v82-continuous"


def _samples(wav_data: bytes) -> tuple[int, array]:
    with wave.open(BytesIO(wav_data), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise ValueError("Voice worker returned unsupported WAV format")
        rate = reader.getframerate()
        pcm = array("h")
        pcm.frombytes(reader.readframes(reader.getnframes()))
    if rate <= 0 or not pcm:
        raise ValueError("Voice worker returned empty audio")
    return rate, pcm


def _resample(samples: array, source_rate: int, target_rate: int) -> array:
    if source_rate == target_rate:
        return samples
    count = round(len(samples) * target_rate / source_rate)
    result = array("h")
    for index in range(count):
        position = index * source_rate / target_rate
        left = min(int(position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        value = round(samples[left] * (1 - (position - left)) + samples[right] * (position - left))
        result.append(max(-32768, min(32767, value)))
    return result


def audio_file(row: MessageVoice) -> Path | None:
    legacy = row.audio_path == f"audio/{row.message_id}.wav"
    fish_cache = bool(row.provider == "fish" and row.audio_path and
                      re.fullmatch(r"voice_cache/fish/[0-9a-f]{64}\.wav", row.audio_path))
    if not (legacy or fish_cache):
        return None
    root = settings.data_root.resolve()
    target = (root / row.audio_path).resolve()
    return target if target.is_relative_to(root) and target.is_file() else None


def index_audio_file(row: MessageVoice) -> Path | None:
    """Return supported cached voices; legacy ONNX files are not promoted."""
    return audio_file(row) if row.engine == "indextts-2.5-expressive" or (row.engine or "").startswith("fish-") else None


def save_message_audio(
    db: Session, message: Message, chunks: list[tuple[str, int, int]], engine_name: str,
    expression: str = "natural", speed_factor: float = 1.0, reference_id: str | None = None,
) -> MessageVoice:
    """Each chunk is (base64 WAV, pause_before_ms, pause_after_ms)."""
    if not chunks:
        raise ValueError("No audio chunks to save")
    parsed = [_samples(base64.b64decode(encoded, validate=True)) for encoded, _, _ in chunks]
    target_rate = parsed[0][0]
    joined = array("h")
    previous_pause = 0
    for index, ((rate, samples), (_, pause_before, pause_after)) in enumerate(zip(parsed, chunks)):
        if index:
            gap_ms = max(40, min(500, previous_pause + pause_before))
            joined.extend(array("h", [0]) * round(target_rate * gap_ms / 1000))
        joined.extend(_resample(samples, rate, target_rate))
        previous_pause = pause_after
    is_fish = engine_name.startswith("fish-")
    audio_dir = settings.data_root / ("voice_cache/fish" if is_fish else "audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(f"{reference_id or ''}|{message.id}".encode("utf-8")).hexdigest() if is_fish else message.id
    final = audio_dir / f"{name}.wav"
    temporary = audio_dir / f"{name}.tmp"
    try:
        with wave.open(str(temporary), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(target_rate)
            writer.writeframes(joined.tobytes())
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)
    row = db.get(MessageVoice, message.id) or MessageVoice(message_id=message.id)
    row.status = "ready"
    row.cache_key = hashlib.sha256(
        f"{message.id}|{message.content}|{engine_name}|{PROFILE_VERSION}|{PLAN_VERSION}|{expression}|{speed_factor}".encode("utf-8")
    ).hexdigest()
    row.error_code = None
    row.error_message = None
    row.audio_path = f"{'voice_cache/fish' if is_fish else 'audio'}/{name}.wav"
    row.engine = engine_name
    row.provider = "fish" if engine_name.startswith("fish-") else "indextts"
    row.model = engine_name.removeprefix("fish-") if row.provider == "fish" else "indextts-2.5"
    row.format = "wav"
    row.reference_id = reference_id if is_fish else None
    row.profile_version = PROFILE_VERSION
    row.speech_plan_version = PLAN_VERSION
    row.duration_seconds = len(joined) / target_rate
    row.created_at = row.created_at or datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    db.add(row)
    db.commit()
    return row
