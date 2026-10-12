"""Translate a speech plan to the controls supported by a selected engine."""

from dataclasses import dataclass

from .speech_plan import SpeechSegmentPlan


@dataclass(frozen=True)
class EngineControl:
    speed_factor: float
    pause_before_ms: int
    pause_after_ms: int
    reference: str
    style_instruction: str
    emotion: str
    intensity: float
    # Desired values are kept for capable engines. ONNX does not expose them.
    pitch_range: float
    energy: float
    secondary_emotion: str | None = None
    secondary_weight: float = 0.0
    intent: str = "casual_chat"
    style_spans: tuple = ()


class ProsodyPlanner:
    def for_onnx(self, segment: SpeechSegmentPlan) -> EngineControl:
        reference = {
            "neutral": "neutral", "playful": "playful", "happy": "happy",
            "excited": "happy", "concerned": "concerned", "gentle": "concerned",
            "serious": "serious", "protective": "serious", "annoyed": "playful",
            "embarrassed": "playful", "nostalgic": "neutral", "curious": "neutral",
        }.get(segment.emotion, "neutral")
        return EngineControl(
            speed_factor=segment.prosody.pace,
            pause_before_ms=segment.delivery.pause_before_ms,
            pause_after_ms=segment.delivery.pause_after_ms,
            reference=reference,
            style_instruction=segment.style_instruction,
            emotion=segment.emotion,
            intensity=segment.intensity,
            pitch_range=segment.prosody.pitch_range,
            energy=segment.prosody.energy,
            intent=segment.intent,
        )

    def for_expressive(self, segment: SpeechSegmentPlan) -> EngineControl:
        control = self.for_onnx(segment)
        return EngineControl(
            speed_factor=control.speed_factor,
            pause_before_ms=control.pause_before_ms,
            pause_after_ms=control.pause_after_ms,
            reference="neutral",  # Keep speaker identity independent of emotion.
            style_instruction=control.style_instruction,
            emotion=control.emotion,
            intensity=control.intensity,
            pitch_range=control.pitch_range,
            energy=control.energy,
            secondary_emotion=segment.secondary_emotion,
            secondary_weight=0.20 if segment.secondary_emotion else 0.0,
            intent=segment.intent,
        )
