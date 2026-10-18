"""Composition root shared by Chat, Agent, Workflow and fixed main-process APIs."""

import asyncio
import json

from sqlalchemy import select

from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEvidence,
    KnowledgePreference,
)
from app.services.knowledge.context import GroundedService, model_target
from app.services.knowledge.contracts import KnowledgeError, SearchQuery
from app.services.knowledge.indexer import IndexManager
from app.services.knowledge.memory import LongTermMemoryService
from app.services.knowledge.retriever import HybridRetriever
from app.services.knowledge.store import KnowledgeStore, dumps


class KnowledgeService:
    def __init__(self, engine, recorder=None):
        self.store = KnowledgeStore(engine)
        self.providers = {}
        self.indexer = IndexManager(self.store, recorder, self.providers)
        self.retriever = HybridRetriever(self.store, self.providers)
        self.memory = LongTermMemoryService(self.store)

    def configure_embedding(self, collection_id, revision, config):
        import os
        from sqlalchemy import delete
        from app.models.knowledge import KnowledgeIndexJob, KnowledgeVector
        from app.services.knowledge.contracts import ACTIVE
        from app.services.knowledge.embedding import EndpointEmbeddingProvider

        provider = None
        if config["mode"] != "DISABLED":
            provider = EndpointEmbeddingProvider(
                **config,
                api_key=os.getenv("KNOWLEDGE_EMBEDDING_API_KEY", "")
                if config["mode"] == "REMOTE"
                else "",
            )
        with self.store.sessions.begin() as db:
            row = self.store.collection(db, collection_id)
            if row.revision != revision:
                raise KnowledgeError("KNOWLEDGE_REVISION_CONFLICT")
            if db.scalar(
                select(KnowledgeIndexJob.id).where(
                    KnowledgeIndexJob.collection_id == row.id, KnowledgeIndexJob.state.in_(ACTIVE)
                )
            ):
                raise KnowledgeError("KNOWLEDGE_JOB_ACTIVE")
            value = json.loads(row.config_json)
            value["options"]["embedding_mode"] = config["mode"]
            value["embedding"] = {k: v for k, v in config.items() if k != "consent"}
            value["embedding"]["fingerprint"] = provider.fingerprint if provider else None
            db.execute(
                delete(KnowledgeVector).where(
                    KnowledgeVector.chunk_id.in_(
                        select(KnowledgeChunk.id).where(KnowledgeChunk.collection_id == row.id)
                    )
                )
            )
            row.config_json = dumps(value)
            row.revision += 1
            row.index_version += 1
            row.status = "NEEDS_INDEX"
        if provider:
            self.providers[collection_id] = provider
        else:
            self.providers.pop(collection_id, None)
        return {
            "mode": config["mode"],
            "active_for_current_process": bool(provider),
            "requires_reauthorization_after_restart": True,
            "requests_sent": 0,
        }

    def preference(self, conversation_id, character_id, update=None):
        with self.store.sessions.begin() as db:
            self.store.conversation(db, conversation_id, character_id)
            row = db.get(KnowledgePreference, str(conversation_id))
            value = (
                json.loads(row.config_json)
                if row
                else {"rag_enabled": False, "memory_enabled": True}
            )
            if update is not None:
                if set(update) - {"rag_enabled", "memory_enabled"} or any(
                    type(v) is not bool for v in update.values()
                ):
                    raise KnowledgeError("KNOWLEDGE_PREFERENCE_INVALID")
                value.update(update)
                db.merge(
                    KnowledgePreference(
                        conversation_id=str(conversation_id), config_json=dumps(value)
                    )
                )
            return value

    async def chat_service(self, service, conversation_id, character_id, message, message_id):
        preference = await asyncio.to_thread(self.preference, conversation_id, character_id)
        if not preference.get("rag_enabled"):
            return service
        grants = await asyncio.to_thread(self.store.grants, conversation_id, character_id)
        if not grants:
            return GroundedService(self, service, [], conversation_id, character_id, message_id)
        result = await asyncio.to_thread(
            self.retriever.search,
            SearchQuery(query=message[:300], collection_ids=[r.collection_id for r in grants]),
            conversation_id,
            character_id,
            target=model_target(service),
        )
        return GroundedService(
            self, service, result["results"], conversation_id, character_id, message_id
        )

    def filter_history(self, rows, conversation_id, character_id, service):
        result = []
        with self.store.sessions() as db:
            for row in rows:
                proof = db.get(KnowledgeEvidence, row.id)
                if proof:
                    try:
                        if proof.model_target != model_target(service):
                            continue
                        for reference in json.loads(proof.references_json):
                            self.retriever.citations.resolve(
                                reference, conversation_id, character_id, target=proof.model_target
                            )
                    except KnowledgeError:
                        continue
                result.append({"role": row.role, "content": row.content})
        return result

    def evidence(self, message_id, conversation_id, character_id):
        with self.store.sessions() as db:
            self.store.conversation(db, conversation_id, character_id)
            row = db.get(KnowledgeEvidence, str(message_id))
            if not row or row.conversation_id != str(conversation_id):
                return {"citations": []}
            result = []
            for reference in json.loads(row.references_json):
                try:
                    result.append(
                        self.retriever.citations.resolve(reference, conversation_id, character_id)
                    )
                except KnowledgeError:
                    result.append(
                        {
                            key: value
                            for key, value in reference.items()
                            if key in {"chunk_id", "relative_path", "location"}
                        }
                        | {"verification": "stale_or_revoked"}
                    )
            return {"citations": result}

    def reference(self, kind, identity, conversation_id, character_id):
        with self.store.sessions() as db:
            chunk = (
                db.get(KnowledgeChunk, str(identity))
                if kind == "chunk"
                else db.scalar(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.document_id == str(identity))
                    .order_by(KnowledgeChunk.ordinal)
                )
            )
            if not chunk:
                raise KnowledgeError("KNOWLEDGE_DOCUMENT_NOT_FOUND", 404)
            doc = db.get(KnowledgeDocument, chunk.document_id)
            collection = self.store.collection(db, chunk.collection_id)
            reference = self.retriever.citations.dto(doc, chunk, collection)
        return self.retriever.citations.resolve(reference, conversation_id, character_id)

    def tool(self, name, arguments, conversation_id, character_id):
        if name == "knowledge.collections":
            grants = self.store.grants(conversation_id, character_id)
            allowed = {r.collection_id for r in grants}
            # Root absolute paths/configuration never enter an LLM tool result.
            return {
                "collections": [
                    {
                        k: row[k]
                        for k in (
                            "id",
                            "name",
                            "description",
                            "document_count",
                            "chunk_count",
                            "index_version",
                            "status",
                        )
                    }
                    for row in self.store.list()
                    if row["id"] in allowed
                ],
                "verified": True,
            }
        if name == "knowledge.search":
            return self.retriever.search(
                SearchQuery.model_validate(arguments), conversation_id, character_id
            )
        if name in {"knowledge.get_document", "knowledge.get_chunk"}:
            key = "document_id" if name.endswith("document") else "chunk_id"
            return {
                "source": self.reference(
                    "document" if key == "document_id" else "chunk",
                    arguments[key],
                    conversation_id,
                    character_id,
                ),
                "verified": True,
            }
        raise KnowledgeError("KNOWLEDGE_TOOL_UNKNOWN")

    async def close(self):
        await self.indexer.close()
