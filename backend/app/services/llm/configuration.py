"""Validated runtime settings for OpenAI-compatible chat endpoints."""

from pydantic import BaseModel, Field, field_validator, model_validator
from urllib.parse import urlsplit

from app.core.config import settings


class LLMConfig(BaseModel):
    provider: str = "openai-compatible"
    base_url: str = Field(max_length=500)
    api_key: str = Field(default="", max_length=500)
    model: str = Field(min_length=1, max_length=150)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)
    timeout_seconds: float = Field(default=60, ge=5, le=180)
    capabilities: dict[str, bool | None] = Field(default_factory=lambda: {"vision": False})

    @field_validator("provider")
    @classmethod
    def supported_provider(cls, value: str) -> str:
        if value not in {"openai-compatible", "openai"}:
            raise ValueError("Only OpenAI-compatible endpoints are supported")
        return "openai-compatible"

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        url = value.rstrip("/")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Base URL must be an HTTP(S) address without credentials")
        if parts.query or parts.fragment:
            raise ValueError("Base URL must not include a query or fragment")
        return url

    @model_validator(mode="after")
    def protect_remote_key(self) -> "LLMConfig":
        parts = urlsplit(self.base_url)
        if self.api_key and parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote API Key requires HTTPS")
        return self

    @classmethod
    def from_environment(cls) -> "LLMConfig":
        # Blank .env values describe an unconfigured installation, not a fatal
        # application error. The provider keeps the actual blank model and fails
        # locally before attempting any request until the user configures one.
        return cls(provider=settings.llm_provider,
                   base_url=settings.openai_base_url.strip() or "https://api.openai.com/v1",
                   api_key=settings.openai_api_key,
                   model=settings.openai_model.strip() or "unconfigured")

    def public(self) -> dict:
        return self.model_dump(exclude={"api_key"}) | {"has_key": bool(self.api_key)}
