"""Conservative speech text cleanup and sentence segmentation."""

from dataclasses import dataclass
import re


STAGE_CUE = re.compile(
    r"(?:\[(?:开心|温柔|严肃|悲伤|担心|笑|叹气)\]|（(?:开心|温柔|严肃|悲伤|担心|笑|叹气)）)"
)
SENTENCE_END = re.compile(r"(?<=[。！？!?])(?=\s*[^。！？!?])")


@dataclass(frozen=True)
class SpeechSegment:
    text: str
    emotion: str | None = None
    intensity: float | None = None


class ExpressiveTextProcessor:
    def clean(self, text: str) -> str:
        return re.sub(r"\s+", " ", STAGE_CUE.sub("", text)).strip()

    def segments(self, text: str) -> list[SpeechSegment]:
        cleaned = self.clean(text)
        return [SpeechSegment(part.strip()) for part in SENTENCE_END.split(cleaned) if part.strip()]
