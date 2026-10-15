"""Private local execution ledger. No credentials, prompts or file bodies are copied."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


def now():
    return datetime.now(timezone.utc)


class RecoveryTask(Base):
    __tablename__ = "agent_recovery_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    work_session_id: Mapped[str] = mapped_column(String(36), index=True)
    renderer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    execution_id: Mapped[str] = mapped_column(String(36))
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    plan_hash: Mapped[str] = mapped_column(String(64))
    workspace_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), default="RECOVERING", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lease: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    failure_json: Mapped[str] = mapped_column(Text, default="{}")
    budget_json: Mapped[str] = mapped_column(Text, default="{}")
    counters_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (UniqueConstraint("task_id", "step_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_recovery_tasks.id"), index=True
    )
    operation_id: Mapped[str] = mapped_column(String(36), unique=True)
    tool_call_id: Mapped[str] = mapped_column(String(100))
    tool_name: Mapped[str] = mapped_column(String(80))
    step_index: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="PREPARED")
    side_effect_class: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    intent_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    permission_state: Mapped[str] = mapped_column(String(24), default="unchecked")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecoveryAttempt(Base):
    __tablename__ = "agent_recovery_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_recovery_tasks.id"), index=True
    )
    step_id: Mapped[str] = mapped_column(String(36))
    strategy: Mapped[str] = mapped_column(String(40))
    error_code: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="STARTED")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
