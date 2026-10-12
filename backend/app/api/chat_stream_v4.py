"""Stream visible text and enqueue speech without making chat wait for it."""

import asyncio
import base64
from dataclasses import replace
import json
import logging
import os
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import SessionLocal
from app.core.config import settings
from app.core.file_logging import file_logger
from app.models import Conversation, Message
from app.schemas.chat import ChatStreamRequest
from app.services.character.prompt_builder import CharacterPromptBuilder
from app.services.character.relationship_memory import relationship_memory
from app.services.character.personalization import read_settings, relevant_manual
from app.services.conversation import latest_or_create, touch_after_user_message, visible_messages
from app.services.voice.prosody_planner import ProsodyPlanner
from app.services.voice.speech_director import SpeechDirector
from app.services.voice.stream_chunker import SpeechStreamChunker
from app.services.voice.speech_filter import speakable_text
from app.services.voice.message_cache import _samples
from app.services.attachment_service import AttachmentService
from app.services.event_router import routed_sse


router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def sse(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/stream")
async def chat_stream(request: Request, payload: ChatStreamRequest) -> StreamingResponse:
    character = request.app.state.characters.get(payload.character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found")
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(status_code=422, detail="Message cannot be blank")
    attachments = AttachmentService()
    try:
        character_llm=request.app.state.model_roles.service('character_model')
        active_config = getattr(character_llm, "config", None)
        prepared = attachments.prepare(payload.attachments, vision=bool(
            active_config and active_config.capabilities.get("vision")))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        with SessionLocal() as db:
            conversation = db.get(Conversation, payload.conversation_id) if payload.conversation_id else latest_or_create(db, payload.character_id)
            if conversation is None or conversation.is_deleted or conversation.character_id != payload.character_id:
                raise HTTPException(status_code=404, detail="Conversation not found")
            previous = visible_messages(db, conversation)
            reply_target = next((row for row in previous if row.id == payload.reply_to_message_id), None)
            if payload.reply_to_message_id and (reply_target is None or reply_target.role != "assistant"):
                raise HTTPException(status_code=404, detail="Reply target not found")
            history = [{"role": item.role, "content": item.content} for item in previous[-20:]]
            memory = relationship_memory(db, payload.character_id, {item.id for item in previous[-20:]})
            overlay = read_settings(db, payload.character_id)
            manual = relevant_manual(db, payload.character_id, user_text)
            metadata = attachments.save(prepared)
            user_row = Message(character_id=payload.character_id, conversation_id=conversation.id,
                               role="user", content=user_text, attachments_json=json.dumps(metadata, ensure_ascii=False),
                               reply_to_message_id=payload.reply_to_message_id)
            db.add(user_row)
            touch_after_user_message(conversation, user_text, any(row.role == "user" for row in previous))
            db.commit()
            db.refresh(user_row)
            user_id, conversation_id = user_row.id, conversation.id
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Could not save streaming user message")
        raise HTTPException(status_code=503, detail="Could not save message") from exc

    messages = CharacterPromptBuilder().build(
        character, history, user_text,
        memory_context=memory,
        reply_context=reply_target.content if payload.reply_to_message_id else None,
        personalization=overlay, manual_memory=manual,
    )
    file_context = attachments.context(prepared, user_text)
    if file_context:
        messages[-1]["content"] += "\n\n" + file_context
    if any(item.kind == "image" for item in prepared):
        prompt_text = messages[-1]["content"]
        messages[-1]["content"] = [{"type": "text", "text": prompt_text}, *(
            {"type": "image_url", "image_url": {"url": f"data:{item.media_type};base64,{base64.b64encode(item.content).decode('ascii')}"}}
            for item in prepared if item.kind == "image")]
    llm = request.app.state.model_roles.service('character_model')
    assistant_id = str(uuid4())
    envelope={'task_id':assistant_id,'conversation_id':conversation_id,'work_session_id':None,
              'renderer_id':payload.renderer_id,'reply_id':assistant_id}
    async def scheduled(source):
        registry=getattr(request.app.state,'runtime_registry',None)
        if registry:
            async with registry.run(assistant_id,conversation_id,renderer_id=payload.renderer_id,kind='chat'):
                async for frame in routed_sse(source,envelope):
                    if frame.startswith('event: error\n'):registry.update(assistant_id,'failed','stream_error')
                    yield frame
        else:
            async for frame in routed_sse(source,envelope):yield frame

    if payload.voice_enabled:
        from app.services.voice.continuous_pipeline import continuous_chat_events
        return StreamingResponse(scheduled(continuous_chat_events(request.app, llm, messages, payload, user_id,
            conversation_id, assistant_id, SessionLocal, sse)), media_type='text/event-stream',
            headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    async def events():
        yield sse("user_saved", {"message_id": user_id, "conversation_id": conversation_id})
        full_reply = ""
        chunker = SpeechStreamChunker()
        prefetched: tuple[str, asyncio.Task] | None = None
        prefetched_delivery = None
        early_audio_sent = False
        prefetch_checked = False
        pcm_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        voice_log = file_logger('app.voice.sync', 'voice-sync.log')
        started = time.perf_counter()
        pcm_delivered = False
        def on_pcm(raw, rate, channels):
            loop.call_soon_threadsafe(pcm_queue.put_nowait, {'pcm_base64':base64.b64encode(raw).decode('ascii'),
                'sample_rate':rate, 'channels':channels, 'message_id':assistant_id})


        def ready_audio():
            if not prefetched or not prefetched[1].done():
                return None
            try:
                result = prefetched[1].result()
                if result.get("engine") != request.app.state.voice_client.name:
                    return None
                raw = base64.b64decode(result["audio_base64"], validate=True)
                _samples(raw)
                folder = settings.data_root / "audio" / "stream"
                folder.mkdir(parents=True, exist_ok=True)
                temporary, target = folder / f"{assistant_id}.tmp", folder / f"{assistant_id}.wav"
                temporary.write_bytes(raw)
                os.replace(temporary, target)
                return {"text": prefetched[0], "audio_url": f"/api/voice/stream/{assistant_id}",
                        "pause_before_ms": prefetched_delivery.pause_before_ms,
                        "pause_after_ms": prefetched_delivery.pause_after_ms}
            except Exception:
                logger.exception("Early voice sentence failed; normal voice job will retry")
                return None

        try:
            stream = llm.stream_chat(messages).__aiter__()
            next_delta = asyncio.create_task(anext(stream))
            try:
                while True:
                    waiting = {next_delta}
                    if prefetched and not prefetch_checked:
                        waiting.add(prefetched[1])
                    pcm_wait = asyncio.create_task(pcm_queue.get())
                    waiting.add(pcm_wait)
                    await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    if pcm_wait.done():
                        if not pcm_delivered:
                            voice_log.info('message=%s event=fish_first_audio_chunk elapsed_ms=%s',assistant_id,round((time.perf_counter()-started)*1000))
                        pcm_delivered = True
                        early_audio_sent = True
                        yield sse('audio_pcm',pcm_wait.result())
                    else:
                        pcm_wait.cancel()
                        await asyncio.gather(pcm_wait, return_exceptions=True)
                    if prefetched and prefetched[1].done() and not prefetch_checked:
                        prefetch_checked = True
                        # Drain all queued PCM before the completion event; otherwise
                        # the player could stop before the last samples are scheduled.
                        while not pcm_queue.empty():
                            pcm_delivered = True
                            early_audio_sent = True
                            yield sse('audio_pcm',pcm_queue.get_nowait())
                        audio = ready_audio()
                        if audio:
                            early_audio_sent = True
                            yield sse('audio_complete' if pcm_delivered else 'audio_chunk',audio)
                        elif pcm_delivered:
                            yield sse('audio_aborted', {'detail': '首段语音中断，请点播放重试'})
                            early_audio_sent = False
                    if not next_delta.done():
                        continue
                    try:
                        delta = next_delta.result()
                    except StopAsyncIteration:
                        break
                    next_delta = asyncio.create_task(anext(stream))
                    full_reply += delta
                    yield sse("text_delta", {"text": delta})
                    if payload.voice_enabled and not prefetched:
                        for chunk in chunker.feed(delta):
                            plan = SpeechDirector().plan(user_message=user_text, current_reply=chunk,
                                                         previous_messages=history, expression=payload.expression)
                            if len(chunk) >= 4 and '```' not in full_reply and speakable_text(chunk)==chunk and full_reply.startswith(chunk) and len(plan.segments) == 1 and plan.segments[0].tts_text == chunk:
                                segment = plan.segments[0]
                                control = ProsodyPlanner().for_expressive(segment)
                                control = replace(control, speed_factor=control.speed_factor * payload.speed_factor)
                                provider = request.app.state.voice_client.choose() if hasattr(request.app.state.voice_client,'choose') else request.app.state.voice_client
                                voice_log.info('message=%s event=semantic_first_segment elapsed_ms=%s chars=%s',assistant_id,round((time.perf_counter()-started)*1000),len(chunk))
                                voice_log.info('message=%s event=fish_request_start elapsed_ms=%s',assistant_id,round((time.perf_counter()-started)*1000))
                                synthesize = provider.synthesize_stream if hasattr(provider,'synthesize_stream') else provider.synthesize
                                arguments = (chunk,control,on_pcm) if hasattr(provider,'synthesize_stream') else (chunk,control)
                                prefetched = (chunk, asyncio.create_task(asyncio.to_thread(synthesize,*arguments)))
                                prefetched_delivery = segment.delivery
                                break
            finally:
                if not next_delta.done():
                    next_delta.cancel()
                    await asyncio.gather(next_delta, return_exceptions=True)
                if hasattr(stream, "aclose"):
                    await stream.aclose()
            if not full_reply.strip():
                raise ValueError("Empty model response")
            with SessionLocal() as db:
                assistant = Message(id=assistant_id, character_id=payload.character_id,
                                    conversation_id=conversation_id, role="assistant", content=full_reply.strip())
                db.add(assistant)
                db.commit()
                db.refresh(assistant)
                yield sse("completed", {"message_id": assistant.id, "character_id": assistant.character_id,
                                         "conversation_id": conversation_id, "content": assistant.content,
                                         "created_at": assistant.created_at.isoformat()})
            if prefetched and not prefetch_checked:
                # Keep the first sentence stream alive after the LLM finishes.
                while not prefetched[1].done() or not pcm_queue.empty():
                    try:
                        pcm = await asyncio.wait_for(pcm_queue.get(),timeout=0.1)
                        if not pcm_delivered:
                            voice_log.info('message=%s event=fish_first_audio_chunk elapsed_ms=%s',assistant_id,round((time.perf_counter()-started)*1000))
                        pcm_delivered = True
                        early_audio_sent = True
                        yield sse('audio_pcm',pcm)
                    except asyncio.TimeoutError:
                        pass
                audio = ready_audio()
                if audio:
                    early_audio_sent = True
                    yield sse('audio_complete' if pcm_delivered else 'audio_chunk',audio)
                elif pcm_delivered:
                    yield sse('audio_aborted',{'detail':'首段语音中断，请点播放重试'})
                    early_audio_sent = False
            if payload.voice_prefetch or payload.voice_enabled:
                try:
                    job = request.app.state.voice_jobs.enqueue(
                        assistant_id, expression=payload.expression, speed_factor=payload.speed_factor,
                        prefetched=prefetched)
                    job["early_audio_sent"] = early_audio_sent
                    yield sse("voice_job", job)
                except Exception:
                    logger.exception("Could not enqueue voice prefetch")
                    yield sse("voice_status", {"status": "failed", "detail": "语音排队失败，点击播放可重试"})
        except Exception:
            logger.exception("Streaming chat failed")
            yield sse("error", {"detail": "回复生成失败，请重试。"})
        yield sse("done", {})

    return StreamingResponse(scheduled(events()), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
