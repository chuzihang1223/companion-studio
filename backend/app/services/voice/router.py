"""Select one provider for a whole message; never mix voices across segments."""

from .fish_provider import FishAudioError, FishAudioProvider


class VoiceRouter:
    def __init__(self, index, fish: FishAudioProvider):
        self.index = index
        self.fish = fish
        # Stay on the proven local engine until a real Fish authorization and
        # playback check have been completed by the user.
        self.selected_engine = "index"

    @property
    def name(self) -> str:
        return self.choose().name

    @property
    def ready(self) -> bool:
        return self.choose().ready

    @property
    def available(self) -> bool:
        return self.choose().available

    @property
    def error(self) -> str:
        return self.choose().error

    @property
    def status(self) -> str:
        return self.choose().status

    @property
    def ready_info(self) -> dict:
        return self.choose().ready_info

    def choose(self):
        if self.selected_engine == "index":
            return self.index
        if self.fish.ready:
            return self.fish
        return self.index if self.fish.config.fallback_enabled else self.fish

    def fallback(self, current, error: Exception):
        if current is not self.fish or not self.fish.config.fallback_enabled:
            return None
        if isinstance(error, FishAudioError) and error.status_code in {0, 500, 502, 503, 504}:
            return self.index
        return None

    def start_background(self) -> None:
        self.index.start_background()

    def stop(self) -> None:
        self.index.stop()

    def restart(self) -> None:
        self.index.restart()

    def synthesize(self, text, control) -> dict:
        return self.choose().synthesize(text, control)
