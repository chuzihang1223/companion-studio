"""Bounded controls for an optional user-supplied local voice engine."""

from dataclasses import dataclass
import json
import os
from pathlib import Path


PRESET_FILE = Path(__file__).with_name("voice_presets.json")
EXPRESSION_MULTIPLIERS = {"low": 0.65, "standard": 1.0, "rich": 1.2}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class VoiceStyle:
    emotion: str
    intensity: float
    speed: float
    pause_seconds: float
    temperature: float
    top_k: int
    preset_version: int
    reference: str = "neutral"
    # Reserved for a future verified engine/DSP path. The current ONNX graph
    # has no corresponding inputs, so these intentionally remain neutral.
    pitch_shift: float = 0.0
    pitch_variation: float = 1.0
    energy: float = 1.0
    pause_scale: float = 1.0


class VoiceExpressionPolicy:
    def __init__(self, preset_file: Path = PRESET_FILE):
        data = json.loads(preset_file.read_text(encoding="utf-8"))
        self.version = int(data["version"])
        self.presets = data["presets"]
        self.reference_fallbacks = data["reference_fallbacks"]
        if set(self.presets) != set(self.reference_fallbacks):
            raise ValueError("Every emotion needs a reference fallback")

    @property
    def emotions(self) -> tuple[str, ...]:
        return tuple(self.presets)

    def style(
        self, emotion: str, intensity: float = 0.5, expression: str = "standard"
    ) -> VoiceStyle:
        if emotion not in self.presets:
            emotion = "neutral"
        if expression not in EXPRESSION_MULTIPLIERS:
            expression = "standard"
        strength = clamp(float(intensity) * EXPRESSION_MULTIPLIERS[expression], 0.0, 0.9)
        preset = self.presets[emotion]
        speed = 1.0 + (float(preset["speed_target"]) - 1.0) * strength
        pause = 0.3 + (float(preset["pause_target"]) - 0.3) * strength
        temperature = 1.0 + (float(preset["temperature"]) - 1.0) * strength
        top_k = round(15 + (int(preset["top_k"]) - 15) * strength)
        return VoiceStyle(
            emotion=emotion,
            intensity=strength,
            speed=round(speed, 3),
            pause_seconds=round(pause, 3),
            temperature=round(temperature, 3),
            top_k=top_k,
            preset_version=self.version,
            pause_scale=round(pause / 0.3, 3),
        )

    def reference_for(self, emotion: str, available: set[str]) -> str:
        for candidate in self.reference_fallbacks.get(emotion, ["neutral"]):
            if candidate in available:
                return candidate
        raise ValueError("No neutral reference available")
