"""One FIFO voice worker, persistent job state, and playable sentence chunks."""

import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.database.session import SessionLocal
from app.models import Conversation, Message, MessageVoice, VoiceJob
from app.services.conversation import visible_messages
from .message_cache import _samples, audio_file, index_audio_file, save_message_audio
from .prosody_planner import ProsodyPlanner
from .speech_director import SpeechDirector
from .fish_provider import FishAudioError
from .request_plan import fish_request_plan
from .speech_filter import speakable_text


def _now() -> datetime:
    return datetime.now(timezone.utc)


def error_category(error: Exception) -> tuple[str, str, bool]:
    if isinstance(error, FishAudioError):
        descriptions = {
            401: "Fish Audio API 密钥无效或未正确授权",
            402: "Fish 账户当前不能使用所选模型",
            403: "Fish 账户无权使用该声音",
            404: "Fish 角色声音 ID 无效或没有访问权限",
            422: "Fish 请求参数错误",
            429: "Fish 请求过于频繁或达到服务限制",
            503: "Fish 服务暂时不可用",
        }
        return (
            f"fish_http_{error.status_code}",
            descriptions.get(error.status_code, str(error)),
            error.status_code in {0, 429, 500, 502, 503, 504},
        )
    message = str(error).lower()
    if "out of memory" in message or "cuda oom" in message:
        return "cuda_oom", "显存不足；请关闭占用显存的程序后重试", True
    if "timed out" in message or "timeout" in message:
        return "timeout", "语音推理超时", True
    if "reference" in message and ("missing" in message or "not found" in message):
        return "reference_missing", "角色参考音频不存在", False
    if "model" in message and ("missing" in message or "not found" in message):
        return "model_missing", "语音模型文件不存在", False
    if isinstance(error, OSError):
        return "file_write", "语音文件写入失败", True
    if "unavailable" in message or "exited" in message:
        return "worker_unavailable", "语音进程暂不可用，正在尝试恢复", True
    detail = str(error).strip().replace("\n", " ")[:180]
    return "synthesis_error", f"语音推理失败：{detail}" if detail else "语音推理失败；请重试", False


class VoiceJobManager:
    def __init__(self, voice, data_root: Path | None = None, session_factory=SessionLocal):
        self.voice = voice
        self.data_root = data_root or settings.data_root
        self.session_factory = session_factory
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.runner: asyncio.Task | None = None
        self.current_id: str | None = None
        self.prefetched: dict[str, tuple[str, asyncio.Task]] = {}
        self.logger = logging.getLogger("app.voice.jobs")
        logs = self.data_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        voice_log = logs / "voice.log"
        if not any(
            isinstance(handler, logging.FileHandler) and handler.baseFilename == str(voice_log)
            for handler in self.logger.handlers
        ):
            handler = logging.FileHandler(voice_log, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def start(self) -> None:
        with self.session_factory() as db:
            interrupted = db.scalars(
                select(VoiceJob).where(
                    VoiceJob.status.in_(("queued", "loading", "synthesizing", "saving"))
                )
            ).all()
            for job in interrupted:
                job.status = "failed"
                job.error_code = "interrupted"
                job.error_message = "上次语音任务被中断，点击即可重试"
                job.finished_at = _now()
                voice = db.get(MessageVoice, job.message_id)
                if voice and voice.job_id == job.id:
                    voice.status = "ready" if audio_file(voice) else "failed"
                    voice.error_code = "interrupted"
                    voice.error_message = job.error_message
            db.commit()
        self.runner = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.runner:
            self.runner.cancel()
            await asyncio.gather(self.runner, return_exceptions=True)

    def enqueue(
        self,
        message_id: str,
        *,
        regenerate: bool = False,
        expression: str = "natural",
        speed_factor: float = 1.0,
        prefetched: tuple[str, asyncio.Task] | None = None,
    ) -> dict:
        with self.session_factory() as db:
            message = db.get(Message, message_id)
            if message is None or message.role != "assistant":
                raise LookupError("Assistant message not found")
            voice = db.get(MessageVoice, message_id)
            if voice and index_audio_file(voice) and not regenerate:
                return {
                    "message_id": message_id,
                    "status": "ready",
                    "audio_url": f"/api/voice/audio/{message_id}",
                    "job_id": voice.job_id,
                }
            if voice and voice.job_id:
                old = db.get(VoiceJob, voice.job_id)
                if old and old.status in {"queued", "loading", "synthesizing", "saving"}:
                    if not regenerate:
                        return self.payload(old)
                    old.status = "cancelled"
                    old.finished_at = _now()
            job = VoiceJob(
                id=str(uuid4()),
                message_id=message_id,
                status="queued",
                expression=expression,
                speed_factor=speed_factor,
            )
            db.add(job)
            if voice is None:
                voice = MessageVoice(message_id=message_id)
                db.add(voice)
            voice.job_id = job.id
            voice.status = "queued"
            voice.error_code = None
            voice.error_message = None
            db.commit()
            result = self.payload(job)
        self.queue.put_nowait(job.id)
        if prefetched:
            self.prefetched[job.id] = prefetched
        self._log(job.id, message_id, "queued", text_length=len(message.content))
        return result

    def payload(self, job: VoiceJob) -> dict:
        paths = json.loads(job.segment_paths_json or "[]")
        return {
            "job_id": job.id,
            "message_id": job.message_id,
            "status": job.status,
            "engine": job.engine,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "segments_done": job.segments_done,
            "segments_total": job.segments_total,
            "segments": [
                {"index": i, "audio_url": f"/api/voice/jobs/{job.id}/segments/{i}/audio"}
                for i in range(len(paths))
            ],
            "audio_url": f"/api/voice/audio/{job.message_id}" if job.status == "ready" else None,
            "queue_length": self.queue.qsize(),
            "model_load_ms": job.model_load_ms,
            "synthesis_ms": job.synthesis_ms,
            "duration_ms": job.duration_ms,
        }

    def segment_file(self, job: VoiceJob, index: int) -> Path | None:
        paths = json.loads(job.segment_paths_json or "[]")
        if index < 0 or index >= len(paths):
            return None
        root = self.data_root.resolve()
        target = (root / paths[index]).resolve()
        return target if target.is_relative_to(root) and target.is_file() else None

    def _log(self, job_id: str, message_id: str, stage: str, **data) -> None:
        self.logger.info(
            json.dumps(
                {"job_id": job_id, "message_id": message_id, "stage": stage, **data},
                ensure_ascii=False,
            )
        )

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            self.current_id = job_id
            try:
                await self._run(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception("Unexpected voice queue error for %s", job_id)
                code, message, _ = error_category(exc)
                try:
                    with self.session_factory() as db:
                        row = db.get(VoiceJob, job_id)
                        if row and row.status not in {"ready", "cancelled"}:
                            row.status, row.error_code, row.error_message = "failed", code, message
                            row.finished_at = _now()
                            voice = db.get(MessageVoice, row.message_id)
                            if voice and voice.job_id == job_id:
                                voice.status, voice.error_code, voice.error_message = (
                                    "failed",
                                    code,
                                    message,
                                )
                            db.commit()
                except Exception:
                    self.logger.exception("Could not mark voice job %s failed", job_id)
            finally:
                self.prefetched.pop(job_id, None)
                self.current_id = None
                self.queue.task_done()

    async def _run(self, job_id: str) -> None:
        started = time.perf_counter()
        pending, failed_segments = {}, []
        with self.session_factory() as db:
            job = db.get(VoiceJob, job_id)
            if job is None or job.status == "cancelled":
                return
            message = db.get(Message, job.message_id)
            conversation = (
                db.get(Conversation, message.conversation_id) if message.conversation_id else None
            )
            history = visible_messages(db, conversation) if conversation else [message]
            position = next(
                (i for i, item in enumerate(history) if item.id == message.id), len(history) - 1
            )
            prior = history[:position]
            user_text = next((row.content for row in reversed(prior) if row.role == "user"), "")
            job.status = "loading"
            job.started_at = _now()
            job.queue_wait_ms = round(
                (_now() - job.created_at.replace(tzinfo=timezone.utc)).total_seconds() * 1000
            )
            db.commit()
            message_id, reply = (
                message.id,
                speakable_text(message.content) or "代码放在工作区了，可以直接查看。",
            )
            recent_context = [{"role": row.role, "content": row.content} for row in prior[-8:]]
            work_state = (
                {"work_mode": "active"}
                if conversation and conversation.preferred_mode == "work"
                else None
            )
            expression, speed = job.expression, job.speed_factor
        self._log(
            job_id, message_id, "loading", queue_wait_ms=job.queue_wait_ms, engine=self.voice.name
        )
        try:
            provider = self.voice.choose() if hasattr(self.voice, "choose") else self.voice
            provider.start_background()
            deadline = time.monotonic() + 180
            while not provider.ready:
                if not provider.available:
                    raise RuntimeError("Voice worker unavailable: model or reference missing")
                if provider.error:
                    raise RuntimeError(provider.error)
                if time.monotonic() > deadline:
                    raise TimeoutError("model load timeout")
                await asyncio.sleep(0.25)
            load_ms = round((time.perf_counter() - started) * 1000)
            plan = SpeechDirector().plan(
                user_message=user_text,
                current_reply=reply,
                previous_messages=recent_context,
                character_state=work_state,
                expression=expression,
            )
            if provider.name.startswith("fish-"):
                plan = fish_request_plan(reply, user_text, recent_context, expression, work_state)
            prefix = self.prefetched.get(job_id, ("", None))[0]
            if prefix and reply.startswith(prefix):
                first = SpeechDirector().plan(
                    user_message=user_text,
                    current_reply=prefix,
                    previous_messages=recent_context,
                    character_state=work_state,
                    expression=expression,
                )
                rest = SpeechDirector().plan(
                    user_message=user_text,
                    current_reply=reply[len(prefix) :],
                    previous_messages=recent_context,
                    character_state=work_state,
                    expression=expression,
                )
                plan = replace(plan, segments=first.segments + rest.segments)
            if not plan.segments:
                raise ValueError("No speakable text")
            with self.session_factory() as db:
                job = db.get(VoiceJob, job_id)
                if job.status == "cancelled":
                    return
                job.model_load_ms = load_ms
                job.segments_total = len(plan.segments)
                job.status = "synthesizing"
                db.commit()
            chunks: list[tuple[str, int, int]] = []
            engines: set[str] = set()
            paths: list[str] = []
            synthesis_started = time.perf_counter()
            for index, segment in enumerate(plan.segments):
                with self.session_factory() as db:
                    if db.get(VoiceJob, job_id).status == "cancelled":
                        return
                control = ProsodyPlanner().for_expressive(segment)
                control = replace(control, speed_factor=control.speed_factor * speed)
                control = replace(control, style_spans=segment.style_spans)
                prefetch = self.prefetched.pop(job_id, None) if index == 0 else None
                if provider.name.startswith("fish-"):
                    for next_index in (index, index + 1):
                        if (
                            next_index >= len(plan.segments)
                            or next_index in pending
                            or (next_index == 0 and prefetch)
                        ):
                            continue
                        upcoming = plan.segments[next_index]
                        direction = replace(
                            ProsodyPlanner().for_expressive(upcoming),
                            speed_factor=speed * upcoming.prosody.pace,
                            style_spans=upcoming.style_spans,
                        )
                        pending[next_index] = asyncio.create_task(
                            asyncio.to_thread(provider.synthesize, upcoming.tts_text, direction)
                        )
                result = None
                for attempt in range(2):
                    try:
                        if prefetch and prefetch[0] == segment.tts_text:
                            try:
                                result = await prefetch[1]
                                if result.get("engine") != provider.name:
                                    raise ValueError("Prefetch used another voice provider")
                            except Exception:
                                self.logger.exception(
                                    "Prefetched first segment failed; generating normally"
                                )
                                result = (
                                    await pending.pop(index)
                                    if attempt == 0 and index in pending
                                    else await asyncio.to_thread(
                                        provider.synthesize, segment.tts_text, control
                                    )
                                )
                        else:
                            result = (
                                await pending.pop(index)
                                if attempt == 0 and index in pending
                                else await asyncio.to_thread(
                                    provider.synthesize, segment.tts_text, control
                                )
                            )
                        break
                    except Exception as exc:
                        alternate = (
                            self.voice.fallback(provider, exc)
                            if index == 0 and hasattr(self.voice, "fallback")
                            else None
                        )
                        if alternate and attempt == 0:
                            self._log(
                                job_id,
                                message_id,
                                "fallback",
                                reason=type(exc).__name__,
                                engine=alternate.name,
                            )
                            for future in pending.values():
                                future.cancel()
                            await asyncio.gather(*pending.values(), return_exceptions=True)
                            pending.clear()
                            provider = alternate
                            provider.start_background()
                            if not provider.ready:
                                raise RuntimeError("Fish 失败，本地语音尚未准备好") from exc
                            prefetch = None
                            continue
                        code, _, transient = error_category(exc)
                        self.logger.exception(
                            "Voice job %s segment %s attempt %s failed", job_id, index, attempt
                        )
                        if attempt or not transient:
                            if index > 0 and transient:
                                failed_segments.append(index)
                                break
                            raise
                        if code == "cuda_oom" and hasattr(provider, "restart"):
                            provider.restart()
                        await asyncio.sleep(0.3)
                if result is None:
                    continue
                raw = base64.b64decode(result["audio_base64"], validate=True)
                if result.get("engine") != provider.name:
                    raise RuntimeError("语音任务中途切换了音色，已停止生成")
                _samples(raw)
                folder = self.data_root / "audio" / "jobs" / job_id
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{index}.wav"
                temporary = folder / f"{index}.tmp"
                temporary.write_bytes(raw)
                os.replace(temporary, target)
                relative = target.relative_to(self.data_root).as_posix()
                chunks.append(
                    (
                        result["audio_base64"],
                        0
                        if provider.name.startswith("fish-")
                        else segment.delivery.pause_before_ms,
                        0 if provider.name.startswith("fish-") else segment.delivery.pause_after_ms,
                    )
                )
                engines.add(result["engine"])
                paths.append(relative)
                with self.session_factory() as db:
                    job = db.get(VoiceJob, job_id)
                    if job.status == "cancelled":
                        return
                    job.engine = result["engine"]
                    job.segment_paths_json = json.dumps(paths)
                    job.segments_done = len(paths)
                    job.synthesis_ms = round((time.perf_counter() - synthesis_started) * 1000)
                    db.commit()
                self._log(
                    job_id,
                    message_id,
                    "segment_ready",
                    index=index,
                    generation_ms=result.get("generation_ms"),
                    transport=result.get("transport"),
                )
            if failed_segments:
                raise RuntimeError("部分语音片段生成失败，后续片段已继续生成；可重新生成完整语音。")
            with self.session_factory() as db:
                job = db.get(VoiceJob, job_id)
                if job.status == "cancelled":
                    return
                job.status = "saving"
                db.commit()
            save_started = time.perf_counter()
            with self.session_factory() as db:
                message = db.get(Message, message_id)
                saved = save_message_audio(
                    db,
                    message,
                    chunks,
                    next(iter(engines)) if len(engines) == 1 else "mixed",
                    expression=expression,
                    speed_factor=speed,
                    reference_id=provider.config.reference_id
                    if provider is getattr(self.voice, "fish", None)
                    else None,
                )
                saved.voice_profile = (
                    "studio_fish" if saved.provider == "fish" else "studio_indextts"
                )
                saved.style_plan = json.dumps(
                    {"expression": expression, "segments": len(chunks)}, ensure_ascii=False
                )
                saved.generation_ms = job.synthesis_ms
                job = db.get(VoiceJob, job_id)
                job.status = "ready"
                job.engine = saved.engine
                job.save_ms = round((time.perf_counter() - save_started) * 1000)
                job.duration_ms = round((time.perf_counter() - started) * 1000)
                job.finished_at = _now()
                saved.status = "ready"
                db.commit()
            self._log(
                job_id,
                message_id,
                "ready",
                engine=saved.engine,
                duration_ms=job.duration_ms,
                model_load_ms=load_ms,
                synthesis_ms=job.synthesis_ms,
                save_ms=job.save_ms,
            )
        except Exception as exc:
            code, message, _ = error_category(exc)
            self.logger.exception("Voice job %s failed", job_id)
            with self.session_factory() as db:
                job = db.get(VoiceJob, job_id)
                job.status = "failed"
                job.error_code = code
                job.error_message = message
                job.finished_at = _now()
                job.duration_ms = round((time.perf_counter() - started) * 1000)
                row = db.get(MessageVoice, job.message_id)
                if row and row.job_id == job_id:
                    row.status = "ready" if audio_file(row) else "failed"
                    row.error_code = code
                    row.error_message = message
                db.commit()
            self._log(job_id, message_id, "failed", error_code=code, duration_ms=job.duration_ms)

        finally:
            for future in pending.values():
                if not future.done():
                    future.cancel()
            await asyncio.gather(*pending.values(), return_exceptions=True)
