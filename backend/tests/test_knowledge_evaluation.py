import asyncio
from pathlib import Path

from fastapi import FastAPI
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.migrations import migrate
from app.schemas.evaluation import RunRequest
from app.services.evaluation.engine import AgentEvaluationEngine
from app.services.evaluation.registry import BenchmarkRegistry
from app.services.evaluation.repository import EvaluationRepository
from app.services.evaluation.reports import EvaluationReportService


def test_knowledge_dataset_modes_and_no_external_model():
    assert len(BenchmarkRegistry("knowledge-core").cases) == 28
    with pytest.raises(ValueError):
        RunRequest(
            dataset_id="knowledge-core",
            mode="live_model",
            live_consent=True,
            max_requests=1,
            token_budget=100,
        )
    for mode in ("KeywordOnly", "HybridLocal", "HybridRemote", "MockRAG"):
        assert RunRequest(dataset_id="knowledge-core", knowledge_mode=mode).knowledge_mode == mode


def test_twenty_eight_knowledge_cases_original_owned_workers(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "index.db").as_posix())
    migrate(engine)
    repository = EvaluationRepository(sessionmaker(engine, expire_on_commit=False))
    runner = AgentEvaluationEngine(tmp_path / "attempts", repository)

    async def run():
        await runner.initialize()
        created = await runner.start(
            RunRequest(dataset_id="knowledge-core", concurrency=2, timeout_seconds=35), FastAPI()
        )
        await asyncio.gather(*list(runner.tasks.values()))
        rows = repository.results(created["run_id"], 0, 100)["items"]
        report = EvaluationReportService(repository).export(created["run_id"], "json")
        (Path(__file__).parents[2] / ".cache" / "knowledge-benchmark.json").write_text(
            report["content"], encoding="utf-8"
        )
        assert len(rows) == 28
        assert all(row["execution_status"] == "PASS" for row in rows), [
            (row["case_id"], row["result"]) for row in rows if row["execution_status"] != "PASS"
        ]
        metrics = repository.detail(created["run_id"])["metrics"]
        assert metrics["real_task_success_rate"] is None
        assert metrics["knowledge"]["unauthorized_access_count"] == 0
        assert metrics["knowledge"]["memory_isolation"] == 1
        assert metrics["knowledge"]["answered_cases"] == 1
        assert not runner.environment.active
        await runner.close()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()
