import asyncio
import json

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


def test_dataset_and_live_mode_boundaries():
    registry = BenchmarkRegistry("mcp-skills-core")
    assert len(registry.cases) == 20
    assert set(registry.by_id) == {f"MCP-{i:03}" for i in range(1, 21)}
    with pytest.raises(ValueError):
        RunRequest(
            dataset_id="mcp-skills-core",
            mode="live_model",
            live_consent=True,
            max_requests=1,
            token_budget=100,
        )


def test_twenty_mcp_skill_cases_real_stdio_runtime_trace_and_existing_engine(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "index.db"))
    migrate(engine)
    repository = EvaluationRepository(sessionmaker(engine, expire_on_commit=False))
    runner = AgentEvaluationEngine(tmp_path / "attempts", repository)

    async def run():
        await runner.initialize()
        created = await runner.start(
            RunRequest(dataset_id="mcp-skills-core", concurrency=2, timeout_seconds=30), FastAPI()
        )
        await asyncio.gather(*list(runner.tasks.values()))
        rows = repository.results(created["run_id"], 0, 100)["items"]
        report = EvaluationReportService(repository).export(created["run_id"], "json")
        (tmp_path / "mcp-result.json").write_text(report["content"], encoding="utf-8")
        assert len(rows) == 20
        assert all(row["execution_status"] == "PASS" for row in rows), [
            (row["case_id"], row["result"]) for row in rows if row["execution_status"] != "PASS"
        ]
        summary = repository.detail(created["run_id"])["metrics"]
        assert summary["real_task_success_rate"] is None
        assert summary["mcp"]["real_third_party_reliability"] is None
        assert summary["mcp"]["permission_denials"] >= 1
        assert summary["mcp"]["schema_failures"] >= 1
        assert not runner.environment.active
        assert not list((runner.environment.root / "_w").iterdir())
        await runner.close()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()
