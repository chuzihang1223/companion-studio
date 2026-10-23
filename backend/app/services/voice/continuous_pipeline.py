"""One text/speech pipeline for Chat, Work and Agent, with bounded queues and cancellation."""

import asyncio
import base64
from dataclasses import replace
from io import BytesIO
import hashlib
import json
import time
from uuid import uuid4
import wave
from sqlalchemy.exc import SQLAlchemyError

from app.core.file_logging import file_logger
from app.models import Message
from app.services.llm.routing.contracts import RoutingError
from app.services.trace.scope import current_trace, current_span
from .canonical_text import CanonicalResponseText, IncrementalSpeechText
from .message_cache import save_message_audio
from .prosody_planner import ProsodyPlanner
from .speech_director import SpeechDirector
from .speech_plan import PreviousVoiceState
from .speech_filter import speakable_text


class RealtimeVoicePipeline:
    def __init__(self, app, payload, assistant_id, factory):
        self.app, self.payload, self.reply_id, self.factory = app, payload, assistant_id, factory
        self.session_id = str(uuid4())
        self.started = time.perf_counter()
        self.logger = file_logger("app.voice.continuity", "voice-continuity.log")
        self.latency = file_logger("app.voice.latency", "voice-latency.log")

    def log(self, event, **values):
        row = {
            "message_id": self.reply_id,
            "reply_id": self.reply_id,
            "session_id": self.session_id,
            "segment_id": self.reply_id + ":0",
            "event": event,
            "elapsed_ms": round((time.perf_counter() - self.started) * 1000),
            "monotonic_ms": round(time.perf_counter() * 1000, 3),
            **values,
        }
        self.logger.info(json.dumps(row))
        self.latency.info(json.dumps(row))

    def save_audio(self, result, provider, realtime):
        with self.factory() as db:
            row = db.get(Message, self.reply_id)
            if not row:
                return None
            try:
                saved = save_message_audio(
                    db,
                    row,
                    [(result["audio_base64"], 0, 0)],
                    result.get("engine", provider.name),
                    expression=self.payload.expression,
                    speed_factor=self.payload.speed_factor,
                    reference_id=getattr(getattr(provider, "config", None), "reference_id", None),
                    realtime=realtime,
                )
                return {"engine": saved.engine}
            except (OSError, ValueError, SQLAlchemyError) as exc:
                if not realtime:
                    raise
                # Text and played PCM are already complete. A quota/disk error
                # must not invite the user to execute an Agent side effect again.
                db.rollback()
                self.log("voice_cache_failed", error=type(exc).__name__)
                return None

    async def events(self, deltas, messages, on_complete):
        outgoing, speech = asyncio.Queue(maxsize=32), asyncio.Queue(maxsize=16)
        canonical, chunker = CanonicalResponseText(), IncrementalSpeechText()
        provider = (
            self.app.state.voice_client.choose()
            if hasattr(self.app.state.voice_client, "choose")
            else self.app.state.voice_client
        )
        voice_session = voice_turn = None
        binding = getattr(self.payload, "voice_context", None)
        if binding:
            from app.services.realtime_voice.contracts import Scope

            voice_session = self.app.state.realtime_voice.owned(
                Scope(owner_id=self.payload.renderer_id, **binding.model_dump()), turn=True
            )
            voice_turn = voice_session.turn
            provider = voice_turn.tts

        def voice_event(event, **metadata):
            if voice_session and voice_session.turn is voice_turn:
                from app.services.realtime_voice.contracts import TurnState
                from app.services.realtime_voice.trace import record

                if event == "TTS_CHUNK_REQUESTED" and voice_turn.state == TurnState.GENERATING:
                    voice_turn.transition(TurnState.SYNTHESIZING)
                record(voice_session, event, **metadata)

        def guard_generation():
            if voice_session and (
                voice_session.turn is not voice_turn or voice_turn.cancellation_token.is_set()
            ):
                raise asyncio.CancelledError()

        if getattr(getattr(provider, "config", None), "latency", None) == "normal":
            # Quality delivery needs enough context before the single early flush;
            # flushing a tiny reaction alone makes the next phrase restart audibly.
            chunker = IncrementalSpeechText(initial_chars=16, initial_limit=60)
        voice = self.payload.voice_enabled
        result, error = None, None
        synthesis_failed = False
        sequence = 0
        audio_started, produced_seconds = None, 0.0
        director, previous_voice, segment_offset = SpeechDirector(), PreviousVoiceState(), 0
        low_water = False
        direction = (
            provider.new_direction_session(expression=self.payload.expression)
            if hasattr(provider, "new_direction_session")
            else provider
        )

        async def text_worker():
            nonlocal error
            first = True
            tokens = None
            if voice_turn and voice_turn.trace:
                tokens = (
                    current_trace.set(voice_turn.trace),
                    current_span.set(voice_turn.trace.root_span_id),
                )

            async def accept(delta, final=False):
                nonlocal first, previous_voice, segment_offset, low_water
                guard_generation()
                visible, spoken = canonical.feed(delta, final)
                if visible:
                    if first:
                        self.log("first_text", chars=len(visible))
                        first = False
                    await outgoing.put(("text_delta", {"text": visible}))
                if audio_started is not None:
                    elapsed = max(0.01, time.perf_counter() - audio_started)
                    buffered = max(0, produced_seconds - elapsed)
                    source = "estimated-duration"
                    native = getattr(getattr(self.app.state, "native_player", None), "pcm", None)
                    if native and getattr(native, "message_id", None) == self.reply_id:
                        buffered = native.status()["buffered_seconds"]
                        source = "hardware-frames"
                    chunker.adapt(buffered, produced_seconds / elapsed)
                    low = buffered < 1.2
                    if low and not low_water:
                        self.log(
                            "prefetch_requested",
                            buffered_audio_ms=round(buffered * 1000),
                            source=source,
                        )
                    low_water = low
                for part in chunker.feed(spoken, final):
                    if not voice:
                        continue
                    plan = director.plan(
                        user_message=self.payload.message,
                        current_reply=part,
                        previous_messages=messages[-8:-1],
                        expression=self.payload.expression,
                        previous_voice_state=previous_voice if segment_offset else None,
                        segment_index_offset=segment_offset,
                    )
                    if not plan.segments:
                        continue
                    previous_voice = plan.next_state
                    segment_offset += len(plan.segments)
                    control = ProsodyPlanner().for_expressive(plan.segments[0])
                    control = replace(
                        control,
                        style_spans=tuple(
                            (s.tts_text, s.intent, s.intensity) for s in plan.segments
                        ),
                        pause_before_ms=0,
                        pause_after_ms=0,
                    )
                    directed = (
                        direction.directed_text(part, control)
                        if hasattr(direction, "directed_text")
                        else part
                    )
                    self.log(
                        "text_ready",
                        chars=len(part),
                        intent=control.intent,
                        body_sha256=hashlib.sha256(part.encode()).hexdigest(),
                    )
                    await speech.put(directed)
                    voice_event("TTS_CHUNK_REQUESTED", character_count=len(part))

            try:
                async for delta in deltas:
                    await accept(delta)
                await accept("", True)
                if not canonical.text.strip():
                    raise ValueError("Empty model response")
                completed = await on_complete(canonical.text.strip())
                await outgoing.put(("completed", completed))
            except asyncio.CancelledError:
                raise
            except RoutingError as exc:
                error = exc
                await outgoing.put(
                    (
                        "error",
                        {
                            "detail": exc.public()["message"],
                            "code": exc.public()["code"],
                            "retryable": False,
                        },
                    )
                )
            except Exception as exc:
                error = exc
                await outgoing.put(("error", {"detail": "回复生成失败，请重试。"}))
            finally:
                try:
                    # Close in the task that iterated the generator: model span
                    # ContextVar tokens must never reset from another task.
                    if hasattr(deltas, "aclose"):
                        await deltas.aclose()
                finally:
                    if tokens:
                        current_span.reset(tokens[1])
                        current_trace.reset(tokens[0])
                if not asyncio.current_task().cancelling():
                    await speech.put(None)
                    await outgoing.put(("text_done", {}))

        async def on_pcm(raw, rate, channels):
            nonlocal sequence, audio_started, produced_seconds
            guard_generation()
            if not raw:
                return
            if not sequence:
                audio_started = time.perf_counter()
                self.log("first_audio", sample_rate=rate)
                voice_event("TTS_CHUNK_READY")
            produced_seconds += len(raw) / (rate * channels * 2)
            # Normal quality can return several seconds in one WebSocket packet.
            # Keep every IPC/SSE frame safely below the existing payload limit.
            frame_bytes = channels * 2
            packet_bytes = 131072 // frame_bytes * frame_bytes
            for offset in range(0, len(raw), packet_bytes):
                packet = raw[offset : offset + packet_bytes]
                self.log("pcm_received", sequence=sequence, bytes=len(packet))
                await outgoing.put(
                    (
                        "audio_pcm",
                        {
                            "message_id": self.reply_id,
                            "reply_id": self.reply_id,
                            "session_id": self.session_id,
                            "sequence": sequence,
                            "segment_id": self.reply_id + ":0",
                            "pcm_base64": base64.b64encode(packet).decode(),
                            "sample_rate": rate,
                            "channels": channels,
                            "initial_buffer_ms": 900
                            if getattr(getattr(provider, "config", None), "latency", None)
                            == "normal"
                            else 300,
                        },
                    )
                )
                sequence += 1

        async def audio_worker():
            voice_gate = getattr(
                getattr(self.app.state, "runtime_registry", None), "gates", {}
            ).get("voice")
            gate_acquired = False
            nonlocal result, synthesis_failed
            try:
                if voice:
                    if voice_gate:
                        await asyncio.wait_for(voice_gate.acquire(), 120)
                        gate_acquired = True
                    self.log(
                        "tts_request_start",
                        mode="shared-live",
                        latency=getattr(getattr(provider, "config", None), "latency", None),
                    )
                    if hasattr(provider, "synthesize_live"):
                        parts, rate, transport = await provider.synthesize_live(
                            speech,
                            self.payload.speed_factor * getattr(provider.config, "speed", 1),
                            on_pcm,
                        )
                        pcm = b"".join(parts)
                        if pcm:
                            if len(pcm) % 2:
                                raise ValueError("PCM frame alignment mismatch")
                            audio = BytesIO()
                            with wave.open(audio, "wb") as writer:
                                writer.setnchannels(1)
                                writer.setsampwidth(2)
                                writer.setframerate(rate)
                                writer.writeframes(pcm)
                            result = {
                                "audio_base64": base64.b64encode(audio.getvalue()).decode(),
                                "sample_rate": rate,
                                "engine": provider.name,
                            }
                            self.log(
                                "utterance_generated",
                                transport=transport,
                                duration_seconds=len(pcm) / (rate * 2),
                                audio_sequences=sequence,
                            )
                    else:
                        # Local backup uses the same output device and canonical reply.
                        texts = []
                        while (part := await speech.get()) is not None:
                            texts.append(part)
                        plain = speakable_text("".join(texts))
                        if plain:
                            plan = SpeechDirector().plan(
                                user_message=self.payload.message,
                                current_reply=plain,
                                expression=self.payload.expression,
                            )
                            control = replace(
                                ProsodyPlanner().for_expressive(plan.segments[0]),
                                speed_factor=self.payload.speed_factor,
                            )
                            result = await asyncio.to_thread(provider.synthesize, plain, control)
                            with wave.open(
                                BytesIO(base64.b64decode(result["audio_base64"])), "rb"
                            ) as reader:
                                await on_pcm(
                                    reader.readframes(reader.getnframes()),
                                    reader.getframerate(),
                                    reader.getnchannels(),
                                )
                await outgoing.put(
                    (
                        "audio_segment_end",
                        {
                            "message_id": self.reply_id,
                            "session_id": self.session_id,
                            "segment_id": self.reply_id + ":0",
                        },
                    )
                )
                await outgoing.put(
                    ("audio_end", {"message_id": self.reply_id, "session_id": self.session_id})
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                backup = (
                    getattr(provider, "fallback", lambda *_: None)(exc)
                    if voice_session
                    else getattr(self.app.state.voice_client, "fallback", lambda *_: None)(
                        provider, exc
                    )
                )
                # Once any PCM is delivered, switching engines would repeat or mix a reply.
                if not sequence and backup and getattr(backup, "available", False):
                    while not workers[0].done() or not speech.empty():
                        try:
                            if await asyncio.wait_for(speech.get(), 0.1) is None:
                                break
                        except asyncio.TimeoutError:
                            pass
                    try:
                        plan = SpeechDirector().plan(
                            user_message=self.payload.message,
                            current_reply=canonical.spoken,
                            expression=self.payload.expression,
                        )
                        control = replace(
                            ProsodyPlanner().for_expressive(plan.segments[0]),
                            speed_factor=self.payload.speed_factor,
                        )
                        if voice_session:
                            from app.services.realtime_voice.tts import local_fallback_audio

                            result = await local_fallback_audio(
                                backup, canonical.spoken, self.payload.speed_factor, on_pcm
                            )
                        else:
                            result = await asyncio.to_thread(
                                backup.synthesize, speakable_text(canonical.spoken), control
                            )
                            with wave.open(
                                BytesIO(base64.b64decode(result["audio_base64"])), "rb"
                            ) as reader:
                                await on_pcm(
                                    reader.readframes(reader.getnframes()),
                                    reader.getframerate(),
                                    reader.getnchannels(),
                                )
                        self.log("local_fallback", engine=backup.name)
                        await outgoing.put(
                            (
                                "audio_segment_end",
                                {
                                    "message_id": self.reply_id,
                                    "session_id": self.session_id,
                                    "segment_id": self.reply_id + ":0",
                                },
                            )
                        )
                    except Exception as fallback_error:
                        synthesis_failed = True
                        self.log("voice_failed", error=type(fallback_error).__name__)
                        await outgoing.put(
                            ("voice_error", {"detail": "云端和本地语音暂时不可用，请稍后重试。"})
                        )
                else:
                    synthesis_failed = True
                    self.log("voice_failed", error=type(exc).__name__)
                    await outgoing.put(
                        ("voice_error", {"detail": str(exc)[:180] or "语音生成失败。"})
                    )
                    while not workers[0].done() or not speech.empty():
                        try:
                            if await asyncio.wait_for(speech.get(), 0.1) is None:
                                break
                        except asyncio.TimeoutError:
                            pass
                await outgoing.put(
                    ("audio_end", {"message_id": self.reply_id, "session_id": self.session_id})
                )
            finally:
                if gate_acquired:
                    voice_gate.release()
                if not asyncio.current_task().cancelling():
                    await outgoing.put(("audio_done", {}))

        workers = [asyncio.create_task(text_worker()), asyncio.create_task(audio_worker())]

        async def cancel_turn():
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            while not outgoing.empty():
                outgoing.get_nowait()
            outgoing.put_nowait(("voice_cancelled", {}))

        if voice_session:
            voice_turn.pipeline = cancel_turn
        sessions = getattr(self.app.state, "live_voice_sessions", None)
        if sessions is None:
            sessions = self.app.state.live_voice_sessions = {}

        async def cancel_audio():
            if workers[1].done():
                return
            workers[1].cancel()
            await asyncio.gather(workers[1], return_exceptions=True)
            await outgoing.put(
                ("audio_end", {"message_id": self.reply_id, "session_id": self.session_id})
            )
            await outgoing.put(("audio_done", {}))
            # Keep consuming text so switching conversations never blocks a work task.
            while not workers[0].done() or not speech.empty():
                try:
                    if await asyncio.wait_for(speech.get(), 0.1) is None:
                        break
                except asyncio.TimeoutError:
                    pass

        sessions[self.session_id] = cancel_audio
        yield "assistant_started", {"message_id": self.reply_id, "session_id": self.session_id}
        done = set()
        try:
            while len(done) < 2:
                event, data = await outgoing.get()
                if event == "voice_cancelled":
                    break
                if event in {"text_done", "audio_done"}:
                    done.add(event)
                else:
                    guard_generation()
                    yield (
                        event,
                        {
                            **data,
                            **(binding.model_dump(mode="json") if binding else {}),
                        },
                    )
            if (
                result
                and not error
                and not (voice_session and voice_turn.cancellation_token.is_set())
            ):
                saved = self.save_audio(result, provider, voice_session is not None)
                if saved:
                    yield (
                        "voice_saved",
                        {"message_id": self.reply_id, "engine": saved["engine"], "status": "ready"},
                    )
                elif voice_session:
                    yield (
                        "voice_error",
                        {
                            "detail": "回复已生成，但语音缓存未保存。请检查磁盘空间或缓存配额；不要重复执行任务。"
                        },
                    )
            if voice_session and not sequence and not voice_turn.cancellation_token.is_set():
                # Code/JSON/URLs can produce valid visible text and no spoken text.
                # No player exists in that case, so it cannot report an ended event.
                identity = self.app.state.realtime_voice.generation_without_playback(
                    Scope(owner_id=self.payload.renderer_id, **binding.model_dump()),
                    failed=bool(error or synthesis_failed or canonical.spoken.strip()),
                )
                yield "voice_state", {"message_id": self.reply_id, **identity}
            self.log(
                "generation_done",
                canonical_chars=len(canonical.text),
                canonical_sha256=hashlib.sha256(canonical.text.strip().encode()).hexdigest(),
            )
            yield "done", {}
        finally:
            sessions.pop(self.session_id, None)
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            if voice_session:
                voice_turn.pipeline = None
                if hasattr(provider, "close"):
                    await provider.close()
            if hasattr(deltas, "aclose"):
                await deltas.aclose()


async def continuous_chat_events(
    app, llm, messages, payload, user_id, conversation_id, assistant_id, factory, sse
):
    envelope = {
        "task_id": assistant_id,
        "conversation_id": conversation_id,
        "work_session_id": None,
        "renderer_id": getattr(payload, "renderer_id", None),
        "reply_id": assistant_id,
    }

    async def complete(text):
        with factory() as db:
            row = Message(
                id=assistant_id,
                character_id=payload.character_id,
                conversation_id=conversation_id,
                role="assistant",
                content=text,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return {
                "message_id": assistant_id,
                "character_id": payload.character_id,
                "conversation_id": conversation_id,
                "content": text,
                "created_at": row.created_at.isoformat(),
            }

    yield sse("user_saved", {**envelope, "message_id": user_id, "event_type": "user_saved"})
    pipeline = RealtimeVoicePipeline(app, payload, assistant_id, factory)
    async for event, data in pipeline.events(llm.stream_chat(messages), messages, complete):
        yield sse(event, {**envelope, **data, "event_type": event})
