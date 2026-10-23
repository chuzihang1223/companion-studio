"""Strict wire contracts and explicit session/turn transitions."""

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4
import time

from pydantic import BaseModel, ConfigDict, Field

from .audio import CaptureBuffer


class SessionState(StrEnum):
    IDLE = "IDLE"
    INITIALIZING = "INITIALIZING"
    LISTENING = "LISTENING"
    SPEECH_DETECTED = "SPEECH_DETECTED"
    TRANSCRIBING = "TRANSCRIBING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    INTERRUPTING = "INTERRUPTING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class TurnState(StrEnum):
    CREATED = "CREATED"
    CAPTURING = "CAPTURING"
    PARTIAL_TRANSCRIPT = "PARTIAL_TRANSCRIPT"
    FINAL_TRANSCRIPT = "FINAL_TRANSCRIPT"
    GENERATING = "GENERATING"
    SYNTHESIZING = "SYNTHESIZING"
    PLAYING = "PLAYING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


TRANSITIONS = {
    TurnState.CREATED: {TurnState.CAPTURING},
    TurnState.CAPTURING: {TurnState.PARTIAL_TRANSCRIPT, TurnState.FINAL_TRANSCRIPT},
    TurnState.PARTIAL_TRANSCRIPT: {TurnState.PARTIAL_TRANSCRIPT, TurnState.FINAL_TRANSCRIPT},
    TurnState.FINAL_TRANSCRIPT: {TurnState.GENERATING},
    TurnState.GENERATING: {TurnState.SYNTHESIZING, TurnState.PLAYING, TurnState.COMPLETED},
    TurnState.SYNTHESIZING: {TurnState.PLAYING, TurnState.COMPLETED},
    TurnState.PLAYING: {TurnState.COMPLETED},
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRequest(Strict):
    owner_id: UUID
    conversation_id: UUID
    character_id: str = Field(min_length=1, max_length=100)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    tts_provider: Literal["windows-sapi", "configured"] = "windows-sapi"
    voice_id: str = Field(default="", max_length=160)
    speaking_rate: float = Field(default=1.0, ge=0.9, le=1.1)
    # Independent consent: configured TTS may send response text to its cloud.
    remote_tts_consent: bool = False


class Scope(Strict):
    owner_id: UUID
    voice_session_id: UUID
    turn_id: UUID | None = None
    generation_id: UUID | None = None


class Frame(Scope):
    sequence: int = Field(ge=0, le=3000)
    sample_rate: Literal[16000, 24000, 44100, 48000]
    channels: Literal[1] = 1
    sample_format: Literal["pcm_s16le"] = "pcm_s16le"
    frame_duration_ms: float = Field(ge=10, le=200, allow_inf_nan=False)
    timestamp: float = Field(ge=0, allow_inf_nan=False)
    pcm_base64: str = Field(min_length=4, max_length=26000)


class ClientEvent(Scope):
    event: Literal["playback_started", "playback_completed", "playback_failed", "paused", "resumed"]


@dataclass
class VoiceTurn:
    id: str = field(default_factory=lambda: str(uuid4()))
    generation_id: str = field(default_factory=lambda: str(uuid4()))
    audio_stream_id: str = field(default_factory=lambda: str(uuid4()))
    cancellation_token: asyncio.Event = field(default_factory=asyncio.Event)
    state: TurnState = TurnState.CREATED
    capture: CaptureBuffer = field(default_factory=CaptureBuffer)
    task: asyncio.Task | None = None
    transcript: str = ""
    trace: object = None
    provider: object = None
    tts: object = None
    model_routes: object = None
    pipeline: object = None
    created: float = field(default_factory=time.monotonic)

    def transition(self, target: TurnState):
        if target == self.state:
            return
        if self.state in {TurnState.COMPLETED, TurnState.CANCELLED, TurnState.FAILED}:
            raise ValueError("TURN_STATE_CONFLICT")
        if target not in {TurnState.CANCELLED, TurnState.FAILED} and target not in TRANSITIONS.get(
            self.state, set()
        ):
            raise ValueError("TURN_STATE_CONFLICT")
        self.state = target


@dataclass
class VoiceSession:
    options: StartRequest
    id: str = field(default_factory=lambda: str(uuid4()))
    state: SessionState = SessionState.IDLE
    turn: VoiceTurn | None = None
    stopped: bool = False
    configured_provider: object = None
    configured_config: object = None

    def identity(self):
        turn = self.turn
        return {
            "voice_session_id": self.id,
            "owner_id": str(self.options.owner_id),
            "conversation_id": str(self.options.conversation_id),
            "character_id": self.options.character_id,
            "state": self.state.value,
            "turn_id": turn.id if turn else None,
            "turn_state": turn.state.value if turn else None,
            "generation_id": turn.generation_id if turn else None,
            "audio_stream_id": turn.audio_stream_id if turn else None,
            "trace_id": turn.trace.trace_id if turn and turn.trace else None,
        }
