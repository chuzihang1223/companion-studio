"""Synthetic roots only; never index the user's profile or repository by default."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from app.database.migrations import migrate
from app.models.conversation import Conversation
from app.services.knowledge.contracts import CreateCollection, KnowledgeError
from app.services.knowledge.filesystem import read_document, root_record
from app.services.knowledge.store import KnowledgeStore, tokens


def test_collection_explicit_roots_and_scope(corpus):
    store, root, conversation_id = corpus
    item = store.create(CreateCollection(name="项目资料"))
    assert item["source_roots"] == [] and item["config"]["embedding_mode"] == "DISABLED"
    item = store.update(item["id"], 1, add_root=str(root))
    assert item["revision"] == 2 and len(item["source_roots"]) == 1
    with store.sessions() as db, pytest.raises(KnowledgeError, match="NOT_GRANTED"):
        store.authorize(db, conversation_id, "assistant", [item["id"]])
    store.grant(conversation_id, "assistant", [item["id"]])
    with store.sessions() as db:
        store.authorize(db, conversation_id, "assistant", [item["id"]])
        with pytest.raises(KnowledgeError, match="OWNER_MISMATCH"):
            store.authorize(db, conversation_id, "different-character", [item["id"]])
        with pytest.raises(KnowledgeError, match="TRANSFER_NOT_GRANTED"):
            store.authorize(db, conversation_id, "assistant", [item["id"]], "a" * 64)
    with pytest.raises(KnowledgeError, match="REVISION_CONFLICT"):
        store.update(item["id"], 1, add_root=str(root))
    store.remove(item["id"], 2)
    assert root.exists() and store.list() == []


def test_read_changes_escape_and_secrets(corpus):
    _, root, _ = corpus
    source = root / "说明.txt"
    source.write_text("原始内容", encoding="utf-8")
    record = root_record(str(root))
    raw, meta = read_document(record, source.name, 1024)
    assert raw.decode() == "原始内容" and len(meta["content_hash"]) == 64
    with pytest.raises(KnowledgeError, match="FILE_CHANGED"):
        read_document(
            record,
            source.name,
            1024,
            after_read=lambda: source.write_text("修改后的内容更长", encoding="utf-8"),
        )
    (root / ".env").write_text("not a credential", encoding="utf-8")
    with pytest.raises(KnowledgeError):
        read_document(record, ".env", 1024)
    with pytest.raises(KnowledgeError):
        read_document(record, "../knowledge.db", 1024)
    source.write_bytes(b"x" * 2048)
    with pytest.raises(KnowledgeError, match="TOO_LARGE"):
        read_document(record, source.name, 1024)


def test_migration_idempotent_and_restart(corpus):
    store, _, _ = corpus
    item = store.create(CreateCollection(name="保留资料"))
    from app.models.knowledge import KnowledgeIndexJob

    with store.sessions.begin() as db:
        db.add(
            KnowledgeIndexJob(
                collection_id=item["id"],
                state="PARSING",
                revision=1,
                progress_json=json.dumps({"processed": 3}),
            )
        )
    migrate(store.engine)
    migrate(store.engine)
    restarted = KnowledgeStore(store.engine)
    assert restarted.list()[0]["name"] == "保留资料"
    with store.sessions() as db:
        row = db.query(KnowledgeIndexJob).one()
        assert row.state == "NEEDS_REVIEW" and json.loads(row.progress_json)["processed"] == 3
        assert db.execute(text("SELECT count(*) FROM knowledge_fts")).scalar() == 0


def test_chinese_english_safe_tokens():
    result = tokens('中文检索 get_document OR "password"; DROP TABLE a')
    assert "中文" in result and "检索" in result and "get_document" in result
    assert not any('"' in token or ";" in token for token in result)
