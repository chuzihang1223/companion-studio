"""Untrusted model and renderer input never carries executable paths or coordinates."""

from typing import Literal
from uuid import UUID
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ComputerUseError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


class Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False, hide_input_in_errors=True
    )


SENSITIVE_INPUT = re.compile(
    r"password|api.?key|access.?token|secret|密码|密钥|验证码|银行卡|sk-[a-z0-9_-]{12,}|github_pat_[a-z0-9_]{12,}|gh[pousr]_[a-z0-9]{12,}|bearer\s+\S{8,}|\beyJ[a-z0-9_-]{12,}\.|(?:\d[ -]?){13,19}|-----BEGIN .*PRIVATE KEY",
    re.I,
)


class Rectangle(Strict):
    x: float = Field(ge=-131072, le=131072)
    y: float = Field(ge=-131072, le=131072)
    width: float = Field(gt=0, le=16384)
    height: float = Field(gt=0, le=16384)


CONTROL_TYPES = Literal[
    "Button",
    "Edit",
    "Text",
    "List",
    "ListItem",
    "Menu",
    "MenuItem",
    "Tree",
    "TreeItem",
    "Pane",
    "CheckBox",
    "ComboBox",
    "Window",
]


class Locator(Strict):
    description: str = Field(min_length=1, max_length=180)
    automation_id: str | None = Field(default=None, min_length=1, max_length=120)
    control_type: CONTROL_TYPES | None = None
    matching: Literal["exact", "normalized", "limited_fuzzy"] = "exact"
    source: Literal["auto", "uia", "dom", "ocr"] = "auto"


class Verification(Strict):
    model_config = ConfigDict(str_strip_whitespace=False)
    kind: Literal[
        "element_present",
        "element_absent",
        "text_equals",
        "text_contains",
        "window_focused",
        "scroll_changed",
    ]
    automation_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def complete(self):
        if self.kind != "window_focused" and not self.automation_id:
            raise ValueError("COMPUTER_VERIFICATION_TARGET_REQUIRED")
        if self.kind in {"text_equals", "text_contains"} and not self.expected:
            raise ValueError("COMPUTER_VERIFICATION_TEXT_REQUIRED")
        return self


class Crop(Strict):
    x: int = Field(ge=0, le=4096, strict=True)
    y: int = Field(ge=0, le=4096, strict=True)
    width: int = Field(gt=0, le=4096, strict=True)
    height: int = Field(gt=0, le=4096, strict=True)


class Action(Strict):
    model_config = ConfigDict(str_strip_whitespace=False)
    target_id: UUID
    observation_id: UUID | None = None
    candidate_id: UUID | None = None
    locator: Locator | None = None
    text: str | None = Field(default=None, max_length=500)
    direction: Literal["up", "down", "left", "right"] | None = None
    verification: Verification | None = None
    timeout_seconds: float = Field(default=5, ge=0.2, le=12)
    capture_scope: Literal["window", "region", "display"] = "window"
    region: Crop | None = None
    display_id: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def capture_scope_complete(self):
        # Reject recognizable secrets *before* any UI, model or Workflow entry
        # can save its tool plan. The native worker repeats this defense.
        texts = [self.text, self.verification.expected if self.verification else None]
        if self.locator:
            texts.extend([self.locator.description, self.locator.automation_id])
        if any(SENSITIVE_INPUT.search(value) for value in texts if value):
            raise ValueError("COMPUTER_SENSITIVE_INPUT_UNSUPPORTED")
        if self.capture_scope == "region" and self.region is None:
            raise ValueError("COMPUTER_CAPTURE_REGION_REQUIRED")
        if self.capture_scope == "display" and not self.display_id:
            raise ValueError("COMPUTER_CAPTURE_DISPLAY_REQUIRED")
        if self.capture_scope != "region" and self.region is not None:
            raise ValueError("COMPUTER_CAPTURE_SCOPE_INVALID")
        return self


class Owner(Strict):
    conversation_id: UUID
    character_id: str = Field(min_length=1, max_length=100)


class ExplicitAction(Owner):
    operation: Literal[
        "observe", "find_element", "click", "type_text", "scroll", "focus_window", "wait_for_state"
    ]
    arguments: Action

    @model_validator(mode="after")
    def action_requirements(self):
        value = self.arguments
        if self.operation == "find_element" and not value.locator:
            raise ValueError("COMPUTER_LOCATOR_REQUIRED")
        if self.operation in {"click", "type_text", "scroll"} and not (
            value.observation_id and value.candidate_id
        ):
            raise ValueError("COMPUTER_FRESH_CANDIDATE_REQUIRED")
        if self.operation in {"click", "wait_for_state"} and not value.verification:
            raise ValueError("COMPUTER_VERIFICATION_REQUIRED")
        if self.operation == "type_text" and not value.text:
            raise ValueError("COMPUTER_INPUT_REQUIRED")
        if self.operation == "scroll" and not value.direction:
            raise ValueError("COMPUTER_SCROLL_DIRECTION_REQUIRED")
        return self
