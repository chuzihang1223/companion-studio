"""Drafts live in SQLite, separate from files in a user's selected project."""

from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import DateTime, ForeignKey, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database.session import Base


class VirtualCodeDocument(Base):
    __tablename__ = "virtual_code_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("work_sessions.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    language: Mapped[str] = mapped_column(String(30), default="text")
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="generated")
    related_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
