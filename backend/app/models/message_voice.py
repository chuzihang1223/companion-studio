from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class MessageVoice(Base):
    __tablename__ = "message_voice"

    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    cache_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    audio_path: Mapped[str | None] = mapped_column(String(250), nullable=True)
    engine: Mapped[str | None] = mapped_column(String(60), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    format: Mapped[str | None] = mapped_column(String(12), nullable=True)
    voice_profile: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    style_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_version: Mapped[str] = mapped_column(String(40), default="studio-v1")
    speech_plan_version: Mapped[str] = mapped_column(String(40), default="speech-v2")
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
