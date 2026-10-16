"""Additive extension metadata. Secret values and imported file bodies live elsewhere."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class ExtensionRecord(Base):
    __tablename__ = "extension_records"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    body: Mapped[str] = mapped_column(Text)
