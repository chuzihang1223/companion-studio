"""Local conversation list, search, branches, and nondestructive deletion."""

from datetime import datetime, timezone
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import exists, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Conversation, Message, MessageVoice, WorkSession
from app.schemas.conversation import (
    BranchCreate,
    ConversationCreate,
    ConversationInfo,
    ConversationMessage,
    ConversationRename,
    ConversationMode,
)
from app.services.conversation import visible_messages
from app.services.voice.message_cache import index_audio_file


router = APIRouter(prefix="/api/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)


def _get(db: Session, conversation_id: str) -> Conversation:
    row = db.get(Conversation, conversation_id)
    if row is None or row.is_deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return row


@router.get("", response_model=list[ConversationInfo])
def list_conversations(
    character_id: str = Query(min_length=1, max_length=100),
    search: str = Query(default="", max_length=100),
    db: Session = Depends(get_db),
) -> list[Conversation]:
    try:
        query = select(Conversation).where(
            Conversation.character_id == character_id,
            Conversation.is_deleted.is_(False),
        )
        term = search.strip()
        if term:
            pattern = "%" + term.replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(
                or_(
                    Conversation.title.like(pattern, escape="\\"),
                    exists(
                        select(Message.id).where(
                            Message.conversation_id == Conversation.id,
                            Message.content.like(pattern, escape="\\"),
                        )
                    ),
                )
            )
        return db.scalars(
            query.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        ).all()
    except SQLAlchemyError as exc:
        logger.exception("Could not list conversations")
        raise HTTPException(status_code=503, detail="Could not load conversations") from exc


@router.post("", response_model=ConversationInfo)
def create_conversation(
    request: Request, payload: ConversationCreate, db: Session = Depends(get_db)
) -> Conversation:
    if request.app.state.characters.get(payload.character_id) is None:
        raise HTTPException(status_code=404, detail="Character not found")
    row = Conversation(
        character_id=payload.character_id,
        conversation_type=payload.conversation_type,
        project_id=payload.project_id,
        preferred_mode="chat" if payload.conversation_type == "chat" else "work",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{conversation_id}", response_model=ConversationInfo)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> Conversation:
    return _get(db, conversation_id)


@router.get("/{conversation_id}/messages", response_model=list[ConversationMessage])
def conversation_messages(
    conversation_id: str, db: Session = Depends(get_db)
) -> list[ConversationMessage]:
    conversation = _get(db, conversation_id)
    try:
        rows = visible_messages(db, conversation)
        voices = (
            {
                item.message_id: item
                for item in db.scalars(
                    select(MessageVoice).where(
                        MessageVoice.message_id.in_([row.id for row in rows]),
                    )
                ).all()
            }
            if rows
            else {}
        )
        return [
            ConversationMessage(
                id=row.id,
                character_id=row.character_id,
                conversation_id=row.conversation_id,
                role=row.role,
                content=row.content,
                created_at=row.created_at,
                reply_to_message_id=row.reply_to_message_id,
                variant_of_message_id=row.variant_of_message_id,
                voice_status=(
                    "ready"
                    if index_audio_file(voices[row.id])
                    else "missing"
                    if voices[row.id].status == "ready"
                    else voices[row.id].status
                )
                if row.id in voices
                else "none",
                voice_engine=voices[row.id].engine if row.id in voices else None,
                attachments=json.loads(row.attachments_json or "[]"),
            )
            for row in rows
        ]
    except (SQLAlchemyError, ValueError) as exc:
        logger.exception("Could not load conversation messages")
        raise HTTPException(status_code=503, detail="Could not load conversation messages") from exc


@router.patch("/{conversation_id}", response_model=ConversationInfo)
def rename_conversation(
    conversation_id: str, payload: ConversationRename, db: Session = Depends(get_db)
) -> Conversation:
    row = _get(db, conversation_id)
    row.title = payload.title.strip()
    if not row.title:
        raise HTTPException(status_code=422, detail="Title cannot be blank")
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{conversation_id}/mode", response_model=ConversationInfo)
def change_mode(
    conversation_id: str, payload: ConversationMode, db: Session = Depends(get_db)
) -> Conversation:
    row = _get(db, conversation_id)
    # Compatibility endpoint: UI mode changes cannot retag historical records.
    row.preferred_mode = "chat" if row.conversation_type == "chat" else "work"
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    row = _get(db, conversation_id)
    row.is_deleted = True
    for work in db.scalars(select(WorkSession).where(WorkSession.conversation_id == row.id)):
        work.status = "stopped"
    db.commit()
    return {"status": "deleted"}


@router.post("/{conversation_id}/branches", response_model=ConversationInfo)
def create_branch(
    conversation_id: str, payload: BranchCreate, db: Session = Depends(get_db)
) -> Conversation:
    source = _get(db, conversation_id)
    visible = visible_messages(db, source)
    if payload.fork_message_id and payload.fork_message_id not in {row.id for row in visible}:
        raise HTTPException(status_code=404, detail="Fork message not found")
    branch = Conversation(
        character_id=source.character_id,
        title=source.title + " · 分支",
        preferred_mode=source.preferred_mode,
        conversation_type=source.conversation_type,
        project_id=source.project_id,
        parent_conversation_id=source.id,
        fork_message_id=payload.fork_message_id,
    )
    db.add(branch)
    db.commit()
    db.refresh(branch)
    return branch


@router.get("/{conversation_id}/siblings", response_model=list[ConversationInfo])
def branches_near(conversation_id: str, db: Session = Depends(get_db)) -> list[Conversation]:
    row = _get(db, conversation_id)
    root_id = row.parent_conversation_id or row.id
    return db.scalars(
        select(Conversation)
        .where(
            Conversation.is_deleted.is_(False),
            or_(Conversation.id == root_id, Conversation.parent_conversation_id == root_id),
        )
        .order_by(Conversation.created_at, Conversation.id)
    ).all()
