from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict[str, str]]) -> str:
        raise NotImplementedError

    async def stream_chat(self, messages: list[dict[str, str]]):
        """Fallback for providers without a native streaming endpoint."""
        yield await self.chat(messages)
