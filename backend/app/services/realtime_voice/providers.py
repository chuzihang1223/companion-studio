"""Actual provider contracts; no implicit remote ASR or invented streaming API."""

from typing import Protocol

from .tts import ConfiguredTTS
from .windows import WindowsSpeech


class ASRProvider(Protocol):
    def capabilities(self) -> dict: ...
    async def initialize(self) -> dict: ...
    async def recognize(self, wav: bytes) -> str: ...
    async def cancel(self) -> None: ...


class TTSProvider(Protocol):
    async def synthesize_live(self, queue, speed, on_pcm): ...
    async def cancel(self) -> None: ...
    async def close(self) -> None: ...


class ASRProviderRouter:
    def create(self, **options) -> ASRProvider:
        # Only installed local Windows speech is offered. Remote audio transport
        # requires a separate provider and permission implementation first.
        return WindowsSpeech(**options)


class TTSProviderRouter:
    @staticmethod
    def create(session, local: WindowsSpeech, router=None) -> TTSProvider:
        return (
            ConfiguredTTS(session.configured_provider, getattr(router, "index", None))
            if session.options.tts_provider == "configured"
            else local
        )
