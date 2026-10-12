"""Engine-independent instructions for how a character reply should be spoken."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PreviousVoiceState:
    emotion: str = "neutral"
    intensity: float = 0.4
    pace: float = 1.0


@dataclass(frozen=True)
class SpeechProsody:
    pace: float = 1.0
    energy: float = 1.0
    pitch_center: float = 0.0
    pitch_range: float = 1.0
    warmth: float = 0.5
    tension: float = 0.2


@dataclass(frozen=True)
class SpeechDelivery:
    opening: str = "casual"
    ending: str = "soft_fall"
    pause_before_ms: int = 0
    pause_after_ms: int = 100


@dataclass(frozen=True)
class SpeechSegmentPlan:
    text: str
    tts_text: str
    intent: str
    emotion: str
    secondary_emotion: str | None
    intensity: float
    prosody: SpeechProsody
    delivery: SpeechDelivery
    emphasis: tuple[str, ...]
    style_instruction: str
    style_spans: tuple = ()


@dataclass(frozen=True)
class SpeechPlan:
    segments: tuple[SpeechSegmentPlan, ...]
    next_state: PreviousVoiceState
