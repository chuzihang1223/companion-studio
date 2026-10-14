"""User-owned overlay and manual memory; base character files stay immutable."""

import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ManualMemory, Personalization
from app.services.character.loader import is_clean_distribution


DEFAULTS = {
    "personality": "",
    "relationship": "",
    "appellation": "",
    "user_name": "",
    "user_nickname": "",
    "user_status": "",
    "user_interests": "",
    "user_games": "",
    "user_foods": "",
    "user_dislikes": "",
    "user_notes": "",
    "custom_rules": "",
    "reply_style": "natural",
    "intimacy": "natural",
}


def read_settings(db: Session, character_id: str) -> dict:
    row = db.get(Personalization, character_id)
    try:
        stored = json.loads(row.settings_json) if row else {}
    except (ValueError, TypeError):
        stored = {}
    defaults = {**DEFAULTS, "intimacy": "natural"} if is_clean_distribution() else DEFAULTS
    return {**defaults, **{key: value for key, value in stored.items() if key in DEFAULTS}}


def save_settings(db: Session, character_id: str, settings: dict) -> dict:
    row = db.get(Personalization, character_id) or Personalization(character_id=character_id)
    defaults = {**DEFAULTS, "intimacy": "natural"} if is_clean_distribution() else DEFAULTS
    values = {**defaults, **{key: value for key, value in settings.items() if key in DEFAULTS}}
    row.settings_json = json.dumps(values, ensure_ascii=False)
    db.add(row)
    db.commit()
    return values


def list_manual(db: Session, character_id: str) -> list[ManualMemory]:
    return list(
        db.scalars(
            select(ManualMemory)
            .where(ManualMemory.character_id == character_id)
            .order_by(ManualMemory.created_at.desc(), ManualMemory.id.desc())
        ).all()
    )


def relevant_manual(db: Session, character_id: str, message: str, limit: int = 6) -> list[str]:
    rows = [row for row in list_manual(db, character_id) if row.enabled]
    query = message.casefold()

    def relevance(row: ManualMemory) -> int:
        text = row.content.casefold()
        return sum(1 for index in range(len(query) - 1) if query[index : index + 2] in text)

    rows.sort(key=relevance, reverse=True)
    if any(cue in query for cue in ("记得", "记住", "关于我", "我的记忆", "你知道我")):
        return [row.content for row in rows[:limit]]
    matched = [row.content for row in rows if relevance(row) > 0]
    if matched:
        return matched[:limit]
    if any(cue in query for cue in ("紧张", "担心", "压力", "累", "焦虑", "怎么办")):
        return [row.content for row in rows[:2]]
    return []
