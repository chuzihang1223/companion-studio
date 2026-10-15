"""Conversation branches share immutable history through their fork point."""

from datetime import datetime, timezone

from sqlalchemy import literal_column, select
from sqlalchemy.orm import Session

from app.models import Conversation, Message


def title_from_message(content: str) -> str:
    return content.strip().replace("\n", " ")[:20] or "新聊天"


def latest_or_create(db: Session, character_id: str) -> Conversation:
    row = db.scalars(
        select(Conversation)
        .where(
            Conversation.character_id == character_id,
            Conversation.is_deleted.is_(False),
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(1)
    ).first()
    if row is None:
        row = Conversation(character_id=character_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def visible_messages(db: Session, conversation: Conversation) -> list[Message]:
    """Resolve a branch with bounded ancestry and no duplicate fork message."""
    lineage: list[Conversation] = []
    seen: set[str] = set()
    current: Conversation | None = conversation
    while current is not None:
        if current.id in seen or len(lineage) >= 32:
            raise ValueError("Invalid conversation branch ancestry")
        seen.add(current.id)
        lineage.append(current)
        current = (
            db.get(Conversation, current.parent_conversation_id)
            if current.parent_conversation_id
            else None
        )
    lineage.reverse()
    result: list[Message] = []
    for index, branch in enumerate(lineage):
        rows = db.scalars(
            select(Message)
            .where(
                Message.conversation_id == branch.id,
            )
            .order_by(Message.created_at, literal_column("rowid"))
        ).all()
        if index + 1 < len(lineage):
            fork_id = lineage[index + 1].fork_message_id
            if fork_id is None:
                rows = []
            else:
                cutoff = next(
                    (position for position, row in enumerate(rows) if row.id == fork_id), None
                )
                if cutoff is None:
                    raise ValueError("Conversation fork point was not found")
                rows = rows[: cutoff + 1]
        result.extend(rows)
    return result


def touch_after_user_message(
    conversation: Conversation, content: str, has_previous_user: bool
) -> None:
    if conversation.title == "新聊天" and not has_previous_user:
        conversation.title = title_from_message(content)
    conversation.updated_at = datetime.now(timezone.utc)
