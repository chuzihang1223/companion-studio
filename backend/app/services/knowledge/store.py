"""Short transactions only; indexing never holds a database lock while parsing."""

import json
import re
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
    KnowledgeGrant,
    KnowledgeIndexJob,
    KnowledgeVector,
)
from app.models.conversation import Conversation
from app.services.knowledge.contracts import (
    ACTIVE,
    CollectionConfig,
    CreateCollection,
    KnowledgeError,
)
from app.services.knowledge.filesystem import root_record


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def tokens(value):
    # unicode61 does not split CJK words. Index/query the same explicit unigrams
    # and bigrams, alongside English/code identifiers, without a model download.
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[\u3400-\u9fff]+", value.lower())
    result = []
    for word in words:
        if "\u3400" <= word[0] <= "\u9fff":
            result.extend(word)
            result.extend(word[i : i + 2] for i in range(len(word) - 1))
        else:
            result.append(word)
    return list(dict.fromkeys(result))


class KnowledgeStore:
    def __init__(self, engine):
        self.engine = engine
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(chunk_id UNINDEXED, collection_id UNINDEXED, terms, tokenize='unicode61')"
                )
            )
        # A crashed parser/job is not presumed complete or automatically replayed.
        with self.sessions.begin() as db:
            for row in db.scalars(
                select(KnowledgeIndexJob).where(KnowledgeIndexJob.state.in_(ACTIVE))
            ):
                row.state, row.last_error = "NEEDS_REVIEW", "KNOWLEDGE_INTERRUPTED"
                collection = db.get(KnowledgeCollection, row.collection_id)
                if collection:
                    collection.status = "NEEDS_REVIEW"

    def collection(self, db, collection_id):
        row = db.get(KnowledgeCollection, str(collection_id))
        if row is None:
            raise KnowledgeError("KNOWLEDGE_COLLECTION_NOT_FOUND", 404)
        return row

    def config(self, row):
        config = json.loads(row.config_json)
        return CollectionConfig.model_validate(config.get("options", {})), config.get("roots", [])

    def summary(self, db, row):
        options, roots = self.config(row)

        def count(model):
            return db.scalar(
                select(func.count()).select_from(model).where(model.collection_id == row.id)
            )

        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "revision": row.revision,
            "index_version": row.index_version,
            "status": row.status,
            "source_roots": roots,
            "config": options.model_dump(),
            "document_count": count(KnowledgeDocument),
            "chunk_count": count(KnowledgeChunk),
            "last_indexed_at": row.last_indexed_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def list(self):
        with self.sessions() as db:
            return [
                self.summary(db, row)
                for row in db.scalars(
                    select(KnowledgeCollection).order_by(KnowledgeCollection.created_at)
                ).all()
            ]

    def create(self, payload: CreateCollection):
        if payload.config.embedding_mode != "DISABLED":
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_CONSENT_REQUIRED", 403)
        with self.sessions.begin() as db:
            if db.scalar(select(func.count()).select_from(KnowledgeCollection)) >= 100:
                raise KnowledgeError("KNOWLEDGE_COLLECTION_LIMIT")
            row = KnowledgeCollection(
                name=payload.name.strip(),
                description=payload.description,
                config_json=dumps({"options": payload.config.model_dump(), "roots": []}),
            )
            db.add(row)
            db.flush()
            return self.summary(db, row)

    def update(self, collection_id, revision, *, payload=None, add_root=None, remove_root=None):
        root = root_record(add_root) if add_root is not None else None
        with self.sessions.begin() as db:
            row = self.collection(db, collection_id)
            if row.revision != revision:
                raise KnowledgeError("KNOWLEDGE_REVISION_CONFLICT")
            active = db.scalar(
                select(KnowledgeIndexJob.id).where(
                    KnowledgeIndexJob.collection_id == row.id, KnowledgeIndexJob.state.in_(ACTIVE)
                )
            )
            if active:
                raise KnowledgeError("KNOWLEDGE_JOB_ACTIVE")
            options, roots = self.config(row)
            if payload:
                if payload.config.embedding_mode != options.embedding_mode:
                    raise KnowledgeError("KNOWLEDGE_EMBEDDING_CONSENT_REQUIRED", 403)
                row.name, row.description = payload.name, payload.description
                options = payload.config
                # A changed exclusion/size policy must invalidate the old corpus.
                self.purge_documents(db, row.id)
            if root:
                if len(roots) >= 10 or any(
                    r["path"].casefold() == root["path"].casefold() for r in roots
                ):
                    raise KnowledgeError("KNOWLEDGE_ROOT_DUPLICATE_OR_LIMIT")
                roots.append(root)
            if remove_root:
                if not any(r["id"] == str(remove_root) for r in roots):
                    raise KnowledgeError("KNOWLEDGE_ROOT_NOT_FOUND", 404)
                for doc in db.scalars(
                    select(KnowledgeDocument).where(
                        KnowledgeDocument.collection_id == row.id,
                        KnowledgeDocument.root_id == str(remove_root),
                    )
                ):
                    self.purge_chunks(db, doc.id)
                    db.delete(doc)
                roots = [r for r in roots if r["id"] != str(remove_root)]
            row.config_json = dumps({"options": options.model_dump(), "roots": roots})
            row.revision += 1
            row.index_version += 1
            row.status = "NEEDS_INDEX"
            return self.summary(db, row)

    def purge_chunks(self, db, document_id):
        ids = select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document_id)
        db.execute(delete(KnowledgeVector).where(KnowledgeVector.chunk_id.in_(ids)))
        db.execute(
            text(
                "DELETE FROM knowledge_fts WHERE chunk_id IN (SELECT id FROM knowledge_chunks WHERE document_id=:id)"
            ),
            {"id": document_id},
        )
        db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))

    def purge_documents(self, db, collection_id):
        for doc in db.scalars(
            select(KnowledgeDocument).where(KnowledgeDocument.collection_id == collection_id)
        ):
            self.purge_chunks(db, doc.id)
            db.delete(doc)

    def remove(self, collection_id, revision):
        with self.sessions.begin() as db:
            row = self.collection(db, collection_id)
            if row.revision != revision:
                raise KnowledgeError("KNOWLEDGE_REVISION_CONFLICT")
            if db.scalar(
                select(KnowledgeIndexJob.id).where(
                    KnowledgeIndexJob.collection_id == row.id, KnowledgeIndexJob.state.in_(ACTIVE)
                )
            ):
                raise KnowledgeError("KNOWLEDGE_JOB_ACTIVE")
            self.purge_documents(db, row.id)
            db.execute(delete(KnowledgeGrant).where(KnowledgeGrant.collection_id == row.id))
            db.execute(delete(KnowledgeIndexJob).where(KnowledgeIndexJob.collection_id == row.id))
            db.delete(row)

    def conversation(self, db, conversation_id, character_id=None):
        row = db.get(Conversation, str(conversation_id))
        if (
            not row
            or row.is_deleted
            or character_id is not None
            and row.character_id != character_id
        ):
            raise KnowledgeError("KNOWLEDGE_OWNER_MISMATCH", 403)
        return row

    def grant(self, conversation_id, character_id, collection_ids, targets=()):
        ids = list(dict.fromkeys(str(UUID(str(v))) for v in collection_ids))
        if (
            len(ids) > 10
            or len(targets) > 4
            or any(not re.fullmatch(r"[a-f0-9]{64}", v) for v in targets)
        ):
            raise KnowledgeError("KNOWLEDGE_GRANT_INVALID")
        with self.sessions.begin() as db:
            self.conversation(db, conversation_id, character_id)
            for collection_id in ids:
                self.collection(db, collection_id)
            db.execute(
                delete(KnowledgeGrant).where(KnowledgeGrant.conversation_id == str(conversation_id))
            )
            for collection_id in ids:
                db.add(
                    KnowledgeGrant(
                        conversation_id=str(conversation_id),
                        character_id=character_id,
                        collection_id=collection_id,
                        model_targets_json=dumps(list(targets)),
                    )
                )
        return {"collection_ids": ids, "model_targets": list(targets)}

    def grants(self, conversation_id, character_id):
        with self.sessions() as db:
            self.conversation(db, conversation_id, character_id)
            return db.scalars(
                select(KnowledgeGrant).where(
                    KnowledgeGrant.conversation_id == str(conversation_id),
                    KnowledgeGrant.character_id == character_id,
                )
            ).all()

    def authorize(self, db, conversation_id, character_id, collection_ids, target=None):
        self.conversation(db, conversation_id, character_id)
        grants = {
            r.collection_id: r
            for r in db.scalars(
                select(KnowledgeGrant).where(
                    KnowledgeGrant.conversation_id == str(conversation_id),
                    KnowledgeGrant.character_id == character_id,
                )
            )
        }
        for collection_id in collection_ids:
            grant = grants.get(str(collection_id))
            if grant is None:
                raise KnowledgeError("KNOWLEDGE_NOT_GRANTED", 403)
            if target and target not in json.loads(grant.model_targets_json):
                raise KnowledgeError("KNOWLEDGE_MODEL_TRANSFER_NOT_GRANTED", 403)
