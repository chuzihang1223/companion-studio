"""The bundled 16 cases run in owned processes, including a genuine hard exit/restart."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.migrations import migrate
from app.schemas.evaluation import BenchmarkCase, RunRequest
from app.services.evaluation.engine import AgentEvaluationEngine
from app.services.evaluation.grader import EvaluationGrader
from app.services.evaluation.metrics import MetricsCollector
from app.services.evaluation.registry import BenchmarkRegistry
from app.services.evaluation.repository import EvaluationRepository, empty_result
from app.services.evaluation.reports import EvaluationReportService


def test_recovery_dataset_rejects_unknown_tools_paths_and_live_mode():
    registry = BenchmarkRegistry("recovery-core")
    assert len(registry.cases) == 16
    assert set(registry.by_id) == {f"REC-{i:03d}" for i in range(1, 17)}
    for case in registry.cases:
        BenchmarkCase.model_validate(case.model_dump())
    with pytest.raises(ValueError):
        BenchmarkRegistry("../../outside")
    with pytest.raises(ValueError):
        registry.select(["EVAL-001"])
    with pytest.raises(ValueError):
        RunRequest(
            dataset_id="recovery-core",
            mode="live_model",
            live_consent=True,
            max_requests=1,
            token_budget=100,
        )


def test_sixteen_recovery_cases_through_existing_engine_persistence_trace_and_export(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'index.db').as_posix()}")
    migrate(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = EvaluationRepository(factory)
    runner = AgentEvaluationEngine(tmp_path / "attempts", repository)

    async def run():
        await runner.initialize()
        created = await runner.start(
            RunRequest(dataset_id="recovery-core", concurrency=2, timeout_seconds=30), FastAPI()
        )
        await asyncio.gather(*list(runner.tasks.values()))
        rows = repository.results(created["run_id"], 0, 100)["items"]
        assert len(rows) == 16
        assert all(row["execution_status"] == "PASS" for row in rows), [
            (row["case_id"], row["error_code"], row["result"].get("evidence"))
            for row in rows
            if row["execution_status"] != "PASS"
        ]
        metrics = repository.detail(created["run_id"])["metrics"]
        assert metrics["counts"] == {"PASS": 16}
        assert metrics["real_task_success_rate"] is None
        assert metrics["recovery"]["attempt_count"] >= 8
        assert metrics["recovery"]["duplicate_execution_count"] == 0
        assert metrics["recovery"]["checkpoint_checks"] >= 6
        assert metrics["recovery"]["unresolved_side_effects"] >= 2
        assert metrics["recovery"]["cost"]["value"] is None
        crash = next(row for row in rows if row["case_id"] == "REC-012")
        assert crash["duration_ms"] is None
        assert crash["result"]["evidence"]["worker_restart_observed"] is True
        assert crash["result"]["evidence"]["file_not_rewritten"] is True
        assert crash["result"]["evidence"]["trace_has_interruption"] is True
        assert crash["result"]["tool_calls"] == 1  # persisted entry observation survives crash
        assert len(crash["result"]["trace_scopes"]) == 2
        assert not runner.environment.active
        assert not list((runner.environment.root / "_w").iterdir())
        for format in ("json", "csv", "markdown"):
            report = EvaluationReportService(repository).export(created["run_id"], format)[
                "content"
            ]
            assert "REC-012" in report and "recovery" in report.lower()
            assert "api_key" not in report
        await runner.close()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_recovery_metrics_do_not_invent_goal_success_usage_or_durations():
    result = dict(
        empty_result("PASS", None),
        duration_ms=None,
        recovery_metrics={
            "attempt_count": 1,
            "successful_attempts": 1,
            "duration_ms": 25,
            "checkpoint_checks": 2,
            "validated_checkpoints": 1,
            "goal_completed": False,
            "unresolved_side_effects": 1,
        },
    )
    metrics = MetricsCollector.aggregate([result], "mock")
    assert metrics["recovery_success_rate"] == 1
    assert metrics["recovery"]["checkpoint_validation_rate"] == 0.5
    assert metrics["recovery"]["goal_completion_rate"] == 0
    assert metrics["average_duration_ms"] is None and metrics["p95_ms"] is None
    assert metrics["model_usage"]["total_tokens"] is None


def test_missing_trace_is_failed_evidence_even_if_other_trace_is_complete():
    case = BenchmarkRegistry().by_id["EVAL-001"]
    graded = EvaluationGrader().grade(
        case,
        {
            "evidence": {"route": "chat", "trace_missing": True},
            "selected_tools": [],
            "duration_ms": 10,
            "summary": {},
        },
        [{"incomplete": False}],
    )
    assert not graded["passed"] and graded["error_code"] == "TRACE_INCOMPLETE"


def test_all_bundled_registry_identities_versions_and_counts_match():
    for dataset_id in BenchmarkRegistry.available():
        registry = BenchmarkRegistry(dataset_id)
        assert registry.dataset_id == dataset_id
        assert registry.version == "1.0.0"
        assert registry.public()["case_count"] == len(registry.cases)
        assert all(case.dataset_version == registry.version for case in registry.cases)
