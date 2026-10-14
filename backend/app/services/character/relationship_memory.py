"""Recall only relationship details the user actually wrote in saved chats."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, Message


_CUES = (
    "叫我",
    "称呼我",
    "喜欢你叫",
    "你可以叫",
    "我们是",
    "你是我",
    "我是你",
    "家人",
    "恋人",
    "男朋友",
    "女朋友",
    "爱你",
    "喜欢你",
    "想你",
    "想念你",
    "答应你",
    "答应我",
    "说好了",
    "约定",
    "纪念日",
    "生日",
    "一起去",
    "一起做",
)


def relationship_memory(
    db: Session, character_id: str, recent_ids: set[str] | None = None
) -> list[str]:
    """Return short source quotes, never inferred facts or invented shared events."""
    rows = db.scalars(
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Message.character_id == character_id,
            Message.role == "user",
            Conversation.is_deleted.is_(False),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(200)
    ).all()
    seen: set[str] = set()
    recalled: list[str] = []
    for row in rows:
        if row.id in (recent_ids or set()):
            continue
        quote = " ".join(row.content.strip().split())[:120]
        if not quote or quote in seen or not any(cue in quote for cue in _CUES):
            continue
        seen.add(quote)
        recalled.append(f"用户曾说：『{quote}』")
        if len(recalled) == 8:
            break
    return (
        ["以下只是用户真实原话，勿把愿望当成已发生的共同经历：", *reversed(recalled)]
        if recalled
        else []
    )
