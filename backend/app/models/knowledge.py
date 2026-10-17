"""Local knowledge metadata. Source files and profiles are never exported by this model."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


def identity():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class KnowledgeCollection(Base):
    __tablename__ = "knowledge_collections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identity)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    index_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="EMPTY")
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    collection_id: Mapped[str] = mapped_column(ForeignKey("knowledge_collections.id"), index=True)
    root_id: Mapped[str] = mapped_column(String(36))
    relative_path: Mapped[str] = mapped_column(Text)
    file_type: Mapped[str] = mapped_column(String(10))
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    mtime_ns: Mapped[str] = mapped_column(String(30), default="0")
    parser_version: Mapped[str] = mapped_column(String(30), default="knowledge-1")
    version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="NEW")
    last_error: Mapped[str | None] = mapped_column(String(80))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    collection_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    location_json: Mapped[str] = mapped_column(Text)


class KnowledgeIndexJob(Base):
    __tablename__ = "knowledge_index_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identity)
    collection_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(30), default="PENDING")
    revision: Mapped[int] = mapped_column(Integer)
    progress_json: Mapped[str] = mapped_column(Text, default="{}")
    last_error: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class KnowledgeGrant(Base):
    __tablename__ = "knowledge_grants"
    # A collection ID alone is never an authorization to read it.
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identity)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    character_id: Mapped[str] = mapped_column(String(100))
    collection_id: Mapped[str] = mapped_column(String(36), index=True)
    model_targets_json: Mapped[str] = mapped_column(Text, default="[]")


class KnowledgePreference(Base):
    __tablename__ = "knowledge_preferences"
    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeEvidence(Base):
    __tablename__ = "knowledge_evidence"
    message_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    references_json: Mapped[str] = mapped_column(Text)
    model_target: Mapped[str] = mapped_column(String(64))


class KnowledgeVector(Base):
    __tablename__ = "knowledge_vectors"
    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    # JSON is deliberately inspectable and bounded; no executable pickle/model objects.
    vector_json: Mapped[str] = mapped_column(Text)
