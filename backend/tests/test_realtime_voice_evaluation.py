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


def test_voice_dataset_has_no_implicit_external_or_audio_device_permission():
    assert len(BenchmarkRegistry("voice-core").cases) == 36
    with pytest.raises(ValueError):
        RunRequest(
            dataset_id="voice-core",
            mode="live_model",
            live_consent=True,
            max_requests=1,
            token_budget=100,
        )


def test_thirty_six_voice_cases_preserve_evidence_boundaries(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "index.db").as_posix())
    migrate(engine)
    repository = EvaluationRepository(sessionmaker(engine, expire_on_commit=False))
    runner = AgentEvaluationEngine(tmp_path / "attempts", repository)

    async def run():
        await runner.initialize()
        created = await runner.start(
            RunRequest(dataset_id="voice-core", concurrency=2, timeout_seconds=30), FastAPI()
        )
        await asyncio.gather(*list(runner.tasks.values()))
        rows = repository.results(created["run_id"], 0, 100)["items"]
        content = EvaluationReportService(repository).export(created["run_id"], "json")["content"]
        (tmp_path / "voice-result.json").write_text(content, encoding="utf-8")
        assert len(rows) == 36
        assert all(row["execution_status"] in {"PASS", "BLOCKED"} for row in rows), [
            (r["case_id"], r["result"])
            for r in rows
            if r["execution_status"] not in {"PASS", "BLOCKED"}
        ]
        assert any(row["execution_status"] == "BLOCKED" for row in rows)
        assert repository.detail(created["run_id"])["metrics"]["real_task_success_rate"] is None
        for row in json.loads(content)["results"]:
            assert row["result"]["evidence"]["physical_microphone"] is False
            assert row["result"]["evidence"]["external_requests"] == 0
        assert not runner.environment.active
        await runner.close()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()
