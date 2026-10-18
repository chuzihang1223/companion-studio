"""Public workflow protocol, independent of the canvas implementation."""

import json
import re
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

NODE_TYPES = {
    "knowledge_search",
    "manual_trigger",
    "input",
    "llm_agent",
    "tool",
    "mcp_tool",
    "agent_skill",
    "condition",
    "transform",
    "human_approval",
    "merge",
    "output",
}
VALUE_TYPES = {"any", "string", "number", "boolean", "object", "array", "file_reference"}
TERMINAL = {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED", "INTERRUPTED", "NEEDS_REVIEW"}
STATES = TERMINAL | {"PENDING", "READY", "RUNNING", "WAITING_PERMISSION", "PAUSED"}
CONFIG_KEYS = {
    "knowledge_search": {
        "query",
        "collection_ids",
        "top_k",
        "mode",
        "file_types",
        "path_scope",
        "min_relevance",
    },
    "manual_trigger": set(),
    "input": {"name", "type", "required", "default"},
    "llm_agent": {"instruction", "value", "output_schema", "model_role", "max_characters"},
    "tool": {"name", "arguments", "coding_mode"},
    "mcp_tool": {"tool_id", "server_id", "schema_fingerprint", "arguments"},
    "agent_skill": {"skill_id", "content_hash", "goal", "value", "output_schema"},
    "condition": {"expression"},
    "transform": {"operation", "value", "fields", "separator", "default", "path"},
    "human_approval": {"message", "value"},
    "merge": {"mode"},
    "output": {"value", "format"},
}


class WorkflowError(ValueError):
    def __init__(self, code, node_id=None, *, status=422):
        self.code, self.node_id, self.status = code, node_id, status
        super().__init__(code)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Position(Strict):
    x: float = Field(default=80, ge=-10000, le=10000)
    y: float = Field(default=80, ge=-10000, le=10000)


class RetryPolicy(Strict):
    max_attempts: int = Field(default=1, ge=1, le=3)
    # Effectful retry decisions belong exclusively to the existing RecoveryPolicy.
    strategy: Literal["stop", "safe_read"] = "stop"


class Node(Strict):
    node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    type: str
    title: str = Field(default="节点", min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    position: Position = Field(default_factory=Position)
    input_ports: dict[str, str] = Field(default_factory=lambda: {"in": "any"}, max_length=16)
    output_ports: dict[str, str] = Field(default_factory=lambda: {"out": "any"}, max_length=16)
    config: dict[str, Any] = Field(default_factory=dict)
    timeout: float = Field(default=60, ge=0.05, le=300)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    permission_requirement: Literal["runtime"] = "runtime"
    metadata: dict[str, str] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def known_config(self):
        if self.node_id in {
            "constructor",
            "prototype",
            "toString",
            "toLocaleString",
            "valueOf",
            "hasOwnProperty",
            "isPrototypeOf",
            "propertyIsEnumerable",
        }:
            raise ValueError("节点编号为保留标识")
        if self.type not in NODE_TYPES or set(self.config) - CONFIG_KEYS[self.type]:
            raise ValueError("未知节点类型或配置字段")
        for ports in (self.input_ports, self.output_ports):
            if any(
                not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", p) or t not in VALUE_TYPES
                for p, t in ports.items()
            ):
                raise ValueError("端口协议无效")
        if self.type == "condition":
            self.output_ports = {"true": "boolean", "false": "boolean", "error": "object"}
        return self


class Edge(Strict):
    edge_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    source_node_id: str
    source_port: str = "out"
    target_node_id: str
    target_port: str = "in"
    condition_label: Literal["", "true", "false", "error"] = ""


class Budget(Strict):
    max_steps: int = Field(default=100, ge=1, le=100)
    max_duration_seconds: float = Field(default=600, ge=0.1, le=1800)
    max_model_calls: int = Field(default=10, ge=0, le=20)
    max_total_tokens: int = Field(default=32768, ge=256, le=131072)
    max_tool_calls: int = Field(default=40, ge=0, le=100)
    max_intermediate_bytes: int = Field(default=262144, ge=1024, le=1048576)


class Definition(Strict):
    workflow_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(default="新工作流", min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    schema_version: Literal[1] = 1
    version: int = Field(default=0, ge=0)
    status: Literal["DRAFT", "PUBLISHED", "ARCHIVED"] = "DRAFT"
    nodes: list[Node] = Field(default_factory=list, max_length=100)
    edges: list[Edge] = Field(default_factory=list, max_length=200)
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False}
    )
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode="after")
    def safe_document(self):
        UUID(self.workflow_id)
        bounded(self.model_dump(mode="json"), limit=512000)
        return self


SECRET_KEY = re.compile(
    r"(?i)(password|api[_-]?key|access[_-]?token|authorization|private[_-]?key|credential|secret)"
)
SECRET_VALUE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{24,}|\bgithub_pat_[a-z0-9_]{24,}|\bgh[pousr]_[a-z0-9]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bbearer\s+[a-z0-9_.-]{24,})"
)


def bounded(value, *, limit=262144, depth=0):
    """User data is local/private; credentials are never valid graph/run variables."""
    if depth > 16:
        raise WorkflowError("WORKFLOW_DEPTH_LIMIT")
    if depth == 0 and len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > limit:
        raise WorkflowError("WORKFLOW_SIZE_LIMIT")
    if isinstance(value, dict):
        if len(value) > 200:
            raise WorkflowError("WORKFLOW_SIZE_LIMIT")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or len(key) > 100
                or SECRET_KEY.search(key)
                or key in {"__proto__", "constructor", "prototype"}
            ):
                raise WorkflowError("WORKFLOW_SENSITIVE_FIELD")
            bounded(item, limit=limit, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise WorkflowError("WORKFLOW_SIZE_LIMIT")
        for item in value:
            bounded(item, limit=limit, depth=depth + 1)
    elif isinstance(value, str) and SECRET_VALUE.search(value):
        raise WorkflowError("WORKFLOW_SENSITIVE_VALUE")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise WorkflowError("WORKFLOW_VALUE_INVALID")
    return value
