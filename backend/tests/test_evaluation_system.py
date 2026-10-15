"""真实 Agent Runtime/Trace + 合成边界，通过生产 API 验证 20 项完整链路。"""

import asyncio
from contextlib import asynccontextmanager
import json
import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app.api.evaluation import router
from app.database.migrations import migrate
from app.models import EvaluationRun, EvaluationAttempt, Message
from app.schemas.evaluation import RunRequest
from app.services.evaluation.engine import AgentEvaluationEngine
from app.services.evaluation.grader import EvaluationGrader
from app.services.evaluation.metrics import MetricsCollector, compare_conditions, percentile
from app.services.evaluation.registry import BenchmarkRegistry
from app.services.evaluation.repository import EvaluationRepository, empty_result
from app.services.evaluation.reports import EvaluationReportService, csv_cell
from app.services.desktop.goal_verifier import GoalVerifier
from app.services.llm.usage import UsageBudget, before_request, observe_response, usage_scope


@pytest.fixture
def evaluation(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "local-evaluation-test")
    db_engine = create_engine(
        f"sqlite:///{(tmp_path / 'index.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    migrate(db_engine)
    factory = sessionmaker(db_engine, expire_on_commit=False)
    repository = EvaluationRepository(factory)
    value = AgentEvaluationEngine(tmp_path / "attempts", repository)

    @asynccontextmanager
    async def lifespan(app):
        await value.initialize()
        yield
        await value.close()

    app = FastAPI(lifespan=lifespan)
    app.state.evaluation = value
    app.include_router(router)
    yield value, app, factory
    db_engine.dispose()


HEADERS = {"X-Companion-Control-Token": "local-evaluation-test"}


def test_twenty_case_api_runtime_trace_database_and_exports(evaluation):
    value, app, factory = evaluation
    with TestClient(app) as client:
        assert client.get("/api/evaluation/datasets").status_code == 403
        assert (
            len(client.get("/api/evaluation/datasets/studio-core", headers=HEADERS).json()["cases"])
            == 20
        )
        started = client.post("/api/evaluation/runs", headers=HEADERS, json={"concurrency": 2})
        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]
        url = "/api/evaluation/runs/" + run_id
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            run = client.get(url, headers=HEADERS).json()
            if run["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.1)
        assert run["status"] == "COMPLETED", run
        metrics = client.get(url + "/metrics", headers=HEADERS).json()
        rows = client.get(url + "/results?limit=100", headers=HEADERS).json()["items"]
        assert len(rows) == 20
        assert metrics["counts"] == {"PASS": 20}, [
            (r["case_id"], r["result"]) for r in rows if r["execution_status"] != "PASS"
        ]
        assert metrics["real_task_success_rate"] is None
        assert metrics["model_usage"]["status"] == "Not applicable (simulated)"
        assert metrics["api_cost"]["value"] is None
        assert not value.environment.active
        assert len({r["trace_id"] for r in rows}) == 20
        for row in rows:
            assert row["task_id"] and row["conversation_id"]
            trace = client.get(
                url + "/attempts/" + row["attempt_id"] + "/trace/events", headers=HEADERS
            ).json()
            assert trace["items"] and all(e["task_id"] == row["task_id"] for e in trace["items"])
            assert not (value.environment.path(run_id, row["attempt_id"]) / "workspace").exists()
        first = rows[0]
        assert (
            client.get(
                url + "/attempts/" + str(uuid4()) + "/trace/events", headers=HEADERS
            ).status_code
            == 404
        )
        assert (
            client.get(
                url + "/attempts/" + first["attempt_id"] + "/trace/events?limit=251",
                headers=HEADERS,
            ).status_code
            == 422
        )
        assert len(client.get(url + "/results?limit=7", headers=HEADERS).json()["items"]) == 7
        for format in ("json", "csv", "markdown"):
            exported = client.get(url + "/export?format=" + format, headers=HEADERS).json()
            assert exported["content"] and not exported["partial"]
            assert "local-evaluation-test" not in exported["content"]
            assert "api_key" not in exported["content"] and "base_url" not in exported["content"]
        assert client.get(
            "/api/evaluation/compare", params={"left": run_id, "right": run_id}, headers=HEADERS
        ).json()["conditions_equal"]
        with factory() as db:
            assert db.scalar(select(EvaluationRun)).id == run_id
            assert len(list(db.scalars(select(EvaluationAttempt)))) == 20
        # Restart restores summaries and original Trace without executing anything.
        restored = EvaluationRepository(factory)
        restored.recover()
        assert restored.detail(run_id)["metrics"]["valid_count"] == 20
        assert not value.tasks
        assert client.delete(url, headers=HEADERS).json()["deleted"]
        assert client.get(url, headers=HEADERS).status_code == 404


def test_metrics_exclude_unrun_statuses_and_use_interpolated_percentiles():
    rows = [
        dict(empty_result("PASS", None), duration_ms=i, tool_calls=2, retry_count=0)
        for i in range(1, 101)
    ]
    rows += [
        empty_result(status, "CONTROLLED")
        for status in ("BLOCKED", "NOT_RUN", "UNKNOWN", "ERROR", "CANCELLED")
    ]
    metrics = MetricsCollector.aggregate(rows, "mock")
    assert metrics["valid_count"] == 100 and metrics["success_rate"] == 1
    assert metrics["p50_ms"] == 50.5 and metrics["p95_ms"] == pytest.approx(95.05)
    assert metrics["recovery_success_rate"] is None
    assert metrics["model_usage"]["total_tokens"] is None
    assert percentile([], 0.5) is None
    assert MetricsCollector.aggregate([], "live_model")["success_rate"] is None
    assert not compare_conditions({"dataset_version": "1"}, {"dataset_version": "2"})[
        "dataset_compatible"
    ]
    assert compare_conditions({"model": "a"}, {"model": "b"})["differences"][0]["field"] == "model"


def test_grader_ignores_natural_language_claims_and_csv_injection():
    case = BenchmarkRegistry().by_id["EVAL-006"]
    result = EvaluationGrader().grade(
        case,
        {
            "selected_tools": ["workspace.create"],
            "evidence": {"actual_file_exists": False},
            "duration_ms": 1,
            "content": "created successfully",
        },
        [{"incomplete": False}],
    )
    assert not result["passed"] and result["completion_status"] == "FAIL"
    assert csv_cell("=formula") == "'=formula"
    assert not GoalVerifier().verify(
        "打开记事本",
        [{"name": "app.launch", "status": "done", "result": {"action": "launch_requested"}}],
    )


def test_usage_is_observed_only_and_unknown_prevents_more_requests():
    budget = UsageBudget(2, 100, 20)
    with usage_scope(budget):
        assert before_request({})["max_tokens"] == 20
        assert budget.public()["status"] == "Unavailable"
        with pytest.raises(ValueError):
            before_request({})
        observe_response(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        )
        assert budget.public()["total_tokens"] == 15
        before_request({})
        observe_response(
            {"usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}}
        )
        assert budget.public()["total_tokens"] == 26
        with pytest.raises(ValueError):
            before_request({})
    assert before_request({}) == {}


def test_additive_migration_duplicate_score_and_interrupted_recovery(evaluation):
    value, _, factory = evaluation
    with factory() as db:
        db.add(Message(character_id="assistant", role="user", content="existing data"))
        db.commit()
    migrate(factory.kw["bind"])
    migrate(factory.kw["bind"])
    assert "evaluation_runs" in inspect(factory.kw["bind"]).get_table_names()
    run = value.repository.create(
        RunRequest(case_ids=["EVAL-001"]), value.registry.select(["EVAL-001"]), {}
    )
    attempt = value.repository.results(run["run_id"])["items"][0]
    value.repository.started(attempt["attempt_id"])
    value.repository.recover()
    assert value.repository.detail(run["run_id"])["status"] == "INTERRUPTED"
    assert value.repository.results(run["run_id"])["items"][0]["execution_status"] == "UNKNOWN"
    assert not value.repository.finish(
        run["run_id"], attempt["attempt_id"], empty_result("PASS", None)
    )
    with factory() as db:
        assert db.scalar(select(Message)).content == "existing data"


def test_pause_cancel_timeout_and_setup_failure(evaluation, monkeypatch):
    value, app, _ = evaluation
    entered = []

    async def controlled(*args):
        entered.append(args[1])
        await asyncio.sleep(0.2)
        raise TimeoutError()

    monkeypatch.setattr(value.environment, "run", controlled)

    async def run():
        current = await value.start(
            RunRequest(case_ids=["EVAL-001", "EVAL-002"], repetitions=2), app
        )
        run_id = current["run_id"]
        while not entered:
            await asyncio.sleep(0.01)
        await value.control(run_id, "pause")
        await asyncio.sleep(0.3)
        assert len(entered) == 1
        assert value.repository.results(run_id)["items"][0]["execution_status"] == "UNKNOWN"
        await value.control(run_id, "cancel")
        statuses = [r["execution_status"] for r in value.repository.results(run_id)["items"]]
        assert statuses.count("NOT_RUN") == 3
        assert value.repository.detail(run_id)["metrics"]["valid_count"] == 0
        assert not value.tasks

    asyncio.run(run())
    assert not value.environment.active


def test_gui_missing_environment_and_implicit_live_are_not_passed(evaluation):
    value, app, _ = evaluation
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/evaluation/runs", headers=HEADERS, json={"mode": "live_model"}
            ).status_code
            == 422
        )
        started = client.post(
            "/api/evaluation/runs",
            headers=HEADERS,
            json={"mode": "windows_gui", "case_ids": ["EVAL-013"]},
        ).json()
        time.sleep(0.1)
        result = client.get(
            "/api/evaluation/runs/" + started["run_id"] + "/results", headers=HEADERS
        ).json()["items"][0]
        assert result["execution_status"] == "BLOCKED"
        assert result["error_code"] == "INTERACTIVE_FIXTURE_BRIDGE_UNAVAILABLE"
