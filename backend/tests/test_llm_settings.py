from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import llm_settings
from app.services.llm.configuration import LLMConfig
from app.services.llm.openai_provider import LLMAuthenticationError, LLMHTTPError, LLMTimeoutError


class FakeService:
    def __init__(self, config):
        self.config = config

    async def chat(self, messages):
        if self.config.api_key == "wrong":
            raise LLMAuthenticationError("sensitive")
        return "OK"


def test_live_switch_requires_success_and_preserves_working_config(monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "test-control")
    monkeypatch.setattr(llm_settings, "create_llm_service", FakeService)
    app = FastAPI()
    app.include_router(llm_settings.router)
    app.state.llm_service = FakeService(
        LLMConfig(
            provider="openai-compatible", base_url="https://a.test/v1", model="old", api_key="good"
        )
    )
    headers = {"X-Companion-Control-Token": "test-control"}
    new = {
        "provider": "openai-compatible",
        "base_url": "https://b.test/v1",
        "model": "new",
        "api_key": "good",
    }
    with TestClient(app) as client:
        assert client.post("/api/llm/settings/apply", json=new, headers=headers).status_code == 409
        assert (
            client.post(
                "/api/llm/settings/test", json={**new, "api_key": "wrong"}, headers=headers
            ).status_code
            == 401
        )
        assert app.state.llm_service.config.model == "old"
        assert client.post("/api/llm/settings/test", json=new, headers=headers).status_code == 200
        assert client.post("/api/llm/settings/apply", json=new, headers=headers).status_code == 200
        assert app.state.llm_service.config.model == "new"
        old = {**new, "base_url": "https://a.test/v1", "model": "old"}
        assert client.post("/api/llm/settings/test", json=old, headers=headers).status_code == 200
        assert client.post("/api/llm/settings/apply", json=old, headers=headers).status_code == 200
        assert app.state.llm_service.config.model == "old"
        assert client.get("/api/llm/settings").status_code == 403
        assert "good" not in client.get("/api/llm/settings", headers=headers).text


def test_api_settings_explains_common_provider_errors():
    assert llm_settings.public_error(LLMHTTPError(403)).status_code == 403
    assert "模型" in llm_settings.public_error(LLMHTTPError(404)).detail
    assert "配额" in llm_settings.public_error(LLMHTTPError(429)).detail
    assert llm_settings.public_error(LLMTimeoutError()).status_code == 504
