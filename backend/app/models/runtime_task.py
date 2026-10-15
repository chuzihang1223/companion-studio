from datetime import datetime, timezone
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database.session import Base


class RuntimeTask(Base):
    __tablename__ = "runtime_tasks"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(String(100), index=True)
    work_session_id: Mapped[str | None] = mapped_column(String(100), index=True)
    renderer_id: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="agent")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    wait_reason: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
