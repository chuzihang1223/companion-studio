from app.core.config import settings
from app.services.llm.base import LLMProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.configuration import LLMConfig


class LLMService:
    def __init__(self, provider: LLMProvider, config: LLMConfig | None = None):
        self.provider = provider
        self.config = config
        self.last_error = False
        self.availability_checked = False
        self.observed_capabilities = {}

    async def chat(self, messages: list[dict[str, str]]) -> str:
        try:
            result = await self.provider.chat(messages)
            self.observed_capabilities['text'] = True
            self.last_error = False
            return result
        except Exception:
            self.last_error = True
            raise
        finally:
            self.availability_checked = True

    async def stream_chat(self, messages: list[dict[str, str]]):
        if self.config and self.config.capabilities.get('streaming') is False:
            yield await self.chat(messages)
            return
        try:
            async for delta in self.provider.stream_chat(messages):
                self.observed_capabilities.update(text=True,streaming=True)
                self.last_error = False
                yield delta
        except Exception:
            self.last_error = True
            raise
        finally:
            self.availability_checked = True

    async def plan_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        if not hasattr(self.provider, "plan_tools"):
            raise ValueError("当前模型不支持工具调用")
        try:
            result = await self.provider.plan_tools(messages, tools)
            self.last_error = False
            return result
        except Exception:
            self.last_error = True
            raise
        finally:
            self.availability_checked = True


def create_llm_service(config: LLMConfig | None = None) -> LLMService:
    current = config or LLMConfig.from_environment()
    provider_model = current.model if config is not None else settings.openai_model.strip()
    return LLMService(OpenAIProvider(current.api_key, provider_model, current.base_url,
                                     temperature=current.temperature, max_tokens=current.max_tokens,
                                     timeout=current.timeout_seconds), current)
