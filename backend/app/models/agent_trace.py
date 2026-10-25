"""Additive trace projection; no prompts, raw tool results or screenshots."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentTrace(Base):
    __tablename__ = "agent_traces"
    __table_args__ = (
        Index("ix_trace_conversation_time", "conversation_id", "started_at"),
        Index("ix_trace_session_time", "work_session_id", "started_at"),
        Index("ix_trace_status_time", "status", "started_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), unique=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    work_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="planning")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    span_count: Mapped[int] = mapped_column(Integer, default=0)
    dropped_events: Mapped[int] = mapped_column(Integer, default=0)
    incomplete: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


class TraceSpan(Base):
    __tablename__ = "trace_spans"
    __table_args__ = (Index("ix_span_trace_start", "trace_id", "started_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_traces.id"), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(24))
    phase: Mapped[str] = mapped_column(String(32))
    tool_name: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    model_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    verification_status: Mapped[str] = mapped_column(String(24), default="not_provided")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")


class TraceEvent(Base):
    __tablename__ = "trace_events"
    __table_args__ = (Index("ix_trace_sequence", "trace_id", "sequence", unique=True),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_traces.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_work_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(40))
    phase: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    data_json: Mapped[str] = mapped_column(Text, default="{}")


class TraceProjectionReceipt(Base):
    """Committed with its projection, including the commit-before-journal-ACK window.

    No FK to the prunable viewer history: a receipt must outlive a pending source.
    It contains identifiers/digests only, never the original command payload.
    """

    __tablename__ = "trace_projection_receipts"
    __table_args__ = (Index("ix_projection_trace_order", "trace_id", "stream_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(36))
    stream_id: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    event_written: Mapped[bool] = mapped_column(Boolean, default=False)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class TraceProjectionTotals(Base):
    """Small lifetime counters distinguish retained history from lawful pruning."""

    __tablename__ = "trace_projection_totals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    committed_commands: Mapped[int] = mapped_column(Integer, default=0)
    committed_events: Mapped[int] = mapped_column(Integer, default=0)
    legacy_events: Mapped[int] = mapped_column(Integer, default=0)
    recovery_events: Mapped[int] = mapped_column(Integer, default=0)
    pruned_events: Mapped[int] = mapped_column(Integer, default=0)
    pruned_traces: Mapped[int] = mapped_column(Integer, default=0)
    limited_events: Mapped[int] = mapped_column(Integer, default=0)
