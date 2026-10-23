"""Validated routing metadata. Credentials stay in the existing LLMConfig service."""

from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class RoutingError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ModelType(StrEnum):
    TEXT_LLM = "TEXT_LLM"
    VISION_LLM = "VISION_LLM"
    EMBEDDING = "EMBEDDING"
    ASR = "ASR"
    TTS = "TTS"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value, info):
        numeric = info.field_name.startswith(
            ("max_", "input_cost", "output_cost")
        ) or info.field_name in {"priority", "context_window", "min_context"}
        if numeric and isinstance(value, bool):
            raise ValueError("Boolean values are not numeric limits")
        return value


class ModelRecord(Strict):
    model_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    display_name: str = Field(min_length=1, max_length=80)
    model_type: ModelType = ModelType.TEXT_LLM
    enabled: bool = True
    allow_auto: bool = False
    allow_remote_text: bool = False
    allow_attachments: bool = False
    context_window: int | None = Field(default=None, ge=256, le=2000000)
    input_cost: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    output_cost: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    cost_currency: Literal["USD", "CNY"] = "USD"
    price_source: Literal["UNKNOWN", "USER"] = "UNKNOWN"
    price_verified_at: str | None = Field(default=None, max_length=40)
    priority: int = Field(default=5, ge=0, le=10)
    config_revision: str = Field(default="initial", pattern=r"^[A-Za-z0-9_-]{1,80}$")

    @model_validator(mode="after")
    def prices(self):
        if self.price_source == "UNKNOWN" and (
            self.input_cost is not None or self.output_cost is not None
        ):
            raise ValueError("Unknown prices must not contain a fabricated amount")
        if self.price_source == "USER" and (self.input_cost is None or self.output_cost is None):
            raise ValueError("Set both input and output prices per million tokens")
        if self.price_verified_at is not None:
            # This implementation has no official-price verification service.
            raise ValueError("Manual prices cannot claim provider verification")
        return self


class RoutingPolicy(Strict):
    enabled: bool = False
    mode: Literal["MANUAL", "ROLE_BASED", "AUTO"] = "ROLE_BASED"
    preference: Literal["BALANCED", "LOW_LATENCY", "LOW_COST", "QUALITY_FIRST", "PRIVACY_FIRST"] = (
        "BALANCED"
    )
    manual_model: str | None = Field(default=None, max_length=80)
    fallback_enabled: bool = False
    budget_mode: Literal["STRICT", "ESTIMATED"] = "STRICT"
    max_requests_per_task: int | None = Field(default=None, ge=1, le=1000)
    max_requests_per_workflow: int | None = Field(default=None, ge=1, le=1000)
    max_requests_per_day: int | None = Field(default=None, ge=1, le=10000)
    max_requests_per_provider: int | None = Field(default=None, ge=1, le=10000)
    max_cost_per_request: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    max_cost_per_task: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    max_cost_per_workflow: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    max_cost_per_day: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    max_cost_per_provider: float | None = Field(default=None, ge=0, le=1000000, allow_inf_nan=False)
    cost_currency: Literal["USD", "CNY"] = "USD"
    max_output_tokens: int = Field(default=2048, ge=1, le=8192)


TaskKind = Literal[
    "CHAT",
    "PLANNING",
    "EXECUTION",
    "CODING",
    "RAG",
    "STRUCTURED",
    "VISION",
    "WORKFLOW",
    "SCHEDULED",
    "VOICE",
    "EMBEDDING",
    "ASR",
    "TTS",
]


class Requirements(Strict):
    task: TaskKind = "CHAT"
    required: frozenset[str] = frozenset({"text"})
    preferred: frozenset[str] = frozenset()
    data_kind: Literal["TEXT", "ATTACHMENT"] = "TEXT"
    min_context: int = Field(default=256, ge=1, le=2000000)
    privacy: Literal["APPROVED_TEXT", "PINNED", "LOCAL_ONLY"] = "APPROVED_TEXT"
    pinned_model_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
    max_latency_ms: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class TaskCapabilityResolver:
    @staticmethod
    def resolve(
        task="CHAT",
        *,
        tools=False,
        min_context=256,
        privacy="APPROVED_TEXT",
        vision=False,
        data_kind="TEXT",
    ):
        required = {"text"}
        preferred = set()
        if task in {"PLANNING", "EXECUTION", "CODING"} or tools:
            required.add("tool_calling")
        if task in {"PLANNING", "CODING"}:
            preferred.add("reasoning")
        if task == "CODING":
            preferred.add("coding")
        if task == "VISION" or vision:
            required.add("vision")
        if task == "VOICE":
            required.add("streaming")
        if task == "STRUCTURED":
            required.add("structured_output")
        if task in {"EMBEDDING", "ASR", "TTS"}:
            required = {task.lower()}
        return Requirements(
            task=task,
            required=frozenset(required),
            preferred=frozenset(preferred),
            min_context=min_context,
            privacy=privacy,
            data_kind=data_kind,
        )
