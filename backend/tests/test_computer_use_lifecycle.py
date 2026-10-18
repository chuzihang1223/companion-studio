"""Cancellation and truthful grading, using original local Runtime/Trace only."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.agent_trace import TraceEvent
from app.services.computer_use import native_evaluation, vision_lifecycle
from app.services.evaluation.metrics import MetricsCollector
from workflow_fixture import host


def test_local_gui_grading_is_not_ai_quality():
    metrics = MetricsCollector.aggregate(
        [{"completion_status": "PASS", "duration_ms": 2}], "windows_gui"
    )
    assert metrics["success_rate"] == 1 and metrics["real_task_success_rate"] is None


def test_vision_owned_cancel_trace_and_early_revoke(tmp_path, monkeypatch):
    async def run():
        async with host(tmp_path) as (app, owner, trace):
            entered = asyncio.Event()

            async def slow(*args):
                entered.set()
                await asyncio.sleep(60)

            monkeypatch.setattr(vision_lifecycle, "analyze", slow)
            value = vision_lifecycle.lifecycle(app)
            task_id = str(uuid4())
            task = asyncio.create_task(value.run(task_id, owner, "synthetic", "PRIVATE_IMAGE"))
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(ValueError, match="OWNER"):
                value.cancel(task_id, str(uuid4()))
            value.cancel(task_id, owner)
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not value.active and not app.state.runtime_registry.active
            await trace.flush()
            with trace.factory() as db:
                events = list(db.scalars(select(TraceEvent)))
                assert any(row.status == "cancelled" for row in events)
                assert "PRIVATE_IMAGE" not in json.dumps([row.data_json for row in events])
            early = str(uuid4())
            value.cancel(early, owner)
            with pytest.raises(ValueError, match="COMPUTER_PAUSED"):
                await value.run(early, owner, "synthetic", "PRIVATE_IMAGE")

    asyncio.run(run())


def test_scheduler_cancel_stops_queue_even_if_cleanup_failed(tmp_path, monkeypatch):
    from app.schemas.evaluation import RunRequest
    from app.services.evaluation.engine import AgentEvaluationEngine
    from app.services.evaluation.repository import empty_result

    async def run():
        async with host(tmp_path) as (app, _, _):
            evaluator = AgentEvaluationEngine(tmp_path / "attempts")
            started, calls = asyncio.Event(), []

            async def cancel_cleanup(*args):
                calls.append(args[1].case_id)
                started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    return {
                        "result": empty_result("FAIL", "VISUAL_FIXTURE_CLEANUP_FAILED"),
                        "links": {},
                        "cancelled": True,
                    }

            monkeypatch.setattr(native_evaluation, "run_case", cancel_cleanup)
            created = await evaluator.start(
                RunRequest(
                    dataset_id="computer-use-core",
                    mode="windows_gui",
                    case_ids=["CU-001", "CU-002"],
                    visual_consent=str(uuid4()),
                ),
                app,
            )
            await started.wait()
            await evaluator.control(created["run_id"], "cancel")
            rows = evaluator.repository.results(created["run_id"], 0, 100)["items"]
            assert calls == ["CU-001"]
            assert sorted(r["execution_status"] for r in rows) == ["FAIL", "NOT_RUN"]
            await evaluator.close()

    asyncio.run(run())


def test_native_deadline_cancels_its_original_workflow_child(tmp_path, monkeypatch):
    from workflow_fixture import start, graph

    async def run():
        async with host(tmp_path) as (app, _, _):
            monkeypatch.setattr(native_evaluation, "enabled", lambda: True)

            async def fixture(*args):
                return {"target_id": str(uuid4()), "fixture_id": str(uuid4()), "verified": True}

            async def tool(*args):
                return {"verified": True}

            async def verify(f, *args):
                key = start(
                    app, f.owner, graph("human_approval", {"message": "Synthetic approval"})
                )
                f.workflow_runs.append(key)
                await asyncio.sleep(60)

            monkeypatch.setattr(native_evaluation.NativeComputerCase, "fixture_command", fixture)
            monkeypatch.setattr(native_evaluation.NativeComputerCase, "tool", tool)
            monkeypatch.setattr(native_evaluation, "verify_case", verify)
            value = await native_evaluation.run_case(
                app, SimpleNamespace(case_id="CU-023"), str(uuid4()), 0.2
            )
            assert value["result"]["completion_status"] == "UNKNOWN"
            assert not app.state.workflows.tasks and not app.state.workflows.approvals

    asyncio.run(run())


@pytest.mark.parametrize("termination", ["timeout", "cancel", "cleanup", "cancel_cleanup"])
def test_native_case_deadline_cleanup_and_trace(tmp_path, monkeypatch, termination):
    async def run():
        async with host(tmp_path) as (app, _, trace):
            monkeypatch.setattr(native_evaluation, "enabled", lambda: True)
            closed, started = [], asyncio.Event()

            async def fixture(self, operation):
                if operation == "open":
                    return {"target_id": str(uuid4()), "fixture_id": str(uuid4())}
                closed.append(operation)
                if termination in {"cleanup", "cancel_cleanup"}:
                    raise ValueError("cleanup failed")
                return {"verified": True}

            async def tool(*args):
                return {"verified": True}

            async def verify(*args):
                started.set()
                if termination != "cleanup":
                    await asyncio.sleep(60)
                return True

            monkeypatch.setattr(native_evaluation.NativeComputerCase, "fixture_command", fixture)
            monkeypatch.setattr(native_evaluation.NativeComputerCase, "tool", tool)
            monkeypatch.setattr(native_evaluation, "verify_case", verify)
            task = asyncio.create_task(
                native_evaluation.run_case(
                    app, SimpleNamespace(case_id="CU-001"), str(uuid4()), 0.1
                )
            )
            await started.wait()
            if termination in {"cancel", "cancel_cleanup"}:
                task.cancel()
            value = await task
            status = {
                "cancel": "CANCELLED",
                "timeout": "UNKNOWN",
                "cleanup": "FAIL",
                "cancel_cleanup": "FAIL",
            }[termination]
            assert value["result"]["completion_status"] == status
            assert value["result"]["passed"] is not True and value["result"]["score"] != 1
            assert closed == ["close_owner"] and value["links"]["trace_id"]
            assert value["cancelled"] == (termination in {"cancel", "cancel_cleanup"})
            await trace.flush()

    asyncio.run(run())
