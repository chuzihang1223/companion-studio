"""Optional user-configured embedding endpoint; no weights or dependencies download."""

from abc import ABC, abstractmethod
import hashlib
import ipaddress
import json
import math
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select, func

from app.models.knowledge import KnowledgeChunk, KnowledgeVector
from app.services.knowledge.contracts import KnowledgeError
from app.services.knowledge.store import dumps


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def fingerprint(self):
        pass

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        pass


def vectors(values, count, dimension):
    if not isinstance(values, list) or len(values) != count:
        raise KnowledgeError("KNOWLEDGE_EMBEDDING_SHAPE")
    result = []
    for row in values:
        if (
            not isinstance(row, list)
            or len(row) != dimension
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in row
            )
        ):
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_SHAPE")
        norm = math.sqrt(sum(v * v for v in row))
        if not norm or not math.isfinite(norm):
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_ZERO_VECTOR")
        result.append([v / norm for v in row])
    return result


class EndpointEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        mode,
        endpoint,
        model,
        dimension,
        revision,
        consent=False,
        api_key="",
        transport=None,
    ):
        if not consent or mode not in {"LOCAL", "REMOTE"}:
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_CONSENT_REQUIRED", 403)
        parsed = urlsplit(endpoint)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or len(endpoint) > 500
            or not 1 <= dimension <= 2048
            or not model
            or len(model) > 100
            or not revision
            or len(revision) > 100
        ):
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_CONFIG")
        try:
            loopback = (
                parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if (
            mode == "LOCAL"
            and (not loopback or parsed.scheme not in {"http", "https"})
            or mode == "REMOTE"
            and parsed.scheme != "https"
        ):
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_ENDPOINT", 403)
        self.mode, self.endpoint, self.model, self.dimension = (
            mode,
            endpoint.rstrip("/"),
            model,
            dimension,
        )
        self.revision, self.key, self.transport = revision, api_key, transport

    @property
    def fingerprint(self):
        return hashlib.sha256(
            dumps([self.mode, self.endpoint, self.model, self.dimension, self.revision]).encode()
        ).hexdigest()

    def embed(self, texts):
        if not 1 <= len(texts) <= 16 or sum(len(t) for t in texts) > 20000:
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_BATCH_LIMIT")
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        try:
            deadline = time.monotonic() + 20
            with httpx.Client(
                timeout=httpx.Timeout(2, connect=5),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                with client.stream(
                    "POST",
                    self.endpoint + "/embeddings",
                    headers=headers,
                    json={"model": self.model, "input": texts},
                ) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    for part in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise KnowledgeError("KNOWLEDGE_EMBEDDING_TIMEOUT")
                        raw.extend(part)
                        if len(raw) > 2 * 1024 * 1024:
                            raise KnowledgeError("KNOWLEDGE_EMBEDDING_RESPONSE_LIMIT")
            payload = json.loads(raw)
            rows = sorted(payload["data"], key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(texts))):
                raise KnowledgeError("KNOWLEDGE_EMBEDDING_SHAPE")
            return vectors([row["embedding"] for row in rows], len(texts), self.dimension)
        except KnowledgeError:
            raise
        except Exception:
            # HTTP body/URL/credentials are never stored in errors or traces.
            raise KnowledgeError("KNOWLEDGE_EMBEDDING_UNAVAILABLE") from None


class VectorStore:
    def __init__(self, store):
        self.store = store

    def put(self, chunks, provider):
        if not chunks:
            return
        collection_ids = {row.collection_id for row in chunks}
        with self.store.sessions() as db:
            count = db.scalar(
                select(func.count())
                .select_from(KnowledgeVector)
                .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeVector.chunk_id)
                .where(
                    KnowledgeChunk.collection_id.in_(collection_ids),
                    KnowledgeVector.chunk_id.not_in([row.id for row in chunks]),
                )
            )
            if count + len(chunks) > 10000:
                raise KnowledgeError("KNOWLEDGE_VECTOR_STORE_LIMIT")
        embedded = vectors(
            provider.embed([r.content for r in chunks]), len(chunks), provider.dimension
        )
        with self.store.sessions.begin() as db:
            for row, vector in zip(chunks, embedded):
                current = db.get(KnowledgeChunk, row.id)
                if (
                    not current
                    or current.version != row.version
                    or current.content_hash != row.content_hash
                ):
                    raise KnowledgeError("KNOWLEDGE_STALE_EVIDENCE")
                db.execute(delete(KnowledgeVector).where(KnowledgeVector.chunk_id == row.id))
                db.add(
                    KnowledgeVector(
                        chunk_id=row.id,
                        fingerprint=provider.fingerprint,
                        dimension=provider.dimension,
                        vector_json=dumps(vector),
                    )
                )

    def search(self, collection_ids, query, provider, *, limit=100):
        vector = vectors(provider.embed([query]), 1, provider.dimension)[0]
        with self.store.sessions() as db:
            rows = db.execute(
                select(KnowledgeVector, KnowledgeChunk)
                .join(KnowledgeChunk, KnowledgeChunk.id == KnowledgeVector.chunk_id)
                .where(
                    KnowledgeChunk.collection_id.in_(collection_ids),
                    KnowledgeVector.fingerprint == provider.fingerprint,
                    KnowledgeVector.dimension == provider.dimension,
                )
                .order_by(KnowledgeVector.chunk_id)
                .limit(10001)
            ).yield_per(128)
            ranked = []
            for index, (record, chunk) in enumerate(rows):
                if index >= 10000:
                    raise KnowledgeError("KNOWLEDGE_VECTOR_SCAN_LIMIT")
                stored = vectors([json.loads(record.vector_json)], 1, provider.dimension)[0]
                score = sum(a * b for a, b in zip(vector, stored))
                if score > 0:
                    ranked.append((chunk.id, score))
            return sorted(ranked, key=lambda v: (-v[1], v[0]))[:limit]


def reciprocal_rank_fusion(keyword, semantic):
    scores = {}
    for ranking in (keyword, semantic):
        for rank, identity in enumerate(ranking, 1):
            scores[identity] = scores.get(identity, 0) + 1 / (60 + rank)
    return sorted(scores, key=lambda identity: (-scores[identity], identity)), scores
