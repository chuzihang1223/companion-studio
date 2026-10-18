from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models import ManualMemory, Personalization
from app.services.character.personalization import (
    DEFAULTS,
    list_manual,
    read_settings,
    save_settings,
)
from app.services.character.relationship_memory import relationship_memory
from app.services.character.loader import is_clean_distribution

router = APIRouter(prefix="/api/personalization", tags=["personalization"])


class SettingsPayload(BaseModel):
    personality: str = Field("", max_length=1000)
    relationship: str = Field("", max_length=1000)
    appellation: str = Field("", max_length=100)
    user_name: str = Field("", max_length=100)
    user_nickname: str = Field("", max_length=100)
    user_status: str = Field("", max_length=300)
    user_interests: str = Field("", max_length=300)
    user_games: str = Field("", max_length=300)
    user_foods: str = Field("", max_length=300)
    user_dislikes: str = Field("", max_length=300)
    user_notes: str = Field("", max_length=1000)
    custom_rules: str = Field("", max_length=1000)
    reply_style: str = Field("natural", pattern="^(short|natural|detailed)$")
    intimacy: str = Field(
        "natural" if is_clean_distribution() else "very_close",
        pattern="^(natural|close|very_close)$",
    )


class MemoryPayload(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    enabled: bool = True


class MemoryUpdate(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=500)
    enabled: bool | None = None


def ensure_character(request: Request, character_id: str) -> None:
    if request.app.state.characters.get(character_id) is None:
        raise HTTPException(status_code=404, detail="未找到角色")


def memory_dict(row: ManualMemory) -> dict:
    return {"id": row.id, "content": row.content, "enabled": row.enabled, "source": "manual"}


@router.get("/{character_id}")
def get_personalization(character_id: str, request: Request) -> dict:
    ensure_character(request, character_id)
    with SessionLocal() as db:
        return {
            "settings": read_settings(db, character_id),
            "manual_memories": [memory_dict(row) for row in list_manual(db, character_id)],
            "conversation_memories": relationship_memory(db, character_id),
        }


@router.put("/{character_id}")
def put_personalization(character_id: str, payload: SettingsPayload, request: Request) -> dict:
    ensure_character(request, character_id)
    with SessionLocal() as db:
        return {"settings": save_settings(db, character_id, payload.model_dump())}


@router.post("/{character_id}/memories", status_code=201)
def add_memory(character_id: str, payload: MemoryPayload, request: Request) -> dict:
    ensure_character(request, character_id)
    with SessionLocal() as db:
        row = ManualMemory(
            character_id=character_id, content=payload.content.strip(), enabled=payload.enabled
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return memory_dict(row)


@router.patch("/{character_id}/memories/{memory_id}")
def update_memory(
    character_id: str, memory_id: str, payload: MemoryUpdate, request: Request
) -> dict:
    ensure_character(request, character_id)
    with SessionLocal() as db:
        row = db.get(ManualMemory, memory_id)
        if row is None or row.character_id != character_id or row.scope != "CHARACTER":
            raise HTTPException(status_code=404, detail="未找到记忆")
        if payload.content is not None:
            row.content = payload.content.strip()
        if payload.enabled is not None:
            row.enabled = payload.enabled
        db.commit()
        return memory_dict(row)


@router.delete("/{character_id}/memories/{memory_id}", status_code=204)
def delete_memory(character_id: str, memory_id: str, request: Request) -> None:
    ensure_character(request, character_id)
    with SessionLocal() as db:
        row = db.get(ManualMemory, memory_id)
        if row is None or row.character_id != character_id or row.scope != "CHARACTER":
            raise HTTPException(status_code=404, detail="未找到记忆")
        db.delete(row)
        db.commit()


@router.delete("/{character_id}/reset", status_code=204)
def reset_personalization(character_id: str, request: Request) -> None:
    ensure_character(request, character_id)
    with SessionLocal() as db:
        row = db.get(Personalization, character_id)
        if row:
            db.delete(row)
        for memory in list_manual(db, character_id):
            db.delete(memory)
        db.commit()
