"""Bounded FTS5/RRF retrieval. Every returned source is revalidated against disk."""

import json
from pathlib import Path
import re
import threading
import time

from sqlalchemy import bindparam, select, text

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services.knowledge.contracts import KnowledgeError, SearchQuery
from app.services.knowledge.embedding import VectorStore, reciprocal_rank_fusion
from app.services.knowledge.filesystem import read_document
from app.services.knowledge.store import tokens
from app.services.knowledge.trace import event


def query_terms(value):
    groups = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[\u3400-\u9fff]+", value.lower())
    result = []
    for group in groups:
        if "\u3400" <= group[0] <= "\u9fff" and len(group) > 1:
            result.extend(group[i : i + 2] for i in range(len(group) - 1))
        else:
            result.append(group)
    return list(dict.fromkeys(result))[:30]


class CitationResolver:
    def __init__(self, store):
        self.store = store

    def fresh(self, db, doc):
        collection = self.store.collection(db, doc.collection_id)
        options, roots = self.store.config(collection)
        if collection.status not in {"READY", "READY_WITH_ERRORS"}:
            raise KnowledgeError("KNOWLEDGE_INDEX_NOT_READY")
        root = next((r for r in roots if r["id"] == doc.root_id), None)
        if not root or doc.status not in {"INDEXED", "UNCHANGED"}:
            raise KnowledgeError("KNOWLEDGE_STALE_EVIDENCE")
        try:
            _, info = read_document(root, doc.relative_path, options.max_file_bytes)
        except KnowledgeError:
            raise KnowledgeError("KNOWLEDGE_STALE_EVIDENCE") from None
        if info["content_hash"] != doc.content_hash:
            raise KnowledgeError("KNOWLEDGE_STALE_EVIDENCE")
        return root, collection

    def dto(self, doc, chunk, collection, *, excerpt=2000):
        return {
            "collection_id": collection.id,
            "collection_name": collection.name,
            "document_id": doc.id,
            "document_version": doc.version,
            "content_hash": doc.content_hash,
            "index_version": collection.index_version,
            "chunk_id": chunk.id,
            "ordinal": chunk.ordinal,
            "relative_path": doc.relative_path,
            "file_type": doc.file_type,
            "location": json.loads(chunk.location_json),
            "excerpt": chunk.content[:excerpt],
            "verification": "source_hash_verified",
        }

    def resolve(self, reference, conversation_id, character_id, *, target=None, include_path=False):
        with self.store.sessions() as db:
            collection_id = str(reference.get("collection_id", ""))
            self.store.authorize(db, conversation_id, character_id, [collection_id], target)
            doc = db.get(KnowledgeDocument, str(reference.get("document_id", "")))
            chunk = db.get(KnowledgeChunk, str(reference.get("chunk_id", "")))
            if (
                not doc
                or not chunk
                or chunk.document_id != doc.id
                or doc.collection_id != collection_id
                or doc.version != reference.get("document_version")
                or doc.content_hash != reference.get("content_hash")
                or chunk.version != doc.version
            ):
                raise KnowledgeError("KNOWLEDGE_STALE_EVIDENCE")
            root, collection = self.fresh(db, doc)
            result = self.dto(doc, chunk, collection)
            if include_path:
                # Only the main-process control route may receive the real path.
                result["open_path"] = str(Path(root["path"]) / doc.relative_path)
            event(
                "CITATION_RESOLVED",
                collection_id=collection.id,
                document_id=doc.id,
                chunk_id=chunk.id,
            )
            return result


class HybridRetriever:
    def __init__(self, store, providers=None):
        self.store = store
        self.providers = providers if providers is not None else {}
        self.citations = CitationResolver(store)
        self.vector = VectorStore(store)
        # One shared instance serves Chat, tools, Workflows and desktop search.
        # Reject bursts instead of queueing unbounded disk/vector work in threads.
        self._query_slots = threading.BoundedSemaphore(4)

    def keyword(self, query, ids):
        words = query_terms(query.query)
        if not words:
            return []
        # Only terms produced by our lexer reach MATCH. User operators, quotes,
        # parentheses and SQL never become query syntax.
        match = " OR ".join('"' + word + '"' for word in words)
        sql = "SELECT c.id, bm25(knowledge_fts) AS distance FROM knowledge_fts JOIN knowledge_chunks c ON c.id=knowledge_fts.chunk_id JOIN knowledge_documents d ON d.id=c.document_id WHERE knowledge_fts MATCH :match AND c.collection_id IN :ids AND d.status IN ('INDEXED','UNCHANGED')"
        parameters = {"match": match, "ids": ids}
        bindings = [bindparam("ids", expanding=True)]
        if query.file_types:
            sql += " AND d.file_type IN :types"
            parameters["types"] = query.file_types
            bindings.append(bindparam("types", expanding=True))
        if query.path_scope:
            scope = query.path_scope.rstrip("/")
            sql += (
                " AND (d.relative_path=:scope OR substr(d.relative_path,1,:prefix_length)=:prefix)"
            )
            parameters.update(scope=scope, prefix=scope + "/", prefix_length=len(scope) + 1)
        statement = text(sql + " ORDER BY distance,c.id LIMIT 100").bindparams(*bindings)
        with self.store.sessions() as db:
            return [(row[0], float(row[1])) for row in db.execute(statement, parameters)]

    def search(self, query: SearchQuery, conversation_id, character_id, *, target=None):
        if not self._query_slots.acquire(blocking=False):
            raise KnowledgeError("KNOWLEDGE_QUERY_BUSY", 429)
        try:
            return self._search(query, conversation_id, character_id, target=target)
        finally:
            self._query_slots.release()

    def _search(self, query: SearchQuery, conversation_id, character_id, *, target=None):
        started, deadline = time.perf_counter(), time.monotonic() + 12
        ids = [str(v) for v in query.collection_ids]
        event("RETRIEVAL_STARTED", collection_id=ids[0])
        with self.store.sessions() as db:
            self.store.authorize(db, conversation_id, character_id, ids, target)
        keyword = self.keyword(query, ids)
        semantic, warnings = [], []
        actual_mode = "keyword"
        if query.mode in {"hybrid", "vector"}:
            try:
                for collection_id in ids:
                    provider = self.providers.get(collection_id)
                    if provider is None:
                        raise KnowledgeError("KNOWLEDGE_EMBEDDING_DISABLED")
                    semantic.extend(
                        self.vector.search(
                            [collection_id],
                            query.query,
                            provider,
                            file_types=query.file_types,
                            path_scope=query.path_scope,
                        )
                    )
                if not semantic:
                    raise KnowledgeError("KNOWLEDGE_VECTOR_INDEX_EMPTY")
                actual_mode = query.mode
            except KnowledgeError as error:
                semantic = []
                warnings.append(error.code + ":keyword_fallback")
        if actual_mode == "vector":
            ranked, fused = [r[0] for r in semantic], {}
        elif semantic:
            ranked, fused = reciprocal_rank_fusion(
                [r[0] for r in keyword], [r[0] for r in sorted(semantic, key=lambda r: -r[1])]
            )
        else:
            ranked, fused = [r[0] for r in keyword], {}
        bm25, cosine = dict(keyword), dict(semantic)
        selected, verified, seen_hashes, per_document = [], {}, set(), {}
        query_words = set(query_terms(query.query))
        with self.store.sessions() as db:
            for identity in ranked[:100]:
                if time.monotonic() > deadline:
                    warnings.append("KNOWLEDGE_RETRIEVAL_DEADLINE")
                    break
                chunk = db.get(KnowledgeChunk, identity)
                doc = db.get(KnowledgeDocument, chunk.document_id) if chunk else None
                if not doc or doc.collection_id not in ids:
                    continue
                if (
                    query.file_types
                    and doc.file_type not in query.file_types
                    or query.path_scope
                    and doc.relative_path != query.path_scope.rstrip("/")
                    and not doc.relative_path.startswith(query.path_scope.rstrip("/") + "/")
                ):
                    continue
                if doc.id not in verified:
                    try:
                        verified[doc.id] = self.citations.fresh(db, doc)[1]
                    except KnowledgeError:
                        verified[doc.id] = None
                        warnings.append("KNOWLEDGE_STALE_EVIDENCE")
                collection = verified[doc.id]
                if (
                    not collection
                    or chunk.version != doc.version
                    or chunk.content_hash in seen_hashes
                ):
                    continue
                if per_document.get(doc.id, 0) >= 3:
                    continue
                coverage = len(query_words & set(tokens(chunk.content))) / max(1, len(query_words))
                relevance = max(coverage, cosine.get(identity, 0))
                if relevance < query.min_relevance:
                    continue
                seen_hashes.add(chunk.content_hash)
                per_document[doc.id] = per_document.get(doc.id, 0) + 1
                result = self.citations.dto(doc, chunk, collection)
                event(
                    "CITATION_RESOLVED",
                    collection_id=collection.id,
                    document_id=doc.id,
                    chunk_id=chunk.id,
                )
                result.update(
                    mode=actual_mode,
                    scores={
                        "bm25": bm25.get(identity),
                        "cosine": cosine.get(identity),
                        "rrf": fused.get(identity),
                        "relevance": relevance,
                    },
                    rank=len(selected) + 1,
                )
                selected.append(result)
        # Deterministic evidence reranking. Scores retain distinct meanings;
        # BM25 and cosine are never added as if their scales were compatible.
        selected.sort(
            key=lambda r: (-r["scores"]["relevance"], -(r["scores"]["rrf"] or 0), r["rank"])
        )
        selected = selected[: query.top_k]
        for rank, item in enumerate(selected, 1):
            item["rank"] = rank
        elapsed = (time.perf_counter() - started) * 1000
        event("RETRIEVAL_COMPLETED", result_count=len(selected), duration_ms=elapsed)
        return {
            "results": selected,
            "mode": actual_mode,
            "requested_mode": query.mode,
            "warnings": list(dict.fromkeys(warnings)),
            "duration_ms": elapsed,
            "verified": True,
        }
