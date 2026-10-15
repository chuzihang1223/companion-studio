import asyncio

import httpx
import pytest

from app.services.llm.openai_provider import LLMAuthenticationError, OpenAIProvider


def test_openai_compatible_provider_sends_server_side_key_and_messages():
    captured = {}

    def respond(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "  你好，我在。  "}}]})

    provider = OpenAIProvider(
        "test-only-key", "test-model", "https://example.test/v1", httpx.MockTransport(respond)
    )
    result = asyncio.run(provider.chat([{"role": "user", "content": "你好"}]))
    assert result == "你好，我在。"
    assert captured["path"] == "/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-only-key"
    assert '"model":"test-model"' in captured["body"]


def test_openai_provider_reports_invalid_key_without_exposing_it():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"code": "invalid_api_key"}})
    )
    provider = OpenAIProvider("secret-test-key", "test-model", "https://example.test/v1", transport)
    with pytest.raises(LLMAuthenticationError, match="HTTP 401") as result:
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))
    assert "secret-test-key" not in str(result.value)


def test_openai_compatible_stream_ignores_reasoning_and_yields_visible_text():
    def respond(request: httpx.Request) -> httpx.Response:
        assert b'"stream":true' in request.read()
        events = (
            'data: {"choices":[{"delta":{"reasoning_content":"hidden","content":null}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"真的？"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"我替你开心！"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=events, headers={"Content-Type": "text/event-stream"})

    provider = OpenAIProvider(
        "test-key", "test-model", "https://example.test/v1", httpx.MockTransport(respond)
    )

    async def collect():
        return [
            part async for part in provider.stream_chat([{"role": "user", "content": "通过了"}])
        ]

    assert asyncio.run(collect()) == ["真的？", "我替你开心！"]
