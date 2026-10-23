"""RAM-only voice sessions, scoped final transcripts and generation cancellation.

This layer never calls a tool, creates a second message history or retries Agent
side effects. A final transcript returns to the existing submission service.
"""

import asyncio
import time
from contextlib import suppress

from .contracts import SessionState as S, TurnState as T, VoiceSession, VoiceTurn
from .trace import record
from .providers import ASRProviderRouter, TTSProviderRouter


class VoiceSessionManager:
    def __init__(self, app, provider_factory=None):
        self.app = app
        self.rows = {}
        self.provider_factory = provider_factory or ASRProviderRouter().create
        self.lock = asyncio.Lock()
        self.watchdog_task = None

    def owned(self, scope, *, turn=False, allow_cancelled=False):
        session = self.rows.get(str(scope.voice_session_id))
        if (
            not session
            or session.stopped
            or session.options.owner_id != scope.owner_id
            or turn
            and (
                not session.turn
                or session.turn.id != str(scope.turn_id)
                or session.turn.generation_id != str(scope.generation_id)
                or not allow_cancelled
                and session.turn.cancellation_token.is_set()
            )
        ):
            raise LookupError("VOICE_SCOPE_INVALID")
        return session

    async def start(self, options):
        async with self.lock:
            for old in tuple(self.rows.values()):
                if old.options.owner_id == options.owner_id:
                    await self.stop_session(old)
            if len(self.rows) >= 8:
                raise ValueError("VOICE_CAPACITY_REACHED")
            session = VoiceSession(options)
            if options.tts_provider == "configured":
                session.configured_provider = self.app.state.voice_client.choose()
                session.configured_config = getattr(session.configured_provider, "config", None)
            self.rows[session.id] = session
            if self.watchdog_task is None:
                self.watchdog_task = asyncio.create_task(self.watchdog())
            return session.identity()

    async def watchdog(self):
        while True:
            await asyncio.sleep(0.5)
            for session in tuple(self.rows.values()):
                turn = session.turn
                if turn and turn.state == T.CAPTURING and time.monotonic() - turn.created > 31:
                    await self.cancel_turn(session)
                    session.state = S.ERROR

    async def begin(self, scope):
        session = self.owned(scope)
        if session.state == S.INTERRUPTING:
            raise ValueError("TURN_STATE_CONFLICT")
        if session.options.tts_provider == "configured":
            current = self.app.state.voice_client.choose()
            if (
                current is not session.configured_provider
                or getattr(current, "config", None) is not session.configured_config
            ):
                raise ValueError("VOICE_PROVIDER_CHANGED")
            if current is self.app.state.fish_voice and not session.options.remote_tts_consent:
                raise ValueError("REMOTE_TTS_CONSENT_REQUIRED")
        # Reject overlapping begin calls instead of letting an old await replace
        # a newer turn. Explicit interrupt must finish before starting again.
        if session.turn and session.turn.state not in {T.COMPLETED, T.CANCELLED, T.FAILED}:
            raise ValueError("TURN_STATE_CONFLICT")
        turn = VoiceTurn()
        routes = getattr(self.app.state, "model_roles", None)
        turn.model_routes = (
            routes.capture(task_id=turn.id) if hasattr(routes, "capture") else routes
        )
        session.turn = turn
        turn.provider = self.provider_factory(
            language=session.options.language,
            voice_id=session.options.voice_id,
            speed=session.options.speaking_rate,
        )
        turn.tts = TTSProviderRouter.create(
            session, turn.provider, getattr(self.app.state, "voice_client", None)
        )
        recorder = getattr(self.app.state, "trace_recorder", None)
        if recorder:
            turn.trace = recorder.create(turn.id, str(session.options.conversation_id))
        record(session, "VOICE_SESSION_STARTED")
        turn.transition(T.CAPTURING)
        session.state = S.INITIALIZING
        return session.identity()

    def frame(self, payload):
        session = self.owned(payload, turn=True)
        turn = session.turn
        if turn.state != T.CAPTURING:
            raise ValueError("TURN_STATE_CONFLICT")
        first = turn.capture.sequence == 0
        activity = turn.capture.feed(
            payload.sequence,
            payload.sample_rate,
            payload.frame_duration_ms,
            payload.pcm_base64,
        )
        if first:
            session.state = S.LISTENING
            record(session, "AUDIO_CAPTURE_STARTED")
        if activity["speech_started"]:
            session.state = S.SPEECH_DETECTED
            record(session, "SPEECH_DETECTED")
        return {**session.identity(), **activity}

    async def finalize(self, scope):
        session = self.owned(scope, turn=True)
        turn = session.turn
        if turn.state != T.CAPTURING or turn.task:
            raise ValueError("TURN_STATE_CONFLICT")
        session.state = S.TRANSCRIBING
        try:
            wav = turn.capture.final()
            turn.capture.clear()
            record(session, "ASR_REQUESTED")
            turn.task = asyncio.create_task(turn.provider.recognize(wav))
            text = await asyncio.wait_for(turn.task, 25)
            self.owned(scope, turn=True)  # late ASR cannot write after interruption
            if not text:
                raise ValueError("NO_VALID_SPEECH")
            turn.transcript = text
            turn.transition(T.FINAL_TRANSCRIPT)
            session.state = S.PAUSED  # mic is off; waiting for explicit submit
            record(session, "ASR_FINAL_RECEIVED", character_count=len(text))
            return {**session.identity(), "kind": "FINAL", "text": text, "partial": None}
        except asyncio.CancelledError:
            raise
        except Exception:
            if session.turn is turn and not turn.cancellation_token.is_set():
                turn.transition(T.FAILED)
                session.state = S.ERROR
                record(session, "VOICE_ERROR")
                if turn.trace:
                    turn.trace.finish(RuntimeError("ASR_FAILED"))
            raise
        finally:
            turn.capture.clear()
            turn.task = None

    def claim(self, scope, conversation_id, character_id):
        session = self.owned(scope, turn=True)
        if (
            str(session.options.conversation_id) != conversation_id
            or session.options.character_id != character_id
        ):
            raise LookupError("VOICE_SCOPE_INVALID")
        if session.turn.state != T.FINAL_TRANSCRIPT:
            raise ValueError("TURN_STATE_CONFLICT")
        session.turn.transition(T.GENERATING)
        session.state = S.PROCESSING
        record(session, "LLM_STREAM_STARTED")
        return session

    def generation_without_playback(self, scope, *, failed=False):
        """Close a silent/failed generation without inventing playback observations."""
        session = self.owned(scope, turn=True)
        turn = session.turn
        turn.transition(T.FAILED if failed else T.COMPLETED)
        session.state = S.ERROR if failed else S.IDLE
        record(session, "VOICE_ERROR" if failed else "VOICE_NO_SPEECH", actual_playback=False)
        if turn.trace:
            turn.trace.finish(RuntimeError("VOICE_GENERATION_FAILED") if failed else None)
        return session.identity()

    def event(self, payload):
        session = self.owned(payload, turn=True)
        turn = session.turn
        if payload.event == "playback_started":
            turn.transition(T.PLAYING)
            session.state = S.SPEAKING
            record(session, "AUDIO_PLAYBACK_STARTED")
        elif payload.event == "playback_completed":
            turn.transition(T.COMPLETED)
            session.state = S.IDLE
            record(session, "AUDIO_PLAYBACK_COMPLETED")
            if turn.trace:
                turn.trace.finish()
        elif payload.event == "playback_failed":
            turn.transition(T.FAILED)
            session.state = S.ERROR
            record(session, "VOICE_ERROR")
            if turn.trace:
                turn.trace.finish(RuntimeError("PLAYBACK_FAILED"))
        elif payload.event == "paused":
            session.state = S.PAUSED
        elif payload.event == "resumed":
            # Do not claim SPEAKING until an actual playback-start observation.
            session.state = S.PROCESSING
        return session.identity()

    async def interrupt(self, scope):
        session = self.owned(scope, turn=scope.turn_id is not None, allow_cancelled=True)
        await self.cancel_turn(session)
        return session.identity()

    async def cancel_turn(self, session):
        turn = session.turn
        if not turn or turn.cancellation_token.is_set():
            return
        session.state = S.INTERRUPTING
        record(session, "VOICE_INTERRUPT_REQUESTED")
        # Invalidate synchronously before any await can deliver old callbacks.
        turn.cancellation_token.set()
        if turn.state not in {T.COMPLETED, T.FAILED, T.CANCELLED}:
            turn.transition(T.CANCELLED)
        if turn.task and not turn.task.done():
            turn.task.cancel()
        # A failed helper stop must not skip other owned resources, retain raw
        # input or strand this session in INTERRUPTING forever.
        operations = [turn.provider.cancel]
        if turn.tts is not turn.provider:
            operations.append(turn.tts.cancel)
        if turn.pipeline:
            operations.append(turn.pipeline)
        if hasattr(turn.tts, "close"):
            operations.append(turn.tts.close)
        try:
            for operation in operations:
                with suppress(Exception):
                    await operation()
            if turn.task:
                await asyncio.gather(turn.task, return_exceptions=True)
        finally:
            turn.capture.clear()
            turn.transcript = ""
            session.state = S.IDLE
        # Backend cancellation is not proof that the physical speaker stopped;
        # the renderer separately stops the original player immediately.
        record(session, "VOICE_INTERRUPT_COMPLETED")
        if turn.trace and not turn.trace.finished:
            turn.trace.finish(asyncio.CancelledError())

    async def stop_session(self, session):
        await self.cancel_turn(session)
        record(session, "VOICE_SESSION_STOPPED")
        session.stopped = True
        session.state = S.STOPPED
        self.rows.pop(session.id, None)

    async def stop_owner(self, owner_id):
        for session in tuple(self.rows.values()):
            if str(session.options.owner_id) == str(owner_id):
                await self.stop_session(session)
        return {"state": "STOPPED"}

    async def close(self):
        if self.watchdog_task:
            self.watchdog_task.cancel()
            await asyncio.gather(self.watchdog_task, return_exceptions=True)
            self.watchdog_task = None
        for session in tuple(self.rows.values()):
            with suppress(Exception):
                await self.stop_session(session)
