from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class VoiceJob(Base):
    __tablename__ = "voice_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("messages.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    engine: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model_version: Mapped[str] = mapped_column(String(60), default="studio-indextts-v1")
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    segment_paths_json: Mapped[str] = mapped_column(Text, default="[]")
    segments_total: Mapped[int] = mapped_column(Integer, default=0)
    segments_done: Mapped[int] = mapped_column(Integer, default=0)
    queue_wait_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_load_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    synthesis_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    save_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expression: Mapped[str] = mapped_column(String(20), default="natural")
    speed_factor: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
