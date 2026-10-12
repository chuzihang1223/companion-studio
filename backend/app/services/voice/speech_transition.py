"""Keep neighboring utterances from changing intensity abruptly."""

from .speech_plan import PreviousVoiceState


class SpeechTransitionManager:
    def intensity(self, desired: float, previous: PreviousVoiceState | None) -> float:
        if previous is None:
            return desired
        # A safety or worry turn may increase immediately; it must never be
        # smoothed into a cheerful response by the previous sentence.
        return max(previous.intensity - 0.24, min(previous.intensity + 0.24, desired))
