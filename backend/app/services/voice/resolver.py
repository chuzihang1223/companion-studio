"""Resolve LLM voice hints with context safeguards and restrained smoothing."""

from dataclasses import dataclass

from .policy import VoiceExpressionPolicy, VoiceStyle, clamp
from .text_processor import SpeechSegment


@dataclass(frozen=True)
class EmotionHint:
    emotion: str = "neutral"
    intensity: float = 0.5


@dataclass(frozen=True)
class ResolvedSpeechSegment:
    text: str
    style: VoiceStyle


class EmotionResolver:
    def __init__(self, policy: VoiceExpressionPolicy | None = None):
        self.policy = policy or VoiceExpressionPolicy()

    def resolve(
        self,
        hint: EmotionHint | None,
        *,
        user_text: str = "",
        reply_text: str = "",
        previous_emotion: str | None = None,
        expression: str = "standard",
    ) -> VoiceStyle:
        hint = hint or EmotionHint()
        emotion = hint.emotion if hint.emotion in self.policy.emotions else "neutral"
        intensity = clamp(float(hint.intensity), 0.0, 1.0)
        user = user_text.strip()
        reply = reply_text.strip()

        # Narrow contradictions; the LLM remains the primary classifier.
        if "晚安" in user and emotion in {"excited", "annoyed", "playful"}:
            emotion, intensity = "gentle", min(intensity, 0.55)
        elif any(word in user for word in ("想自杀", "不想活", "去世了")) and emotion in {
            "happy",
            "excited",
            "playful",
        }:
            emotion, intensity = "serious", max(intensity, 0.65)
        elif "别拿自己冒险" in reply and emotion in {"happy", "excited", "playful"}:
            emotion = "concerned"
        elif (
            previous_emotion in self.policy.emotions
            and previous_emotion != emotion
            and intensity < 0.3
        ):
            emotion = previous_emotion
        return self.policy.style(emotion, intensity, expression)

    def resolve_segments(
        self,
        segments: list[SpeechSegment],
        hint: EmotionHint | None,
        *,
        user_text: str = "",
        previous_emotion: str | None = None,
        expression: str = "standard",
    ) -> list[ResolvedSpeechSegment]:
        base = hint or EmotionHint()
        result = []
        current = previous_emotion
        for segment in segments:
            candidate = EmotionHint(
                emotion=segment.emotion or base.emotion,
                intensity=segment.intensity if segment.intensity is not None else base.intensity,
            )
            style = self.resolve(
                candidate,
                user_text=user_text,
                reply_text=segment.text,
                previous_emotion=current,
                expression=expression,
            )
            result.append(ResolvedSpeechSegment(segment.text, style))
            current = style.emotion
        return result
