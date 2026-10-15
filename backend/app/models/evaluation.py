"""评测索引与单次评分；实际 Agent/Trace 位于每次 Attempt 的独立库。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True, default="QUEUED")
    mode: Mapped[str] = mapped_column(String(20), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text)
    request_json: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    total: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvaluationAttempt(Base):
    __tablename__ = "evaluation_attempts"
    __table_args__ = (UniqueConstraint("run_id", "case_id", "repetition_index"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    case_id: Mapped[str] = mapped_column(String(20), index=True)
    repetition_index: Mapped[int] = mapped_column(Integer)
    execution_status: Mapped[str] = mapped_column(String(20), index=True, default="NOT_RUN")
    grader_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    work_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
