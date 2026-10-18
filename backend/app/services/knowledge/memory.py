"""User-authored facts only, extending the original manual_memories table."""

from datetime import datetime, timezone
import json
import re
from typing import Literal

from pydantic import Field, field_validator
from sqlalchemy import delete, select

from app.models.knowledge import KnowledgePreference
from app.models.personalization import ManualMemory
from app.models.work import WorkSession
from app.services.knowledge.contracts import KnowledgeError, Strict
from app.services.knowledge.store import tokens
from app.services.knowledge.trace import event


class MemoryInput(Strict):
    scope: Literal["GLOBAL_ASSISTANT", "CHARACTER", "CONVERSATION", "WORKSPACE"]
    owner_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=500)
    category: Literal["fact", "preference", "task"] = "fact"
    enabled: bool = True
    expires_at: datetime | None = None

    @field_validator("content")
    @classmethod
    def no_credentials(cls, value):
        if not value.strip() or re.search(
            r"(?i)(?:sk-|gh[pousr]_|github_pat_|bearer\s+|-----BEGIN .*PRIVATE KEY|(?:password|密码|api[_ -]?key|token|密钥)\s*[:=：])",
            value,
        ):
            raise ValueError("记忆不能为空或包含密码、密钥及令牌")
        return value.strip()

    @field_validator("expires_at")
    @classmethod
    def expiry(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("到期时间需要时区")
        return value


class LongTermMemoryService:
    def __init__(self, store):
        self.store = store

    def scope(self, db, scope, owner_id, character_id, conversation_id):
        conversation = self.store.conversation(db, conversation_id, character_id)
        if scope == "GLOBAL_ASSISTANT" and (character_id != "assistant" or owner_id != "assistant"):
            raise KnowledgeError("MEMORY_SCOPE_MISMATCH", 403)
        if scope == "CHARACTER" and owner_id != character_id:
            raise KnowledgeError("MEMORY_SCOPE_MISMATCH", 403)
        if scope == "CONVERSATION" and owner_id != conversation.id:
            raise KnowledgeError("MEMORY_SCOPE_MISMATCH", 403)
        if scope == "WORKSPACE":
            work = db.get(WorkSession, owner_id)
            if not work or work.conversation_id != conversation.id or not work.workspace_root:
                raise KnowledgeError("MEMORY_SCOPE_MISMATCH", 403)

    @staticmethod
    def dto(row):
        return {
            "memory_id": row.id,
            "scope": row.scope,
            "owner_id": row.owner_id or row.character_id,
            "content": row.content,
            "category": row.category,
            "source_type": row.source_type,
            "source_reference": row.source_reference,
            "enabled": row.enabled,
            "status": row.status,
            "expires_at": row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at else None,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def list(self, scope, owner_id, character_id, conversation_id):
        with self.store.sessions() as db:
            self.scope(db, scope, owner_id, character_id, conversation_id)
            rows = db.scalars(
                select(ManualMemory)
                .where(ManualMemory.scope == scope, ManualMemory.character_id == character_id)
                .order_by(ManualMemory.created_at.desc())
                .limit(1000)
            ).all()
            return [self.dto(r) for r in rows if (r.owner_id or r.character_id) == owner_id]

    def save(self, payload: MemoryInput, character_id, conversation_id, memory_id=None):
        with self.store.sessions.begin() as db:
            self.scope(db, payload.scope, payload.owner_id, character_id, conversation_id)
            row = db.get(ManualMemory, memory_id) if memory_id else None
            if memory_id and (
                not row
                or row.character_id != character_id
                or row.scope != payload.scope
                or (row.owner_id or row.character_id) != payload.owner_id
            ):
                raise KnowledgeError("MEMORY_NOT_FOUND", 404)
            if row is None:
                count = db.scalars(
                    select(ManualMemory.id)
                    .where(ManualMemory.character_id == character_id)
                    .limit(1001)
                ).all()
                if len(count) >= 1000:
                    raise KnowledgeError("MEMORY_LIMIT")
                row = ManualMemory(
                    character_id=character_id, source_type="manual", source_reference=None
                )
                db.add(row)
            for key, value in payload.model_dump().items():
                setattr(row, key, value)
            row.status = "ACTIVE" if payload.enabled else "DISABLED"
            db.flush()
            result = self.dto(row)
        event("MEMORY_SAVED", memory_id=result["memory_id"])
        return result

    def remove(self, scope, owner_id, character_id, conversation_id, memory_id=None):
        with self.store.sessions.begin() as db:
            self.scope(db, scope, owner_id, character_id, conversation_id)
            rows = db.scalars(
                select(ManualMemory).where(
                    ManualMemory.scope == scope, ManualMemory.character_id == character_id
                )
            ).all()
            matched = [
                r
                for r in rows
                if (r.owner_id or r.character_id) == owner_id
                and (memory_id is None or r.id == memory_id)
            ]
            if memory_id and not matched:
                raise KnowledgeError("MEMORY_NOT_FOUND", 404)
            for row in matched:
                db.delete(row)
        for row in matched:
            event("MEMORY_DELETED", memory_id=row.id)
        return {"deleted": len(matched)}

    def relevant(self, character_id, conversation_id, message, *, limit=6, include_character=True):
        with self.store.sessions() as db:
            conversation = self.store.conversation(db, conversation_id, character_id)
            preference = db.get(KnowledgePreference, conversation_id)
            if preference and json.loads(preference.config_json).get("memory_enabled") is False:
                return []
            scopes = {("CONVERSATION", conversation_id)}
            if include_character:
                scopes.add(("CHARACTER", character_id))
            if character_id == "assistant":
                scopes.add(("GLOBAL_ASSISTANT", "assistant"))
            work = db.scalar(
                select(WorkSession)
                .where(WorkSession.conversation_id == conversation.id)
                .order_by(WorkSession.created_at.desc())
            )
            if work and work.workspace_root:
                scopes.add(("WORKSPACE", work.id))
            rows = db.scalars(
                select(ManualMemory)
                .where(
                    ManualMemory.character_id == character_id,
                    ManualMemory.enabled.is_(True),
                    ManualMemory.status == "ACTIVE",
                )
                .order_by(ManualMemory.updated_at.desc())
                .limit(1000)
            ).all()
            query, current = set(tokens(message)), datetime.now(timezone.utc)
            selected = []
            for row in rows:
                if (
                    (row.scope, row.owner_id or row.character_id) not in scopes
                    or row.expires_at
                    and row.expires_at.replace(tzinfo=timezone.utc) <= current
                ):
                    continue
                score = len(query & set(tokens(row.content)))
                if score > 0:
                    selected.append((score, row))
            selected.sort(key=lambda r: -r[0])
            return [row.content for _, row in selected[:limit]]
