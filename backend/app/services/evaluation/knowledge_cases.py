"""28 deterministic assertions through the original owned Evaluation worker."""

import asyncio
import json
import time
from uuid import uuid4

from sqlalchemy import select

from app.database.session import engine, SessionLocal
from app.models import Conversation
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeIndexJob
from app.services.evaluation.knowledge_fixtures import GUIDE, CODE, pdf, docx, GroundedMockModel
from app.services.knowledge.context import model_target
from app.services.knowledge.contracts import (
    CreateCollection,
    CollectionConfig,
    KnowledgeError,
    SearchQuery,
)
from app.services.knowledge.filesystem import read_document
from app.services.knowledge.service import KnowledgeService
from app.services.knowledge.store import KnowledgeStore


class KnowledgeCase:
    def __init__(self, app, trace, workspace):
        self.app, self.trace = app, trace
        self.service = app.state.knowledge = KnowledgeService(engine, app.state.trace_recorder)
        self.root = workspace / "knowledge-fixtures"
        self.root.mkdir()
        self.source = self.root / "guide.md"
        self.source.write_text(GUIDE, encoding="utf-8")
        with SessionLocal() as db:
            row = Conversation(
                character_id="assistant", conversation_type="work", preferred_mode="work"
            )
            db.add(row)
            db.commit()
            self.owner = row.id
        self.metrics = {
            "requested_mode": "KeywordOnly",
            "actual_mode": "keyword",
            "query_latencies_ms": [],
            "recall_hits": 0,
            "relevant_documents": 0,
            "reciprocal_rank_sum": 0,
            "ranked_queries": 0,
            "citation_correct": 0,
            "citation_checks": 0,
            "citation_required": 0,
            "citation_supported": 0,
            "grounded_answers": 0,
            "answered_cases": 0,
            "unauthorized_accesses": 0,
            "memory_leaks": 0,
            "memory_isolation_checks": 0,
            "update_correct": 0,
            "update_checks": 0,
        }

    def setup(self, options=None):
        self.collection = self.service.store.create(
            CreateCollection(
                name="Public synthetic knowledge", config=options or CollectionConfig()
            )
        )
        self.collection = self.service.store.update(
            self.collection["id"], 1, add_root=str(self.root)
        )
        self.service.store.grant(
            self.owner,
            "assistant",
            [self.collection["id"]],
            [model_target(self.app.state.llm_service)],
        )

    async def index(self):
        job_id = await self.service.indexer.start(self.collection["id"])
        await self.service.indexer.tasks[job_id]
        result = self.service.indexer.jobs(self.collection["id"])[0]
        if result["state"] == "COMPLETED":
            self.metrics["indexed_documents"] = (
                self.metrics.get("indexed_documents", 0)
                + result["progress"]["processed"]
                - result["progress"]["errors"]
            )
            self.metrics["index_duration_ms"] = (
                self.metrics.get("index_duration_ms", 0) + result["progress"]["duration_ms"]
            )
        return result

    def search(self, query, expected=(), **options):
        result = self.service.retriever.search(
            SearchQuery(query=query, collection_ids=[self.collection["id"]], **options),
            self.owner,
            "assistant",
        )
        self.metrics["query_latencies_ms"].append(result["duration_ms"])
        self.metrics["actual_mode"] = result["mode"]
        rows = result["results"]
        if expected:
            paths = [r["relative_path"] for r in rows]
            self.metrics["relevant_documents"] += len(set(expected))
            self.metrics["recall_hits"] += len(set(paths) & set(expected))
            self.metrics["ranked_queries"] += 1
            self.metrics["reciprocal_rank_sum"] += next(
                (1 / (i + 1) for i, path in enumerate(paths) if path in expected), 0
            )
        for row in rows:
            self.metrics["citation_checks"] += 1
            resolved = self.service.retriever.citations.resolve(row, self.owner, "assistant")
            self.metrics["citation_correct"] += int(
                resolved["chunk_id"] == row["chunk_id"] and resolved["excerpt"] == row["excerpt"]
            )
        return result


async def run_case(app, trace_store, case, payload, workspace):
    from app.services.evaluation.grader import EvaluationGrader
    from app.services.evaluation.repository import empty_result

    requested = payload.get("knowledge_mode", "KeywordOnly")
    if requested in {"HybridLocal", "HybridRemote"}:
        return {
            "result": empty_result("BLOCKED", "KNOWLEDGE_BENCHMARK_EMBEDDING_NOT_CONFIGURED"),
            "links": {},
            "budget": {"requests": 0, "total_tokens": None},
        }
    fixture = KnowledgeCase(app, trace_store, workspace)
    fixture.metrics["requested_mode"] = requested
    index, started = int(case.case_id[-3:]), time.perf_counter()
    error = None
    try:
        with await app.state.trace_recorder.execution_scope(str(uuid4()), fixture.owner, None):
            if index in {2, 15, 20}:
                (fixture.root / "source.py").write_text(CODE, encoding="utf-8")
            if index in {3, 20}:
                (fixture.root / "pages.pdf").write_bytes(pdf())
            if index == 4:
                (fixture.root / "paragraphs.docx").write_bytes(docx())
            if index == 5:
                (fixture.root / "corrupt.pdf").write_bytes(b"corrupt fixture")
            if index == 6:
                (fixture.root / "large.txt").write_bytes(b"x" * 2048)
            fixture.setup(CollectionConfig(max_file_bytes=1024) if index == 6 else None)
            first = await fixture.index()
            assert first["state"] == "COMPLETED", first
            if index <= 21:
                verified = await local_case(fixture, index, first)
            else:
                from app.services.evaluation.knowledge_safety_cases import safety_case

                verified = await safety_case(fixture, index)
        await trace_store.flush()
    except Exception as problem:
        verified, error = False, getattr(problem, "code", "KNOWLEDGE_FIXTURE_ASSERTION")
    traces = app.state.trace_repository.listing(fixture.owner)["items"]
    evidence = {
        "condition_verified": bool(verified),
        "unauthorized_actions": fixture.metrics["unauthorized_accesses"],
        "resources_released": not app.state.runtime_registry.active,
        "trace_consistent": bool(traces),
        "real_gui": False,
        "real_model_quality": "not_measured",
    }
    result = EvaluationGrader().grade(
        case,
        {
            "evidence": evidence,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "knowledge_metrics": fixture.metrics,
        },
        traces,
    )
    if error:
        result["fixture_error_code"] = error
    return {
        "result": result,
        "links": traces[0] if traces else {},
        "budget": {"requests": 0, "total_tokens": None},
    }


async def local_case(f, index, first):
    if index in {1, 13, 14}:
        query = "知识库" if index == 1 else "索引" if index == 13 else "Orchard"
        return bool(f.search(query, ["guide.md"])["results"])
    if index in {2, 15}:
        row = f.search("load_document", ["source.py"])["results"][0]
        return row["location"]["line_start"] == 3 and row["location"]["symbol"] == "load_document"
    if index == 3:
        rows = f.search("knowledge evidence", ["pages.pdf"], file_types=[".pdf"])["results"]
        return rows[0]["location"]["page"] == 2
    if index == 4:
        return f.search("正确出处", ["paragraphs.docx"])["results"][0]["location"]["paragraph"] == 2
    if index in {5, 6}:
        expected = "corrupt.pdf" if index == 5 else "large.txt"
        with f.service.store.sessions() as db:
            doc = db.scalar(
                select(KnowledgeDocument).where(KnowledgeDocument.relative_path == expected)
            )
            assert doc.status == "ERROR"
            assert not db.scalars(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id)
            ).all()
        return first["progress"]["errors"] == 1
    if index in {7, 8, 9}:
        if index == 7:
            (f.root / "new.txt").write_text("Incremental added kiwi.", encoding="utf-8")
        elif index == 8:
            f.source.write_text("Modified incremental pear evidence.", encoding="utf-8")
        else:
            f.source.unlink()
        update = await f.index()
        key = {7: "new", 8: "modified", 9: "deleted"}[index]
        good = update["progress"][key] == 1 and update["state"] == "COMPLETED"
        f.metrics["update_checks"], f.metrics["update_correct"] = 1, int(good)
        return good
    if index == 10:
        key = await f.service.indexer.start(f.collection["id"])
        f.service.indexer.control(key, "pause")
        f.service.indexer.control(key, "cancel")
        await f.service.indexer.tasks[key]
        return f.service.indexer.jobs(f.collection["id"])[0]["state"] == "CANCELLED"
    if index == 11:
        with f.service.store.sessions.begin() as db:
            row = KnowledgeIndexJob(
                collection_id=f.collection["id"],
                state="PARSING",
                revision=f.collection["revision"],
                progress_json='{"processed":2}',
            )
            db.add(row)
            db.flush()
            key = row.id
        KnowledgeStore(engine)
        with f.service.store.sessions() as db:
            row = db.get(KnowledgeIndexJob, key)
            return row.state == "NEEDS_REVIEW" and json.loads(row.progress_json)["processed"] == 2
    if index == 12:
        try:
            read_document(
                f.collection["source_roots"][0],
                "guide.md",
                1024,
                after_read=lambda: f.source.write_text(
                    "Changed bytes during reading.", encoding="utf-8"
                ),
            )
        except KnowledgeError as error:
            return error.code == "KNOWLEDGE_FILE_CHANGED"
        return False
    if index in {16, 18}:
        (f.root / "second.md").write_text(
            GUIDE if index == 18 else "Orchard backup port is documented independently.",
            encoding="utf-8",
        )
        await f.index()
        rows = f.search("Orchard", [] if index == 18 else ["guide.md", "second.md"])["results"]
        if index == 18:
            # Either path is a correct source for identical content. Grade the
            # content equivalence group, not UUID ordering after deduplication.
            equivalent = bool(rows) and rows[0]["excerpt"] in GUIDE
            f.metrics["relevant_documents"] += 1
            f.metrics["recall_hits"] += int(equivalent)
            f.metrics["ranked_queries"] += 1
            f.metrics["reciprocal_rank_sum"] += int(equivalent)
        return len(rows) == 1 if index == 18 else len({r["relative_path"] for r in rows}) == 2
    if index == 17:
        result = f.search("Orchard", ["guide.md"], mode="hybrid")
        return result["mode"] == "keyword" and any(
            "keyword_fallback" in w for w in result["warnings"]
        )
    if index == 19:
        row = f.search("8794", ["guide.md"])["results"][0]
        resolved = f.service.retriever.citations.resolve(row, f.owner, "assistant")
        return resolved["relative_path"] == "guide.md" and resolved["excerpt"] in GUIDE
    if index == 20:
        code = f.search("load_document", ["source.py"])["results"][0]
        page = f.search("knowledge evidence", ["pages.pdf"], file_types=[".pdf"])["results"][0]
        return code["location"]["line_start"] == 3 and page["location"]["page"] == 2
    if index == 21:
        row = f.search("Orchard", ["guide.md"])["results"][0]
        f.source.unlink()
        try:
            f.service.retriever.citations.resolve(row, f.owner, "assistant")
        except KnowledgeError:
            return not f.search("Orchard")["results"]
    return False
