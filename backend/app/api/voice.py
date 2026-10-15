"""Local voice state, on-demand generation, and per-message playback."""

import asyncio
import base64
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database.session import SessionLocal
from app.core.config import settings
from app.models import Message, MessageVoice, VoiceJob
from app.services.voice.message_cache import audio_file, index_audio_file
from app.services.voice.prosody_planner import ProsodyPlanner
from app.services.voice.speech_director import SpeechDirector
from app.api.llm_settings import require_control


router = APIRouter(prefix="/api/voice", tags=["voice"])
logger = logging.getLogger(__name__)


@router.get("/cache")
def cache_info() -> dict:
    folder = settings.data_root / "audio"
    fish_folder = settings.data_root / "voice_cache" / "fish"
    files = (list(folder.glob("*.wav")) if folder.is_dir() else []) + (
        list(fish_folder.glob("*.wav")) if fish_folder.is_dir() else []
    )
    return {
        "path": str(settings.data_root),
        "files": len(files),
        "bytes": sum(file.stat().st_size for file in files),
    }


@router.delete("/cache")
def clear_voice_cache() -> dict[str, int]:
    """Remove only files named for persisted messages inside the audio directory."""
    removed = 0
    with SessionLocal() as db:
        rows = db.scalars(select(MessageVoice)).all()
        for row in rows:
            path = audio_file(row)
            if path is not None:
                path.unlink(missing_ok=True)
                removed += 1
            row.status = "missing"
            row.audio_path = None
        db.commit()
    return {"removed": removed}


class VoiceGenerateRequest(BaseModel):
    regenerate: bool = False
    expression: str = "natural"
    speed_factor: float = Field(default=1.0, ge=0.9, le=1.1)


class VoiceEngineRequest(BaseModel):
    engine: str


@router.get("/status")
def status(request: Request) -> dict[str, str]:
    worker = request.app.state.voice_client
    if not worker.available:
        state = "unavailable"
    elif worker.ready:
        state = "online"
    elif worker.error:
        state = "error"
    else:
        state = "preparing"
    return {
        "status": state,
        "engine": worker.name,
        "selected_engine": getattr(worker, "selected_engine", "auto"),
    }


@router.get("/health")
def health(request: Request) -> dict:
    worker = request.app.state.voice_client
    info = worker.ready_info
    state = "error" if worker.error else worker.status if worker.available else "unavailable"
    return {
        "status": state,
        "engine": worker.name,
        "primary": worker.selected_engine,
        "fish": request.app.state.fish_voice.health()
        | {
            "authorized": request.app.state.fish_voice.authorized,
            "reference_ready": request.app.state.fish_voice.authorized,
        },
        "indextts": {"ready": worker.index.ready},
        "model_loaded": worker.ready and bool(info.get("model_loaded")),
        "speaker_loaded": worker.ready and bool(info.get("speaker_loaded")),
        "device": info.get("device"),
        "warmup_ms": info.get("warmup_ms"),
        "queue_length": request.app.state.voice_jobs.queue.qsize(),
        "last_error": worker.error or None,
        "cache_path": str(settings.data_root / "audio"),
        "fish_cache_path": str(settings.data_root / "voice_cache" / "fish"),
    }


@router.post("/worker/restart")
def restart_worker(request: Request) -> dict:
    request.app.state.voice_client.restart()
    return health(request)


@router.post("/test")
async def test_voice(request: Request) -> dict:
    worker = request.app.state.voice_client
    try:
        plan = SpeechDirector().plan(
            user_message="试听", current_reply="嗯……这样应该能听见我了吧？"
        )
        segment = plan.segments[0]
        control = ProsodyPlanner().for_expressive(segment)
        result = await asyncio.to_thread(worker.synthesize, segment.tts_text, control)
        folder = settings.data_root / "audio"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "test-sample.wav"
        temporary = folder / "test-sample.tmp"
        temporary.write_bytes(base64.b64decode(result["audio_base64"]))
        os.replace(temporary, target)
        return {"audio_url": "/api/voice/test/audio", "file_size": target.stat().st_size}
    except Exception as exc:
        logger.exception("Voice test failed")
        raise HTTPException(status_code=503, detail=f"试听失败：{str(exc)[:150]}") from exc


@router.get("/test/audio")
def test_voice_audio() -> FileResponse:
    target = settings.data_root / "audio" / "test-sample.wav"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="请先生成测试语音")
    return FileResponse(
        target,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"},
    )


class NativePlayRequest(BaseModel):
    audio_url: str = Field(max_length=300)
    volume: float = Field(default=1.0, ge=0, le=1)


def native_audio_path(audio_url: str, request: Request) -> Path:
    if audio_url == "/api/voice/fish/stream-test/audio":
        candidate = settings.data_root / "voice_test" / "fish_stream_test.wav"
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Fish 流式测试语音不存在")
        return candidate
    if audio_url == "/api/voice/fish/test/audio":
        candidate = settings.data_root / "voice_test" / "fish_auth_test.mp3"
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Fish 测试语音不存在")
        return candidate
    fish_sample = re.fullmatch(r"/api/voice/fish/character-test/([1-5])/audio", audio_url)
    if fish_sample:
        candidate = settings.data_root / "voice_test" / f"fish_character_{fish_sample.group(1)}.mp3"
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Fish 角色试听不存在")
        return candidate
    fish_compare = re.fullmatch(
        r"/api/voice/fish/compare/(warm|default|expressive|quality)/audio", audio_url
    )
    if fish_compare:
        candidate = settings.data_root / "voice_test" / f"fish_compare_{fish_compare.group(1)}.mp3"
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Fish 对比语音不存在")
        return candidate
    stream_match = re.fullmatch(r"/api/voice/stream/([0-9a-f-]{36})", audio_url)
    if stream_match:
        candidate = settings.data_root / "audio" / "stream" / f"{stream_match.group(1)}.wav"
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="语音片段不存在")
        return candidate
    music_match = re.fullmatch(r"/api/music/audio/([0-9a-f]{24})", audio_url)
    if music_match:
        path = request.app.state.music_library.path(music_match.group(1))
        if path is None or path.suffix.lower() != ".wav":
            raise HTTPException(
                status_code=404, detail="Windows 原生通道仅支持所选曲库中的 PCM WAV"
            )
        return path
    match = re.fullmatch(r"/api/voice/audio/([0-9a-f-]{36})", audio_url)
    if match:
        with SessionLocal() as db:
            row = db.get(MessageVoice, match.group(1))
            path = index_audio_file(row) if row else None
    else:
        match = re.fullmatch(r"/api/voice/jobs/([0-9a-f-]{36})/segments/(\d+)/audio", audio_url)
        if match:
            with SessionLocal() as db:
                job = db.get(VoiceJob, match.group(1))
                path = (
                    request.app.state.voice_jobs.segment_file(job, int(match.group(2)))
                    if job
                    else None
                )
        elif audio_url == "/api/voice/test/audio":
            candidate = settings.data_root / "audio" / "test-sample.wav"
            path = candidate if candidate.is_file() else None
        else:
            raise HTTPException(status_code=400, detail="Invalid audio URL")
    if path is None:
        raise HTTPException(status_code=404, detail="语音文件不存在")
    return path


@router.post("/native/play")
def native_play(
    payload: NativePlayRequest,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
) -> dict:
    require_control(x_companion_control_token)
    try:
        return request.app.state.native_player.play(
            native_audio_path(payload.audio_url, request), payload.volume
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Windows 音频播放失败：{exc}") from exc


@router.post("/native/pause")
def native_pause(
    request: Request, x_companion_control_token: str | None = Header(default=None)
) -> dict:
    require_control(x_companion_control_token)
    return request.app.state.native_player.pause()


@router.post("/native/resume")
def native_resume(
    request: Request, x_companion_control_token: str | None = Header(default=None)
) -> dict:
    require_control(x_companion_control_token)
    return request.app.state.native_player.resume()


@router.post("/native/stop")
def native_stop(
    request: Request, x_companion_control_token: str | None = Header(default=None)
) -> dict:
    require_control(x_companion_control_token)
    return request.app.state.native_player.stop()


@router.get("/native/status")
def native_status(
    request: Request, x_companion_control_token: str | None = Header(default=None)
) -> dict:
    require_control(x_companion_control_token)
    return request.app.state.native_player.status()


@router.post("/engine")
def select_engine(payload: VoiceEngineRequest, request: Request) -> dict[str, str]:
    if payload.engine not in {"index", "fish"}:
        raise HTTPException(status_code=422, detail="语音引擎应为 Fish Audio 或 IndexTTS")
    worker = request.app.state.voice_client
    worker.selected_engine = payload.engine
    return {"selected_engine": payload.engine, "engine": worker.name}


@router.get("/messages/{message_id}")
def message_voice_status(message_id: str) -> dict:
    with SessionLocal() as db:
        message = db.get(Message, message_id)
        if message is None or message.role != "assistant":
            raise HTTPException(status_code=404, detail="Assistant message not found")
        row = db.get(MessageVoice, message_id)
        cached = index_audio_file(row) if row else None
        state = (
            "ready"
            if cached
            else ("missing" if row and row.status == "ready" else row.status if row else "none")
        )
        return {
            "message_id": message_id,
            "status": state,
            "engine": row.engine if row else None,
            "job_id": row.job_id if row else None,
            "error_code": row.error_code if row else None,
            "error_message": row.error_message if row else None,
            "duration_seconds": row.duration_seconds if state == "ready" else None,
            "audio_url": f"/api/voice/audio/{message_id}" if state == "ready" else None,
        }


@router.get("/audio/{message_id}")
@router.get("/messages/{message_id}/audio")
def play_message_audio(message_id: str) -> FileResponse:
    with SessionLocal() as db:
        message = db.get(Message, message_id)
        row = db.get(MessageVoice, message_id)
        if message is None or message.role != "assistant" or row is None:
            raise HTTPException(status_code=404, detail="Voice not found")
        path = index_audio_file(row)
        if path is None:
            raise HTTPException(status_code=404, detail="Voice cache was cleared")
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store", "Accept-Ranges": "bytes"},
    )


@router.get("/audio/{message_id}/diagnostic")
def audio_diagnostic(message_id: str) -> dict:
    import wave

    with SessionLocal() as db:
        message = db.get(Message, message_id)
        row = db.get(MessageVoice, message_id)
        if message is None or message.role != "assistant" or row is None:
            raise HTTPException(status_code=404, detail="Voice not found")
        path = index_audio_file(row)
        diagnostic = {
            "message_id": message_id,
            "audio_path": str(path or row.audio_path or ""),
            "file_exists": path is not None,
            "file_size": path.stat().st_size if path else 0,
            "mime_type": "audio/wav",
            "audio_url": f"/api/voice/audio/{message_id}",
        }
        if path:
            try:
                with wave.open(str(path), "rb") as wav:
                    diagnostic.update(
                        sample_rate=wav.getframerate(),
                        channels=wav.getnchannels(),
                        bit_depth=wav.getsampwidth() * 8,
                        duration=wav.getnframes() / wav.getframerate(),
                        codec="PCM",
                    )
            except (wave.Error, EOFError, ZeroDivisionError) as exc:
                diagnostic["error"] = f"Invalid WAV: {exc}"
        return diagnostic


@router.post("/messages/{message_id}/generate", status_code=202)
async def generate_message_audio(
    message_id: str, payload: VoiceGenerateRequest, request: Request
) -> dict:
    if payload.expression not in {"restrained", "natural", "rich"}:
        raise HTTPException(status_code=422, detail="Invalid expression level")
    try:
        return request.app.state.voice_jobs.enqueue(
            message_id,
            regenerate=payload.regenerate,
            expression=payload.expression,
            speed_factor=payload.speed_factor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    with SessionLocal() as db:
        job = db.get(VoiceJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Voice job not found")
        return request.app.state.voice_jobs.payload(job)


@router.get("/stream/{message_id}")
def early_voice_segment(message_id: str) -> FileResponse:
    if not re.fullmatch(r"[0-9a-f-]{36}", message_id):
        raise HTTPException(status_code=404, detail="语音片段不存在")
    target = settings.data_root / "audio" / "stream" / f"{message_id}.wav"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="语音片段不存在")
    return FileResponse(target, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/segments/{index}/audio")
def job_segment(job_id: str, index: int, request: Request) -> FileResponse:
    with SessionLocal() as db:
        job = db.get(VoiceJob, job_id)
        path = request.app.state.voice_jobs.segment_file(job, index) if job else None
        if path is None:
            raise HTTPException(status_code=404, detail="Voice segment not ready")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})
