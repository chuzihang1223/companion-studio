"""Stream visible text immediately and schedule local speech in the background."""

import asyncio
from dataclasses import replace
import json
import logging
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import SessionLocal
from app.models import Conversation, Message
from app.schemas.chat import ChatStreamRequest
from app.services.character.prompt_builder import CharacterPromptBuilder
from app.services.conversation import latest_or_create, touch_after_user_message, visible_messages
from app.services.voice.message_cache import save_message_audio
from app.services.voice.prosody_planner import ProsodyPlanner
from app.services.voice.speech_director import SpeechDirector
from app.services.voice.speech_plan import PreviousVoiceState
from app.services.voice.stream_chunker import SpeechStreamChunker


router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def sse(name: str, payload: dict) -> str:
    return (
        f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.post("/stream")
async def chat_stream(request: Request, payload: ChatStreamRequest) -> StreamingResponse:
    character = request.app.state.characters.get(payload.character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found")
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(status_code=422, detail="Message cannot be blank")
    try:
        with SessionLocal() as db:
            conversation = (
                db.get(Conversation, payload.conversation_id)
                if payload.conversation_id
                else latest_or_create(db, payload.character_id)
            )
            if (
                conversation is None
                or conversation.is_deleted
                or conversation.character_id != payload.character_id
            ):
                raise HTTPException(status_code=404, detail="Conversation not found")
            previous = visible_messages(db, conversation)
            reply_target = next(
                (row for row in previous if row.id == payload.reply_to_message_id), None
            )
            if payload.reply_to_message_id and (
                reply_target is None or reply_target.role != "assistant"
            ):
                raise HTTPException(status_code=404, detail="Reply target not found")
            history = [{"role": item.role, "content": item.content} for item in previous[-20:]]
            user_row = Message(
                character_id=payload.character_id,
                conversation_id=conversation.id,
                role="user",
                content=user_text,
                reply_to_message_id=payload.reply_to_message_id,
            )
            db.add(user_row)
            touch_after_user_message(
                conversation, user_text, any(row.role == "user" for row in previous)
            )
            db.commit()
            db.refresh(user_row)
            user_id = user_row.id
            conversation_id = conversation.id
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.exception("Could not save streaming user message")
        raise HTTPException(status_code=503, detail="Could not save message") from exc

    messages = CharacterPromptBuilder().build(
        character,
        history,
        user_text,
        reply_context=reply_target.content if payload.reply_to_message_id else None,
    )
    started = time.perf_counter()
    assistant_id = str(uuid4())

    async def events():
        outgoing: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        speech_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=16)
        director, planner = SpeechDirector(), ProsodyPlanner()
        voice = request.app.state.voice_client
        wav_chunks: list[tuple[str, int, int]] = []
        chunk_engines: set[str] = set()
        assistant_saved = False
        audio_failed = False

        async def produce_text():
            nonlocal assistant_saved
            chunks = SpeechStreamChunker()
            full_reply = ""
            try:
                async for delta in request.app.state.llm_service.stream_chat(messages):
                    full_reply += delta
                    await outgoing.put(("text_delta", {"text": delta}))
                    for sentence in chunks.feed(delta):
                        await speech_queue.put(sentence)
                for sentence in chunks.flush():
                    await speech_queue.put(sentence)
                if not full_reply.strip():
                    raise ValueError("Empty model response")
                with SessionLocal() as db:
                    assistant = Message(
                        id=assistant_id,
                        character_id=payload.character_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_reply.strip(),
                    )
                    db.add(assistant)
                    db.commit()
                    db.refresh(assistant)
                    assistant_saved = True
                    await outgoing.put(
                        (
                            "completed",
                            {
                                "message_id": assistant.id,
                                "character_id": assistant.character_id,
                                "conversation_id": conversation_id,
                                "content": assistant.content,
                                "created_at": assistant.created_at.isoformat(),
                            },
                        )
                    )
            except Exception:
                logger.exception("Streaming chat failed")
                await outgoing.put(("error", {"detail": "回复生成失败，请重试。"}))
            finally:
                await speech_queue.put(None)
                await outgoing.put(("text_done", {}))

        async def produce_audio():
            nonlocal audio_failed
            previous_state: PreviousVoiceState = request.app.state.voice_states.get(
                conversation_id, PreviousVoiceState()
            )
            index = 0
            first_audio = True
            usable = bool(payload.voice_enabled and voice.available)
            if payload.voice_enabled and not usable:
                await outgoing.put(
                    ("voice_status", {"status": "unavailable", "engine": voice.name})
                )
            elif usable and not voice.ready:
                await outgoing.put(("voice_status", {"status": "preparing", "engine": voice.name}))
            try:
                while True:
                    sentence = await speech_queue.get()
                    if sentence is None:
                        break
                    plan = director.plan(
                        user_message=user_text,
                        current_reply=sentence,
                        previous_messages=history,
                        previous_voice_state=previous_state,
                        segment_index_offset=index,
                        expression=payload.expression,
                    )
                    previous_state = plan.next_state
                    for segment in plan.segments:
                        control = (
                            planner.for_expressive(segment)
                            if voice.name == "indextts-2.5-expressive"
                            else planner.for_onnx(segment)
                        )
                        control = replace(
                            control, speed_factor=control.speed_factor * payload.speed_factor
                        )
                        await outgoing.put(
                            (
                                "speech_plan",
                                {
                                    "index": index,
                                    "message_id": assistant_id,
                                    "text": segment.text,
                                    "intent": segment.intent,
                                    "emotion": segment.emotion,
                                    "style_instruction": segment.style_instruction,
                                    "engine": voice.name,
                                },
                            )
                        )
                        if usable:
                            try:
                                result = await asyncio.to_thread(
                                    voice.synthesize, segment.tts_text, control
                                )
                                wav_chunks.append(
                                    (
                                        result["audio_base64"],
                                        segment.delivery.pause_before_ms,
                                        segment.delivery.pause_after_ms,
                                    )
                                )
                                chunk_engines.add(result["engine"])
                                await outgoing.put(
                                    (
                                        "audio_chunk",
                                        {
                                            "index": index,
                                            "message_id": assistant_id,
                                            "audio_base64": result["audio_base64"],
                                            "sample_rate": result["sample_rate"],
                                            "generation_ms": result["generation_ms"],
                                            "pause_before_ms": segment.delivery.pause_before_ms,
                                            "pause_after_ms": segment.delivery.pause_after_ms,
                                            "ttfa_ms": round((time.perf_counter() - started) * 1000)
                                            if first_audio
                                            else None,
                                            "engine": result["engine"],
                                        },
                                    )
                                )
                                first_audio = False
                                await outgoing.put(
                                    (
                                        "voice_status",
                                        {"status": "online", "engine": result["engine"]},
                                    )
                                )
                            except Exception:
                                logger.exception("Voice generation failed")
                                usable = False
                                audio_failed = True
                                await outgoing.put(
                                    ("voice_status", {"status": "error", "engine": voice.name})
                                )
                        index += 1
                request.app.state.voice_states[conversation_id] = previous_state
            finally:
                await outgoing.put(("audio_done", {}))

        text_task = asyncio.create_task(produce_text())
        audio_task = asyncio.create_task(produce_audio())
        yield sse("user_saved", {"message_id": user_id, "conversation_id": conversation_id})
        done = set()
        try:
            while done != {"text_done", "audio_done"}:
                name, data = await outgoing.get()
                if name in {"text_done", "audio_done"}:
                    done.add(name)
                else:
                    yield sse(name, data)
            if assistant_saved and wav_chunks and not audio_failed:
                try:

                    def persist_voice():
                        with SessionLocal() as db:
                            assistant = db.get(Message, assistant_id)
                            engine_name = (
                                next(iter(chunk_engines)) if len(chunk_engines) == 1 else "mixed"
                            )
                            return save_message_audio(db, assistant, wav_chunks, engine_name)

                    saved = await asyncio.to_thread(persist_voice)
                    yield sse(
                        "voice_saved",
                        {"message_id": assistant_id, "engine": saved.engine, "status": "ready"},
                    )
                except Exception:
                    logger.exception("Could not cache streamed voice")
                    yield sse("voice_status", {"status": "error", "engine": voice.name})
            yield sse("done", {})
        finally:
            for task in (text_task, audio_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(text_task, audio_task, return_exceptions=True)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
