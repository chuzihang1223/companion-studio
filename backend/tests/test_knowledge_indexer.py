import asyncio
import json
import threading
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexJob
from app.services.knowledge.contracts import CreateCollection, KnowledgeError, SearchQuery
from app.services.knowledge.embedding import (
    EndpointEmbeddingProvider,
    VectorStore,
    reciprocal_rank_fusion,
)
from app.services.knowledge.indexer import IndexManager
from app.services.knowledge.retriever import HybridRetriever


def prepare(corpus):
    store, root, owner = corpus
    item = store.create(CreateCollection(name="Fixture knowledge"))
    item = store.update(item["id"], 1, add_root=str(root))
    store.grant(owner, "assistant", [item["id"]])
    return item


def test_filters_apply_before_candidate_limit_and_path_is_directory_boundary(corpus):
    store, root, owner = corpus
    for i in range(105):
        (root / f"short-{i}.py").write_text("needle", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs-other").mkdir()
    (root / "docs-other" / "short.md").write_text("needle", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("needle " + " ".join(f"long_identifier_{i}" for i in range(35)), encoding="utf-8")
    item = prepare(corpus)
    assert index(store, item["id"])["state"] == "COMPLETED"
    result = search(store, item, owner, "needle", file_types=[".md"], path_scope="docs")
    assert [r["relative_path"] for r in result["results"]] == ["docs/guide.md"]


def index(store, collection_id):
    async def run():
        manager = IndexManager(store)
        job_id = await manager.start(collection_id)
        await manager.tasks[job_id]
        return manager.jobs(collection_id)[0]

    return asyncio.run(run())


def search(store, item, owner, query, **kwargs):
    return HybridRetriever(store).search(
        SearchQuery(query=query, collection_ids=[item["id"]], **kwargs), owner, "assistant"
    )


def test_index_incremental_search_and_stale_citation(corpus):
    store, root, owner = corpus
    (root / "guide.md").write_text(
        "# 启动\n\n知识库支持增量索引。服务端口为 8794。", encoding="utf-8"
    )
    (root / "code.py").write_text(
        "def load_document():\n    return 'orchard apple evidence'\n", encoding="utf-8"
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "ignored.md").write_text("never index", encoding="utf-8")
    item = prepare(corpus)
    first = index(store, item["id"])
    assert first["state"] == "COMPLETED" and first["progress"]["new"] == 2
    second = index(store, item["id"])
    assert second["progress"]["unchanged"] == 2
    zh = search(store, item, owner, "增量索引")
    assert zh["results"][0]["relative_path"] == "guide.md"
    result = search(store, item, owner, "load_document")["results"][0]
    assert result["location"]["symbol"] == "load_document"
    resolver = HybridRetriever(store).citations
    assert resolver.resolve(result, owner, "assistant")["verification"] == "source_hash_verified"
    (root / "code.py").write_text("def new_function():\n    return 'changed'\n", encoding="utf-8")
    with pytest.raises(KnowledgeError, match="STALE_EVIDENCE"):
        resolver.resolve(result, owner, "assistant")
    assert search(store, item, owner, "load_document")["results"] == []
    assert index(store, item["id"])["progress"]["modified"] == 1
    (root / "code.py").unlink()
    assert index(store, item["id"])["progress"]["deleted"] == 1
    assert not search(store, item, owner, "new_function")["results"]


def test_failures_invalidate_old_chunks_and_hybrid_fallback(corpus):
    store, root, owner = corpus
    source = root / "report.txt"
    source.write_text("knowledge evidence 中文查询", encoding="utf-8")
    item = prepare(corpus)
    index(store, item["id"])
    result = search(store, item, owner, "knowledge", mode="hybrid")
    assert result["mode"] == "keyword" and "keyword_fallback" in result["warnings"][0]
    # MATCH syntax and SQL are treated as lexical text, never executable syntax.
    search(store, item, owner, '" OR NOT *; DROP TABLE knowledge_chunks --')
    source.write_bytes(b"broken\x00binary")
    job = index(store, item["id"])
    assert job["progress"]["errors"] == 1
    assert not search(store, item, owner, "knowledge")["results"]
    with store.sessions() as db:
        assert db.scalars(select(KnowledgeChunk)).all() == []
        assert db.scalars(select(KnowledgeDocument)).one().status == "ERROR"


def test_cancel_pause_and_capacity(corpus, monkeypatch):
    store, root, _ = corpus
    (root / "first.txt").write_text("cancel evidence", encoding="utf-8")
    item = prepare(corpus)
    entered = threading.Event()
    from app.services.knowledge import indexer

    original = indexer.parse_isolated

    def slow(raw, kind, stop):
        entered.set()
        stop.wait(3)
        return original(raw, kind, stop)

    monkeypatch.setattr(indexer, "parse_isolated", slow)

    async def run():
        manager = IndexManager(store)
        job_id = await manager.start(item["id"])
        assert await asyncio.to_thread(entered.wait, 2)
        with pytest.raises(KnowledgeError, match="JOB_ACTIVE"):
            await manager.start(item["id"])
        manager.control(job_id, "pause")
        manager.control(job_id, "cancel")
        await asyncio.wait_for(manager.tasks[job_id], 2)
        assert manager.jobs(item["id"])[0]["state"] == "CANCELLED"

    asyncio.run(run())


def test_embeddings_explicit_consent_version_isolation(corpus):
    store, root, owner = corpus
    (root / "one.txt").write_text("knowledge orchard", encoding="utf-8")
    item = prepare(corpus)
    index(store, item["id"])

    def handler(request):
        payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [1.0, 0.5, 0.2]}
                    for i, _ in enumerate(payload["input"])
                ]
            },
        )

    config = {
        "mode": "LOCAL",
        "endpoint": "http://127.0.0.1:9/v1",
        "model": "fixture",
        "dimension": 3,
        "revision": "v1",
        "transport": httpx.MockTransport(handler),
    }
    with pytest.raises(KnowledgeError, match="CONSENT_REQUIRED"):
        EndpointEmbeddingProvider(**config)
    provider = EndpointEmbeddingProvider(**config, consent=True)
    with store.sessions() as db:
        chunks = db.scalars(select(KnowledgeChunk)).all()
    VectorStore(store).put(chunks, provider)
    retriever = HybridRetriever(store, {item["id"]: provider})
    result = retriever.search(
        SearchQuery(query="knowledge", collection_ids=[item["id"]], mode="hybrid"),
        owner,
        "assistant",
    )
    assert result["mode"] == "hybrid" and result["results"][0]["scores"]["rrf"] > 0
    other = EndpointEmbeddingProvider(**{**config, "revision": "v2"}, consent=True)
    assert VectorStore(store).search([item["id"]], "knowledge", other) == []
    ids, scores = reciprocal_rank_fusion(["one", "two"], ["two", "one"])
    assert set(ids) == {"one", "two"} and scores["one"] == scores["two"]
