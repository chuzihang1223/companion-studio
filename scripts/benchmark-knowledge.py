"""Offline performance probe with original generated documents and a fresh owned profile."""

import argparse
import asyncio
import ctypes
import json
import os
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4


def rss():
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    from ctypes import wintypes as w

    class Counters(ctypes.Structure):
        _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [
            (key, ctypes.c_size_t)
            for key in (
                "peak_working",
                "working",
                "peak_paged",
                "paged",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
                "private",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    call = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    call.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
    if not call(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError("Process memory observation unavailable")
    return counters.working


async def benchmark(home):
    from sqlalchemy import func, select
    from app.database.migrations import migrate
    from app.database.session import engine
    from app.models import Conversation
    from app.models.knowledge import KnowledgeChunk
    from app.services.knowledge.contracts import CollectionConfig, CreateCollection, SearchQuery
    from app.services.knowledge.service import KnowledgeService
    from app.services.evaluation.metrics import percentile

    migrate(engine)
    service = KnowledgeService(engine)
    owner = str(uuid4())
    with service.store.sessions.begin() as db:
        db.add(Conversation(id=owner, character_id="assistant"))
    reports = []
    for label, count, paragraphs in [
        ("100-documents", 100, 1),
        ("1000-documents", 1000, 1),
        ("10000-chunks", 100, 100),
    ]:
        root = home / label
        root.mkdir()
        for i in range(count):
            body = "\n\n".join(
                f"marker_{i}_{j} chapter_{j} 知识库增量索引。Original public synthetic evidence. "
                + "sample content " * 14
                for j in range(paragraphs)
            )
            (root / f"document-{i:04}.md").write_text(body, encoding="utf-8")
        collection = service.store.create(
            CreateCollection(
                name=label, config=CollectionConfig(max_documents=2000, max_chunks=20000)
            )
        )
        service.store.update(collection["id"], 1, add_root=str(root))
        service.store.grant(owner, "assistant", [collection["id"]])
        peaks, stop = [rss()], threading.Event()

        def sample():
            while not stop.wait(0.02):
                peaks.append(rss())

        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        try:
            started = time.perf_counter()
            job = await service.indexer.start(collection["id"])
            lag = []
            while not service.indexer.tasks[job].done():
                before = time.perf_counter()
                await asyncio.sleep(0.02)
                lag.append(max(0, (time.perf_counter() - before - 0.02) * 1000))
                if job not in service.indexer.tasks:
                    break
            elapsed = time.perf_counter() - started
            result = service.indexer.jobs(collection["id"])[0]
            if result["state"] != "COMPLETED" or result["progress"]["errors"]:
                raise RuntimeError(json.dumps(result, default=str))
            with service.store.sessions() as db:
                chunks = db.scalar(
                    select(func.count())
                    .select_from(KnowledgeChunk)
                    .where(KnowledgeChunk.collection_id == collection["id"])
                )
            latencies = []
            for i in range(30):
                result = service.retriever.search(
                    SearchQuery(query=f"marker_{i % count}_0", collection_ids=[collection["id"]]),
                    owner,
                    "assistant",
                )
                if not result["results"]:
                    raise RuntimeError("Synthetic target was not retrieved")
                latencies.append(result["duration_ms"])
            reports.append(
                {
                    "fixture": label,
                    "documents": count,
                    "chunks": chunks,
                    "index_seconds": elapsed,
                    "documents_per_second": count / elapsed,
                    "chunks_per_second": chunks / elapsed,
                    "retrieval_queries": len(latencies),
                    "retrieval_p50_ms": percentile(latencies, 0.5),
                    "retrieval_p95_ms": percentile(latencies, 0.95),
                    "observed_peak_rss_bytes": max(peaks),
                    "database_bytes": sum(p.stat().st_size for p in home.glob("profile/chat.db*")),
                    "backend_heartbeat_lag_p95_ms": percentile(lag, 0.95),
                    "real_renderer_fps": None,
                }
            )
        finally:
            stop.set()
            thread.join(2)
        print(json.dumps(reports[-1], ensure_ascii=False), flush=True)
    # Cancellation is an actual in-flight job, not an inferred timeout.
    started = time.perf_counter()
    job = await service.indexer.start(collection["id"], full=True)
    task = service.indexer.tasks[job]
    await asyncio.sleep(0.03)
    service.indexer.control(job, "cancel")
    await task
    cancelled = service.indexer.jobs(collection["id"])[0]
    result = {
        "schema_version": 1,
        "source": "original_offline_synthetic_documents",
        "external_requests": 0,
        "embedding": "DISABLED",
        "measurements": reports,
        "cancel_observed_seconds": time.perf_counter() - started,
        "cancel_state": cancelled["state"],
        "limits": [
            "Backend heartbeat is not renderer FPS.",
            "No real embedding model, remote service or model quality was measured.",
            "RSS samples include the Python process and SQLite, not independent parser child peaks.",
        ],
    }
    (home / "performance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    await service.close()
    engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    options = parser.parse_args()
    home = options.directory.resolve()
    if home.exists():
        raise ValueError("A fresh performance directory is required")
    home.mkdir(parents=True)
    profile = home / "profile"
    profile.mkdir()
    (profile / ".env").write_text("", encoding="utf-8")
    os.environ.update(
        COMPANION_DATA_DIR=str(profile),
        COMPANION_CLEAN_DISTRIBUTION="1",
        OPENAI_API_KEY="",
        OPENAI_BASE_URL="http://127.0.0.1:9/v1",
        FISH_API_KEY="",
        KNOWLEDGE_EMBEDDING_API_KEY="",
        TEMP=str(home),
        TMP=str(home),
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    asyncio.run(benchmark(home))


if __name__ == "__main__":
    main()
