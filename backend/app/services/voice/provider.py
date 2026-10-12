"""Voice engine contract shared by offline evaluation and future live serving."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .prosody_planner import EngineControl


@dataclass(frozen=True)
class VoiceAudio:
    samples: Any  # float32 mono ndarray in engine environments
    sample_rate: int


class VoiceProvider(ABC):
    name: str

    @abstractmethod
    def warm(self) -> None:
        """Load weights and reusable speaker conditioning once."""

    @abstractmethod
    def synthesize(self, text: str, control: EngineControl) -> VoiceAudio:
        """Synthesize one semantic phrase with no text added or removed."""
