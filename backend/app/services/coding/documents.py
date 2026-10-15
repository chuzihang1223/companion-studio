"""Persistent virtual documents and conflict checked, user requested saves."""

from datetime import datetime, timezone
import difflib
import hashlib
from pathlib import Path
from sqlalchemy import select
from app.database.session import SessionLocal
from app.models import VirtualCodeDocument, WorkSession
from app.services.workspace_paths import scoped_path

LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".md": "markdown",
}


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def serialize(row: VirtualCodeDocument) -> dict:
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "session_id",
            "title",
            "language",
            "content",
            "source",
            "related_file",
            "base_hash",
            "dirty",
            "status",
        )
    } | {"created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


class VirtualDocumentStore:
    def _session(self, db, session_id: str):
        row = db.get(WorkSession, session_id)
        if row is None:
            raise LookupError("工作会话不存在")
        return row

    def get(self, session_id: str, document_id: str) -> dict:
        with SessionLocal() as db:
            session = self._session(db, session_id)
            row = db.get(VirtualCodeDocument, document_id)
            owner = db.get(WorkSession, row.session_id) if row else None
            if (
                not row
                or not owner
                or (
                    owner.id != session.id
                    and (
                        not session.conversation_id
                        or owner.conversation_id != session.conversation_id
                    )
                )
            ):
                raise LookupError("草稿不存在于当前工作会话")
            return serialize(row)

    def latest(self, session_id: str, title: str) -> dict | None:
        with SessionLocal() as db:
            session = self._session(db, session_id)
            query = (
                select(VirtualCodeDocument)
                .join(WorkSession)
                .where(
                    VirtualCodeDocument.title == title,
                    VirtualCodeDocument.dirty.is_(True),
                    VirtualCodeDocument.status == "proposed",
                )
            )
            if session.conversation_id:
                query = query.where(
                    WorkSession.conversation_id == session.conversation_id,
                    WorkSession.workspace_root == session.workspace_root,
                )
            else:
                query = query.where(VirtualCodeDocument.session_id == session_id)
            row = db.scalar(query.order_by(VirtualCodeDocument.updated_at.desc()))
            return serialize(row) if row else None

    def active(self, session_id: str) -> list[dict]:
        with SessionLocal() as db:
            session = self._session(db, session_id)
            query = (
                select(VirtualCodeDocument)
                .join(WorkSession)
                .where(
                    VirtualCodeDocument.dirty.is_(True), VirtualCodeDocument.status == "proposed"
                )
            )
            query = (
                query.where(
                    WorkSession.conversation_id == session.conversation_id,
                    WorkSession.workspace_root == session.workspace_root,
                )
                if session.conversation_id
                else query.where(VirtualCodeDocument.session_id == session_id)
            )
            rows = db.scalars(
                query.order_by(VirtualCodeDocument.updated_at.desc()).limit(100)
            ).all()
            return [serialize(row) for row in reversed(rows)]

    def put(
        self,
        session_id: str,
        title: str,
        content: str,
        *,
        source="generated",
        related_file: str | None = None,
        previous: str = "",
        base_hash: str | None = None,
    ) -> dict:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 100_000:
            raise ValueError("草稿内容超过 100 KB")
        if source not in {"generated", "pasted", "derived_from_file", "agent_modified"}:
            raise ValueError("无效草稿来源")
        if not title or len(title) > 160:
            raise ValueError("草稿标题无效")
        with SessionLocal() as db:
            self._session(db, session_id)
            row = db.scalar(
                select(VirtualCodeDocument).where(
                    VirtualCodeDocument.session_id == session_id, VirtualCodeDocument.title == title
                )
            )
            if not row:
                row = VirtualCodeDocument(
                    session_id=session_id,
                    title=title,
                    language=LANGUAGES.get(Path(title).suffix.lower(), "text"),
                    content=content,
                    source=source,
                    related_file=related_file,
                    base_hash=base_hash,
                )
                db.add(row)
            else:
                row.content, row.source, row.dirty, row.status = content, source, True, "proposed"
                row.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(row)
            document = serialize(row)
        patch = "".join(
            difflib.unified_diff(
                previous.splitlines(True),
                content.splitlines(True),
                fromfile="a/" + title,
                tofile="b/" + title,
            )
        )[:16_000]
        return {
            "document_id": document["id"],
            "path": title,
            "virtual": True,
            "dirty": True,
            "source": source,
            "language": document["language"],
            "diff": patch,
            "status": "proposed",
            "bytes": len(content.encode("utf-8")),
        }

    def apply(
        self,
        session_id: str,
        document_id: str,
        root: Path,
        relative: str,
        *,
        overwrite: bool = False,
    ) -> dict:
        from app.services.agent_tools import ToolExecutor

        document = self.get(session_id, document_id)
        target = scoped_path(root, relative)
        if target.exists():
            if not target.is_file() or target.stat().st_size > 100_000:
                raise ValueError("目标不是可编辑的文本文件")
            if document["related_file"] == relative and document["base_hash"]:
                if content_hash(target.read_text(encoding="utf-8")) != document["base_hash"]:
                    raise ValueError("源文件在生成草稿后发生变化，请重新读取后合并")
            elif not overwrite:
                raise FileExistsError("目标已存在，请选择另一个文件名或明确确认覆盖")
        result = ToolExecutor(root).execute(
            {
                "name": "code.edit_file" if target.exists() else "code.create_file",
                "arguments": {"path": relative, "content": document["content"]},
            }
        )
        with SessionLocal() as db:
            row = db.get(VirtualCodeDocument, document_id)
            row.dirty = False
            row.status = "applied"
            row.related_file, row.base_hash = relative, content_hash(row.content)
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
        return {
            **result,
            "document_id": document_id,
            "dirty": False,
            "status": "applied",
            "summary": "代码已保存到项目文件",
        }

    def discard(self, session_id: str, document_id: str) -> None:
        self.get(session_id, document_id)
        with SessionLocal() as db:
            row = db.get(VirtualCodeDocument, document_id)
            # Keep the document/history recoverable; never delete a user's saved draft.
            row.status = "discarded"
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
