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
from sqlalchemy import create_engine, inspect, select, text
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
        assert run["started_at"] and run["completed_at"]
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
        # Retain the original export before the deletion API test removes this run.
        (value.environment.root.parent / "studio-result.json").write_text(
            EvaluationReportService(value.repository).export(run_id, "json")["content"],
            encoding="utf-8",
        )
        first = rows[0]
        dual = rows[-1]
        assert (
            len(
                client.get(
                    url + "/attempts/" + dual["attempt_id"] + "/trace/list", headers=HEADERS
                ).json()["items"]
            )
            == 2
        )
        assert (
            client.get(
                url
                + "/attempts/"
                + dual["attempt_id"]
                + "/trace/detail?trace_id="
                + first["trace_id"],
                headers=HEADERS,
            ).status_code
            == 404
        )
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
        assert not list((value.environment.root / "_w").iterdir())
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
    with factory.kw["bind"].begin() as connection:
        connection.execute(text("ALTER TABLE evaluation_runs DROP COLUMN started_at"))
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


def test_gui_missing_environment_and_implicit_live_are_not_passed(evaluation, monkeypatch):
    value, app, _ = evaluation
    delayed_start = []
    original_status = value.repository.status

    def status_after_worker_delay(run_id, status, *args, **kwargs):
        if status == "RUNNING":
            # Exercise a real thread scheduling delay beyond the old blind sleep.
            time.sleep(0.2)
            delayed_start.append(True)
        return original_status(run_id, status, *args, **kwargs)

    monkeypatch.setattr(value.repository, "status", status_after_worker_delay)
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

        async def wait_for_owned_evaluation():
            # Completion, not a 100ms startup guess, is the functional barrier.
            # Keep the existing production case timeout; do not alter runtime budgets.
            task = value.tasks.get(started["run_id"])
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), RunRequest().timeout_seconds)

        client.portal.call(wait_for_owned_evaluation)
        assert delayed_start == [True]
        url = "/api/evaluation/runs/" + started["run_id"]
        assert client.get(url, headers=HEADERS).json()["status"] == "COMPLETED"
        items = client.get(url + "/results", headers=HEADERS).json()["items"]
        assert len(items) == 1
        result = items[0]
        assert result["execution_status"] == "BLOCKED"
        assert result["error_code"] == "INTERACTIVE_FIXTURE_BRIDGE_UNAVAILABLE"


def test_setup_failure_closes_owned_server_and_directories(evaluation, monkeypatch):
    value, _, _ = evaluation

    async def fail(*args, **kwargs):
        raise OSError("controlled startup failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)

    async def run():
        with pytest.raises(OSError):
            await value.environment.run(
                str(uuid4()), str(uuid4()), {"mode": "mock", "case_id": "EVAL-001"}, 2
            )

    asyncio.run(run())
    assert not value.environment.active
    assert not list((value.environment.root / "_w").iterdir())


def test_remove_owned_long_path_archive_preserves_other_data(tmp_path):
    from app.core.filesystem import disk_path
    from app.services.evaluation.environment import EnvironmentManager
    from app.services.trace.journal import TraceJournal

    # Keep each component short while the archived journal filename exceeds
    # classic Win32 MAX_PATH. The real SQLite writer already supports it.
    run_id, attempt_id = str(uuid4()), str(uuid4())
    unpadded = (
        tmp_path
        / "owned-"
        / run_id
        / attempt_id
        / "data"
        / "chat.db.trace-pending"
        / "pending.sqlite3"
    )
    padding = max(45, 285 - len(str(unpadded)))
    manager = EnvironmentManager(tmp_path / ("owned-" + "x" * padding))
    archive = manager.path(run_id, attempt_id)
    data = archive / "data"
    disk_path(data).mkdir(parents=True)
    database = data / "chat.db"
    disk_path(database).touch()
    journal = TraceJournal(database, capacity=8)
    pending = journal.path
    journal.close()
    assert len(str(pending)) > 260
    assert disk_path(pending).is_file()
    outside = tmp_path / "unrelated.txt"
    outside.write_text("preserve unrelated data", encoding="utf-8")
    target = manager.path(run_id)
    assert target.resolve().is_relative_to(manager.root.resolve())
    manager.remove(run_id)
    assert not disk_path(target).exists()
    assert outside.read_text(encoding="utf-8") == "preserve unrelated data"


def test_evaluation_run_directory_alias_is_not_an_owned_run(tmp_path):
    import os
    import subprocess
    from app.core.filesystem import disk_path
    from app.services.evaluation.environment import EnvironmentManager

    manager = EnvironmentManager(tmp_path / "evaluation")
    target = manager.path(str(uuid4()))
    target.mkdir()
    sentinel = target / "preserve.txt"
    sentinel.write_text("another run", encoding="utf-8")
    alias_id = str(uuid4())
    alias = manager.root / alias_id
    if os.name == "nt":
        # Native directory junctions do not need symlink privileges. Only these
        # two synthetic paths are passed through environment values, not code.
        env = dict(os.environ, STUDIO_TEST_JUNCTION=str(alias), STUDIO_TEST_TARGET=str(target))
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                "-Path $env:STUDIO_TEST_JUNCTION -Target $env:STUDIO_TEST_TARGET | Out-Null",
            ],
            env=env,
            check=True,
            capture_output=True,
            timeout=20,
        )
        assert alias.is_junction()
    else:
        alias.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="所有权"):
            manager.path(alias_id)
        with pytest.raises(ValueError, match="所有权"):
            manager.remove(alias_id)
        assert sentinel.read_text(encoding="utf-8") == "another run"
    finally:
        assert alias.parent == manager.root and target.resolve().is_relative_to(manager.root)
        if os.name == "nt":
            disk_path(alias).rmdir()  # Remove this verified junction, not its target.
        else:
            alias.unlink()


def test_scoped_sse_and_thousand_synthetic_metric_records(evaluation):
    value, _, _ = evaluation
    current = value.repository.create(
        RunRequest(case_ids=["EVAL-001"]), value.registry.select(["EVAL-001"]), {}
    )

    async def run():
        stream = value.stream(current["run_id"])
        first = await anext(stream)
        assert current["run_id"] in first and "EVAL_RUN_PROGRESS" in first
        value.publish(str(uuid4()), "EVAL_CASE_STARTED", "EVAL-020")
        value.publish(current["run_id"], "EVAL_CASE_STARTED", "EVAL-001")
        second = await anext(stream)
        assert "EVAL-001" in second and "EVAL-020" not in second
        await stream.aclose()

    asyncio.run(run())
    assert not value.subscribers
    started = time.perf_counter()
    metrics = MetricsCollector.aggregate(
        [dict(empty_result("PASS", None), duration_ms=i) for i in range(1000)], "mock"
    )
    assert metrics["valid_count"] == 1000 and metrics["p50_ms"] == 499.5
    assert time.perf_counter() - started < 2  # CPU aggregation only, not renderer FPS.


def test_provider_response_usage_is_request_local():
    import httpx
    from app.services.llm.openai_provider import OpenAIProvider

    def response(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "local response"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11},
            },
        )

    async def run_one():
        budget = UsageBudget(1, 100)
        provider = OpenAIProvider(
            "", "fixture", "http://127.0.0.1:9", transport=httpx.MockTransport(response)
        )
        with usage_scope(budget):
            assert await provider.chat([]) == "local response"
        return budget.public()

    async def run():
        return await asyncio.gather(run_one(), run_one())

    left, right = asyncio.run(run())
    assert left["total_tokens"] == right["total_tokens"] == 11
    assert left["requests"] == right["requests"] == 1


def test_shutdown_before_scheduler_starts_does_not_leave_queued_run(evaluation):
    value, app, _ = evaluation

    async def run():
        current = await value.start(RunRequest(case_ids=["EVAL-001"]), app)
        await value.close()
        assert value.repository.detail(current["run_id"])["status"] == "CANCELLED"
        assert (
            value.repository.results(current["run_id"])["items"][0]["execution_status"] == "NOT_RUN"
        )
        assert not value.tasks and not value.environment.active

    asyncio.run(run())
