from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    character_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(100), default="新聊天")
    preferred_mode: Mapped[str] = mapped_column(String(10), default="chat")
    conversation_type: Mapped[str] = mapped_column(String(10), default="chat", index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    work_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_conversation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    fork_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
