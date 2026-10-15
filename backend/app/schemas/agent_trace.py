"""Stable read DTOs. No raw WorkEvent detail is part of this contract."""

from typing import Literal, TypeAlias
from pydantic import BaseModel, Field

TraceStatus: TypeAlias = Literal[
    "planning",
    "ready",
    "queued",
    "running",
    "paused",
    "waiting_permission",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]
TraceMetadata: TypeAlias = dict[str, str | int | float | bool | None | dict[str, str]]


class TraceEvidence(BaseModel):
    kind: Literal["boolean", "count"]
    name: str
    value: bool | int


class AgentTrace(BaseModel):
    trace_id: str
    task_id: str
    conversation_id: str | None
    work_session_id: str | None
    status: TraceStatus
    started_at: str
    completed_at: str | None
    duration_ms: float | None
    event_count: int
    span_count: int
    dropped_events: int
    incomplete: bool
    schema_version: int = 1


class TraceSpan(BaseModel):
    span_id: str
    parent_span_id: str | None
    kind: str
    phase: str
    tool_name: str | None
    model_name: str | None
    started_at: str
    completed_at: str | None
    duration_ms: float | None
    status: str
    error_code: str | None
    error_summary: str | None
    retry_count: int
    verification_status: str
    metadata: TraceMetadata = Field(default_factory=dict)
    evidence: list[TraceEvidence] = Field(default_factory=list)


class TraceEvent(BaseModel):
    sequence: int
    trace_id: str
    task_id: str
    work_session_id: str | None
    conversation_id: str | None
    span_id: str | None
    event_type: str
    phase: str
    status: str
    created_at: str
    duration_ms: float | None
    error_code: str | None
    error_summary: str | None
    verification_status: str
    metadata: TraceMetadata = Field(default_factory=dict)
    evidence: list[TraceEvidence] = Field(default_factory=list)
    span: TraceSpan | None = None
    source_work_event_id: int | None = None
    schema_version: int = 1


class TracePage(BaseModel):
    items: list[AgentTrace]
    next_cursor: int | None
    has_more: bool
    limit: int
    schema_version: int = 1


class TraceEventPage(BaseModel):
    items: list[TraceEvent]
    next_cursor: int | None
    has_more: bool
    limit: int
    schema_version: int = 1


class TraceSummary(BaseModel):
    trace_id: str
    task_total_ms: float | None
    model_ms: float
    tool_ms: float
    scheduler_ms: float
    permission_wait_ms: float
    recovery_ms: float
    model_requests: int
    tool_requests: int
    tools_executed: int
    verification_passes: int
    verification_failures: int
    retries: int
    incomplete: bool
    schema_version: int = 1
