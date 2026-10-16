import asyncio
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


def test_workflow_dataset_blocks_real_model_without_new_authorization():
    assert len(BenchmarkRegistry("workflow-core").cases) == 20
    with pytest.raises(ValueError):
        RunRequest(
            dataset_id="workflow-core",
            mode="live_model",
            live_consent=True,
            max_requests=1,
            token_budget=100,
        )


def test_twenty_workflow_cases_original_evaluation_workers(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "index.db").as_posix())
    migrate(engine)
    repository = EvaluationRepository(sessionmaker(engine, expire_on_commit=False))
    runner = AgentEvaluationEngine(tmp_path / "attempts", repository)

    async def run():
        await runner.initialize()
        created = await runner.start(
            RunRequest(dataset_id="workflow-core", concurrency=2, timeout_seconds=35), FastAPI()
        )
        assert created["snapshot"]["environment"]["fixture"] == "workflow-fixture-v1"
        await asyncio.gather(*list(runner.tasks.values()))
        rows = repository.results(created["run_id"], 0, 100)["items"]
        report = EvaluationReportService(repository).export(created["run_id"], "json")
        (tmp_path / "workflow-result.json").write_text(report["content"], encoding="utf-8")
        assert len(rows) == 20
        assert all(row["execution_status"] == "PASS" for row in rows), [
            (row["case_id"], row["result"]) for row in rows if row["execution_status"] != "PASS"
        ]
        assert repository.detail(created["run_id"])["metrics"]["real_task_success_rate"] is None
        assert not runner.environment.active
        await runner.close()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()
