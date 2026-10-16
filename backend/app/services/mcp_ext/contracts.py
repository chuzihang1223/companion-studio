"""Bounded owner-supplied configuration; credentials are references, never values."""

import hashlib
import json
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MCPError(ValueError):
    def __init__(self, code="MCP_RESULT_INVALID", status=409):
        self.code, self.status = code, status
        super().__init__(code)


class ConnectionStatus(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    display_name: str = Field(min_length=1, max_length=80)
    transport: Literal["stdio", "http"]
    endpoint_or_command: str = Field(min_length=1, max_length=2048)
    args: list[str] = Field(default_factory=list, max_length=32)
    enabled: bool = False
    allow_loopback: bool = False
    # Environment names only. Values arrive through the credential-only endpoint.
    environment_names: list[str] = Field(default_factory=list, max_length=12)
    credential_reference: bool = False
    connect_timeout: int = Field(default=10, ge=2, le=30)
    call_timeout: float = Field(default=20, ge=0.2, le=60)
    protocol_mode: Literal["auto", "legacy", "2026-07-28"] = "auto"

    @field_validator("args")
    @classmethod
    def bounded_args(cls, values):
        if any(
            len(s) > 1024
            or "\x00" in s
            or re.search(
                r"(?i)(?:--?(?:api[-_]?key|token|password|secret)|bearer\s|sk-[a-z0-9]{12})", s
            )
            for s in values
        ):
            raise ValueError("MCP_CONFIG_INVALID")
        return values

    @field_validator("environment_names")
    @classmethod
    def safe_environment(cls, values):
        # No PATH, interpreter hooks, loader flags or proxy overrides.
        if any(
            not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", s)
            or s in {"PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "WINDIR"}
            or s.startswith(("PYTHON", "NODE_", "LD_", "DYLD_", "HTTP_", "HTTPS_", "ALL_PROXY"))
            for s in values
        ):
            raise ValueError("MCP_ENVIRONMENT_INVALID")
        return values

    @model_validator(mode="after")
    def consistent(self):
        if self.transport == "http" and (self.args or self.environment_names):
            raise ValueError("MCP_CONFIG_INVALID")
        return self


RISKS = {
    "READ_ONLY_LOCAL",
    "READ_ONLY_EXTERNAL",
    "WRITE_LOCAL",
    "WRITE_EXTERNAL",
    "PROCESS_EXECUTION",
    "SENSITIVE_DATA_ACCESS",
    "HIGH_RISK_ACTION",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def bounded_json(value, *, limit=131072, depth=10):
    def visit(item, level):
        if level > depth:
            raise MCPError()
        if isinstance(item, dict):
            if len(item) > 256:
                raise MCPError()
            for child in item.values():
                visit(child, level + 1)
        elif isinstance(item, list):
            if len(item) > 256:
                raise MCPError()
            for child in item:
                visit(child, level + 1)

    visit(value, 0)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > limit:
        raise MCPError()
    return value
