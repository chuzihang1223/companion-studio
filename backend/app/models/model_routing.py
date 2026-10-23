"""Additive, content-free model request reservations and observed usage."""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database.session import Base


class ModelRequestRow(Base):
    __tablename__ = "model_routing_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(80), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    provider_ref: Mapped[str] = mapped_column(String(64), index=True)
    model_id: Mapped[str] = mapped_column(String(80), index=True)
    config_version: Mapped[str] = mapped_column(String(24))
    currency: Mapped[str] = mapped_column(String(3))
    reserved_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_micros: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RESERVED")
    usage_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
