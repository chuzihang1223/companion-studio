import json
from urllib.parse import urlsplit

import httpx

from app.services.llm.base import LLMProvider
from app.services.llm.test_budget import record_test_request
from app.services.llm.usage import before_request, observe_response


class LLMConfigurationError(Exception):
    pass


class LLMResponseError(Exception):
    pass


class LLMAuthenticationError(LLMResponseError):
    pass


class LLMHTTPError(LLMResponseError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class LLMTimeoutError(LLMResponseError):
    pass


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 60,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _payload(self, messages: list[dict[str, str]], *, stream: bool = False) -> dict:
        body: dict = {"model": self.model, "messages": messages}
        if stream:
            body["stream"] = True
            # Official DeepSeek Flash now defaults to thinking. Companion text
            # streaming uses its documented non-thinking mode to begin promptly;
            # tool planning and other compatible endpoints retain their defaults.
            if urlsplit(self.base_url).hostname == "api.deepseek.com" and self.model in {
                "deepseek-flash",
                "deepseek-v4-flash",
                "deepseek-v4-flash-vision-exp",
            }:
                body["thinking"] = {"type": "disabled"}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        return body

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _client(self) -> httpx.AsyncClient:
        # System proxy settings can route a loopback Ollama endpoint elsewhere.
        # Local models must stay on this machine even when a proxy is configured.
        local = urlsplit(self.base_url).hostname in {"localhost", "127.0.0.1", "::1"}
        return httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport, trust_env=not local
        )

    def _validate(self) -> None:
        if not self.model or (
            not self.api_key
            and not self.base_url.startswith(("http://127.0.0.1:", "http://localhost:"))
        ):
            raise LLMConfigurationError("请先在设置中填写模型名称和 API 密钥，或配置本地模型服务")

    async def chat(self, messages: list[dict[str, str]]) -> str:
        self._validate()
        record_test_request(self.base_url)
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=before_request(self._payload(messages)),
                )
                response.raise_for_status()
                data = response.json()
                observe_response(data)
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Empty model response")
                return content.strip()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise LLMAuthenticationError("HTTP 401: API Key 无效") from exc
            raise LLMHTTPError(exc.response.status_code) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("模型请求超时") from exc
        except (httpx.RequestError, KeyError, IndexError, ValueError) as exc:
            raise LLMResponseError("The model request failed") from exc

    async def plan_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        """Return the raw assistant tool-call message; never infer a call from prose."""
        self._validate()
        record_test_request(self.base_url)
        body = self._payload(messages)
        body.update({"tools": tools, "tool_choice": "auto"})
        before_request(body)
        try:
            async with self._client() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=body
                )
                if response.status_code in (400, 404, 422):
                    raise LLMResponseError(
                        "当前模型或 API 不支持工具调用，请切换模型或使用聊天模式。"
                    )
                response.raise_for_status()
                data = response.json()
                observe_response(data)
                message = data["choices"][0]["message"]
                if not isinstance(message, dict):
                    raise ValueError("Invalid tool response")
                return message
        except httpx.HTTPStatusError as exc:
            raise LLMHTTPError(exc.response.status_code) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("模型工具规划超时") from exc
        except (httpx.RequestError, KeyError, IndexError, ValueError) as exc:
            raise LLMResponseError("模型工具规划失败") from exc

    async def stream_chat(self, messages: list[dict[str, str]]):
        self._validate()
        record_test_request(self.base_url)
        try:
            async with self._client() as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=before_request(self._payload(messages, stream=True)),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        if not data:
                            continue
                        payload = json.loads(data)
                        for choice in payload.get("choices", []):
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                yield content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise LLMAuthenticationError("HTTP 401: API Key 无效") from exc
            raise LLMHTTPError(exc.response.status_code) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("模型请求超时") from exc
        except (httpx.RequestError, ValueError, KeyError, IndexError) as exc:
            raise LLMResponseError("The model stream failed") from exc

    async def list_models(self) -> list[str]:
        self._validate()
        try:
            async with self._client() as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
                response.raise_for_status()
                return sorted(
                    str(item["id"]) for item in response.json().get("data", []) if item.get("id")
                )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise LLMAuthenticationError("HTTP 401: API Key 无效") from exc
            raise LLMHTTPError(exc.response.status_code) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("模型列表请求超时") from exc
        except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
            raise LLMResponseError("无法读取模型列表") from exc
