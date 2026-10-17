"""Bounded knowledge contracts shared by API, tools, workflows and evaluation."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORMATS = {
    ".md",
    ".txt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".pdf",
    ".docx",
}
EXCLUDED = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
    "cache",
    ".cache",
    ".pytest_cache",
    ".next",
}
ACTIVE = {"PENDING", "SCANNING", "PARSING", "CHUNKING", "INDEXING", "PAUSED"}


class KnowledgeError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CollectionConfig(Strict):
    allowed_types: list[str] = Field(default_factory=lambda: sorted(FORMATS), max_length=12)
    excluded_names: list[str] = Field(default_factory=lambda: sorted(EXCLUDED), max_length=60)
    include_globs: list[str] = Field(default_factory=list, max_length=20)
    exclude_globs: list[str] = Field(default_factory=list, max_length=20)
    max_file_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    max_documents: int = Field(default=2000, ge=1, le=10000)
    max_chunks: int = Field(default=20000, ge=1, le=100000)
    chunk_characters: int = Field(default=1000, ge=200, le=2000)
    overlap: int = Field(default=100, ge=0, le=150)
    embedding_mode: Literal["DISABLED", "LOCAL", "REMOTE"] = "DISABLED"

    @field_validator("allowed_types")
    @classmethod
    def formats(cls, values):
        if not set(values) <= FORMATS:
            raise ValueError("不支持的文档格式")
        return list(dict.fromkeys(values))

    @field_validator("excluded_names", "include_globs", "exclude_globs")
    @classmethod
    def safe_patterns(cls, values):
        if any(
            not v
            or len(v) > 150
            or "\x00" in v
            or ":" in v
            or v.startswith(("/", "\\"))
            or ".." in v.split("/")
            for v in values
        ):
            raise ValueError("无效过滤条件")
        return values


class CreateCollection(Strict):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    config: CollectionConfig = Field(default_factory=CollectionConfig)


class SearchQuery(Strict):
    query: str = Field(min_length=1, max_length=300)
    collection_ids: list[UUID] = Field(min_length=1, max_length=10)
    top_k: int = Field(default=5, ge=1, le=20)
    mode: Literal["keyword", "hybrid", "vector"] = "keyword"
    file_types: list[str] = Field(default_factory=list, max_length=12)
    path_scope: str = Field(default="", max_length=400)
    min_relevance: float = Field(default=0, ge=0, le=1)

    @field_validator("path_scope")
    @classmethod
    def relative_scope(cls, value):
        if (
            "\x00" in value
            or ":" in value
            or value.startswith(("/", "\\"))
            or ".." in value.replace("\\", "/").split("/")
        ):
            raise ValueError("路径范围必须为库内相对路径")
        return value.replace("\\", "/")

    @field_validator("file_types")
    @classmethod
    def supported(cls, values):
        if not set(values) <= FORMATS:
            raise ValueError("不支持的格式筛选")
        return values
