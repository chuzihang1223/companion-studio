"""Small additive SQLite migrations. Existing messages are never recreated."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database.session import Base
from app.models import (
    Conversation,
    Message,
    MessageVoice,
    VoiceJob,
    Personalization,
    ManualMemory,
    WorkSession,
    WorkflowStep,
    WorkEvent,
    WorkPermission,
    WorkspaceTab,
    VirtualCodeDocument,
)  # noqa: F401 - register tables


def migrate(engine: Engine) -> None:
    if inspect(engine).has_table("messages"):
        with engine.begin() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info(messages)"))}
            for column in ("conversation_id", "reply_to_message_id", "variant_of_message_id"):
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE messages ADD COLUMN {column} VARCHAR(36)"))
            if "attachments_json" not in existing:
                conn.execute(
                    text("ALTER TABLE messages ADD COLUMN attachments_json TEXT DEFAULT '[]'")
                )

    # V8.3: add virtual_code_documents only; existing data and tables stay intact.
    Base.metadata.create_all(bind=engine)
    # Evaluation was introduced on an unpublished candidate branch. Preserve its
    # earlier local histories; an absent start time remains unknown, never guessed.
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(evaluation_runs)"))}
        if "started_at" not in columns:
            conn.execute(text("ALTER TABLE evaluation_runs ADD COLUMN started_at DATETIME"))
    with engine.begin() as conn:
        conversation_columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(conversations)"))
        }
        legacy_type = "conversation_type" not in conversation_columns
        if "preferred_mode" not in conversation_columns:
            conn.execute(
                text(
                    "ALTER TABLE conversations ADD COLUMN preferred_mode VARCHAR(10) DEFAULT 'chat'"
                )
            )
        if "conversation_type" not in conversation_columns:
            conn.execute(
                text(
                    "ALTER TABLE conversations ADD COLUMN conversation_type VARCHAR(10) NOT NULL DEFAULT 'chat'"
                )
            )
            # Only a stored Work session proves an old record is Work.
            conn.execute(
                text(
                    "UPDATE conversations SET conversation_type='work', preferred_mode='work' WHERE id IN (SELECT conversation_id FROM work_sessions WHERE conversation_id IS NOT NULL)"
                )
            )
        for column in ("project_id", "work_session_id"):
            if column not in conversation_columns:
                conn.execute(text(f"ALTER TABLE conversations ADD COLUMN {column} VARCHAR(36)"))
        if legacy_type:
            conn.execute(
                text(
                    "UPDATE conversations SET conversation_type='project', preferred_mode='work' WHERE project_id IS NOT NULL AND project_id<>''"
                )
            )
        conn.execute(
            text(
                "UPDATE conversations SET work_session_id=(SELECT id FROM work_sessions WHERE conversation_id=conversations.id ORDER BY created_at DESC LIMIT 1) WHERE work_session_id IS NULL AND conversation_type IN ('work','project')"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conversations_conversation_type ON conversations(conversation_type)"
            )
        )
        work_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(work_sessions)"))}
        if "plan_json" not in work_columns:
            conn.execute(
                text("ALTER TABLE work_sessions ADD COLUMN plan_json TEXT NOT NULL DEFAULT '{}'")
            )
        if "workspace_root" not in work_columns:
            conn.execute(text("ALTER TABLE work_sessions ADD COLUMN workspace_root TEXT"))
            # Migrate selected workspaces from saved V8 plans without deleting history.
            rows = conn.execute(text("SELECT id, plan_json FROM work_sessions")).all()
            import json

            for row in rows:
                try:
                    root = json.loads(row[1] or "{}").get("workspace")
                except (ValueError, TypeError):
                    root = None
                if isinstance(root, str) and root:
                    conn.execute(
                        text("UPDATE work_sessions SET workspace_root=:root WHERE id=:id"),
                        {"root": root, "id": row[0]},
                    )
        voice_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(message_voice)"))}
        for column, kind in (
            ("job_id", "VARCHAR(36)"),
            ("error_code", "VARCHAR(40)"),
            ("error_message", "VARCHAR(300)"),
            ("provider", "VARCHAR(20)"),
            ("model", "VARCHAR(80)"),
            ("voice_profile", "VARCHAR(80)"),
            ("format", "VARCHAR(12)"),
            ("created_at", "DATETIME"),
            ("reference_id", "VARCHAR(100)"),
            ("style_plan", "TEXT"),
            ("generation_ms", "INTEGER"),
        ):
            if column not in voice_columns:
                conn.execute(text(f"ALTER TABLE message_voice ADD COLUMN {column} {kind}"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id)"
            )
        )
        legacy_characters = (
            conn.execute(
                text("SELECT DISTINCT character_id FROM messages WHERE conversation_id IS NULL")
            )
            .scalars()
            .all()
        )
        for character_id in legacy_characters:
            first = conn.execute(
                text(
                    "SELECT content FROM messages WHERE character_id=:character_id AND role='user' "
                    "AND conversation_id IS NULL ORDER BY created_at, id LIMIT 1"
                ),
                {"character_id": character_id},
            ).scalar_one_or_none()
            title = (first or "以往聊天").strip().replace("\n", " ")[:20] or "以往聊天"
            conversation_id = str(uuid4())
            now = datetime.now(timezone.utc)
            conn.execute(
                text(
                    "INSERT INTO conversations (id, character_id, title, preferred_mode, conversation_type, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :character_id, :title, 'chat', 'chat', 0, :now, :now)"
                ),
                {"id": conversation_id, "character_id": character_id, "title": title, "now": now},
            )
            conn.execute(
                text(
                    "UPDATE messages SET conversation_id=:id "
                    "WHERE character_id=:character_id AND conversation_id IS NULL"
                ),
                {"id": conversation_id, "character_id": character_id},
            )
