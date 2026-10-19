import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

from app.core.profile_lock import profile_owner
from app.services.scheduling.coordinator import DurableScheduler
from scheduler_fixture import scheduler_host, scheduled, run_to_completion
from workflow_fixture import graph, settle
from sqlalchemy.exc import OperationalError
from sqlalchemy import event, select, func
from app.database.session import SessionLocal
from app.models.workflows import WorkflowNodeRow, WorkflowRunRow
from app.services.workflows.contracts import Definition, Node, Edge, Budget


def test_native_profile_lock_rejects_other_process_and_releases(tmp_path):
    root = str(tmp_path / "locked-profile")
    script = 'from app.core.profile_lock import profile_owner\nimport sys\ntry:\n with profile_owner(sys.argv[1]): print("ACQUIRED")\nexcept RuntimeError as e: print(str(e))\n'
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "backend")
    with profile_owner(root):
        child = subprocess.run(
            [sys.executable, "-B", "-c", script, root],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "PROFILE_ALREADY_RUNNING"
    child = subprocess.run(
        [sys.executable, "-B", "-c", script, root],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 0 and child.stdout.strip() == "ACQUIRED", (
        child.stdout,
        child.stderr,
    )


def test_eight_sqlite_claimants_create_one_durable_run(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)

            def claim(_):
                return scheduler.store.claim(
                    row["occurrence_id"], scheduler.owner, scheduler.token, clock()
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(claim, range(8)))
            assert sum(item is not None for item in results) == 1
            current = scheduler.store.occurrence(row["occurrence_id"], owner)
            assert current["attempt"] == 1 and current["summary"]["status"] == "CLAIMED"

    asyncio.run(run())


def test_interrupted_real_read_tool_can_resume_with_original_checkpoint(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered = asyncio.Event()
            original = app.state.task_runtime.execute_call

            async def barrier(context, call, handle):
                entered.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(app.state.task_runtime, "execute_call", barrier)
            definition = scheduled(
                app,
                owner,
                clock,
                graph_definition=graph(
                    "tool", {"name": "workspace.read", "arguments": {"path": "fixture.txt"}}
                ),
            )
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            await asyncio.wait_for(entered.wait(), 5)
            await scheduler.close()
            current = scheduler.store.occurrence(row["occurrence_id"], owner)
            original_run = app.state.workflows.repository.run(current["workflow_run_id"], owner)
            read = next(n for n in original_run["nodes"] if n["node_id"] == "step")
            checkpoint = app.state.checkpoint_manager.detail(read["recovery_task_id"], owner)
            assert checkpoint["state"] == "INTERRUPTED", checkpoint
            assert checkpoint["checkpoints"][0]["step_state"] == "EXECUTING"
            monkeypatch.setattr(app.state.task_runtime, "execute_call", original)
            restored = app.state.scheduler = DurableScheduler(app, clock=clock)
            await restored.tick()
            preview = await restored.recovery_preview(row["occurrence_id"], owner)
            assert preview["can_resume"], preview
            await restored.resume(row["occurrence_id"], owner, preview["revision"])
            final = await run_to_completion(app, restored, owner, row["occurrence_id"])
            assert final["status"] == "COMPLETED", final
            assert not app.state.runtime_registry.active
            await restored.close()

    asyncio.run(run())


def test_interrupted_model_request_does_not_automatically_replay(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered = asyncio.Event()
            attempts = []
            original = app.state.workflows.executor.execute

            async def barrier(node, *args):
                if node.type == "llm_agent":
                    attempts.append(node.node_id)
                    entered.set()
                    await asyncio.Event().wait()
                return await original(node, *args)

            monkeypatch.setattr(app.state.workflows.executor, "execute", barrier)
            definition = scheduled(
                app,
                owner,
                clock,
                graph_definition=graph("llm_agent", {"instruction": "Synthetic only", "value": {}}),
            )
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            scheduler.grant_once(row["occurrence_id"], owner)
            await scheduler.tick()
            await asyncio.wait_for(entered.wait(), 5)
            await scheduler.close()
            restored = app.state.scheduler = DurableScheduler(app, clock=clock)
            await restored.tick()
            preview = await restored.recovery_preview(row["occurrence_id"], owner)
            assert not preview["can_resume"], preview
            assert attempts == ["step"]
            await restored.tick()
            assert attempts == ["step"]
            await restored.close()

    asyncio.run(run())


def test_downtime_does_not_consume_original_active_execution_budget(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered = asyncio.Event()
            original = app.state.workflows.executor.execute

            async def barrier(node, *args):
                if node.type == "transform":
                    entered.set()
                    await asyncio.Event().wait()
                return await original(node, *args)

            monkeypatch.setattr(app.state.workflows.executor, "execute", barrier)
            definition = scheduled(app, owner, clock, execution_timeout=600)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            await asyncio.wait_for(entered.wait(), 5)
            await scheduler.close()
            clock.advance(3600)
            monkeypatch.setattr(app.state.workflows.executor, "execute", original)
            restored = app.state.scheduler = DurableScheduler(app, clock=clock)
            await restored.tick()
            preview = await restored.recovery_preview(row["occurrence_id"], owner)
            assert preview["can_resume"], preview
            await restored.resume(row["occurrence_id"], owner, preview["revision"])
            final = await run_to_completion(app, restored, owner, row["occurrence_id"])
            assert final["status"] == "COMPLETED", json.dumps(
                final, ensure_ascii=False, default=str
            )
            await restored.close()

    asyncio.run(run())


def test_storage_fault_during_close_still_cancels_owned_active_coroutine(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered = asyncio.Event()
            original = app.state.workflows.executor.execute

            async def barrier(node, *args):
                if node.type == "transform":
                    entered.set()
                    await asyncio.Event().wait()
                return await original(node, *args)

            monkeypatch.setattr(app.state.workflows.executor, "execute", barrier)
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            await asyncio.wait_for(entered.wait(), 5)
            current = scheduler.store.occurrence(row["occurrence_id"], owner)
            live = app.state.workflows.tasks[current["workflow_run_id"]]

            def storage_unavailable(*args, **kwargs):
                raise OperationalError(
                    "synthetic read during shutdown", {}, RuntimeError("storage unavailable")
                )

            original_occurrences = scheduler.store.occurrences
            monkeypatch.setattr(scheduler.store, "occurrences", storage_unavailable)
            error = None
            try:
                await scheduler.close()
            except OperationalError as caught:
                error = caught
            was_done = live.done()
            monkeypatch.setattr(scheduler.store, "occurrences", original_occurrences)
            scheduler.store.release(scheduler.owner, scheduler.token)
            assert was_done, {
                "shutdown_raised": str(error),
                "closing": scheduler.closing,
                "owned_coroutine_done": was_done,
            }

    asyncio.run(run())


def test_original_model_call_budget_survives_durable_resume(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered, model_calls = asyncio.Event(), []
            original = app.state.workflows.executor.execute

            async def execution(node, *args):
                if node.type == "llm_agent":
                    model_calls.append(node.node_id)
                    return "synthetic response; no provider request"
                if node.node_id == "barrier":
                    entered.set()
                    await asyncio.Event().wait()
                return await original(node, *args)

            monkeypatch.setattr(app.state.workflows.executor, "execute", execution)
            workflow = Definition(
                nodes=[
                    Node(node_id="start", type="manual_trigger"),
                    Node(
                        node_id="first",
                        type="llm_agent",
                        config={"instruction": "Synthetic only", "value": {}},
                    ),
                    Node(
                        node_id="barrier",
                        type="transform",
                        config={"operation": "concat", "value": ["a", "b"]},
                    ),
                    Node(
                        node_id="second",
                        type="llm_agent",
                        config={"instruction": "Synthetic only", "value": {}},
                    ),
                    Node(
                        node_id="finish",
                        type="output",
                        config={"value": {"$ref": "nodes.second.output"}},
                    ),
                ],
                edges=[
                    Edge(edge_id="edge" + str(i), source_node_id=a, target_node_id=b)
                    for i, (a, b) in enumerate(
                        zip(
                            ["start", "first", "barrier", "second"],
                            ["first", "barrier", "second", "finish"],
                        )
                    )
                ],
                budget=Budget(max_model_calls=1),
            )
            definition = scheduled(app, owner, clock, graph_definition=workflow)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            scheduler.grant_once(row["occurrence_id"], owner)
            await scheduler.tick()
            await asyncio.wait_for(entered.wait(), 5)
            await scheduler.close()

            async def resumed(node, *args):
                if node.type == "llm_agent":
                    model_calls.append(node.node_id)
                    return "unexpected second request"
                return await original(node, *args)

            monkeypatch.setattr(app.state.workflows.executor, "execute", resumed)
            restored = app.state.scheduler = DurableScheduler(app, clock=clock)
            await restored.tick()
            preview = await restored.recovery_preview(row["occurrence_id"], owner)
            assert preview["can_resume"], preview
            await restored.resume(row["occurrence_id"], owner, preview["revision"])
            final = await run_to_completion(app, restored, owner, row["occurrence_id"])
            assert (
                final["status"] in {"FAILED", "NEEDS_REVIEW"}
                and final["last_error"] == "WORKFLOW_MODEL_BUDGET"
            ), final
            assert model_calls == ["first"]
            await restored.close()

    asyncio.run(run())


def test_original_tool_call_budget_survives_durable_resume(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered, calls = asyncio.Event(), []
            original = app.state.workflows.executor.execute
            original_tool = app.state.task_runtime.execute_call

            async def execution(node, *args):
                if node.node_id == "barrier":
                    entered.set()
                    await asyncio.Event().wait()
                return await original(node, *args)

            async def tool(context, call, handle):
                calls.append(call["name"])
                return await original_tool(context, call, handle)

            monkeypatch.setattr(app.state.workflows.executor, "execute", execution)
            monkeypatch.setattr(app.state.task_runtime, "execute_call", tool)
            workflow = Definition(
                nodes=[
                    Node(node_id="start", type="manual_trigger"),
                    Node(
                        node_id="first",
                        type="tool",
                        config={"name": "workspace.read", "arguments": {"path": "fixture.txt"}},
                    ),
                    Node(
                        node_id="barrier",
                        type="transform",
                        config={"operation": "concat", "value": ["a", "b"]},
                    ),
                    Node(
                        node_id="second",
                        type="tool",
                        config={"name": "workspace.read", "arguments": {"path": "fixture.txt"}},
                    ),
                    Node(
                        node_id="finish",
                        type="output",
                        config={"value": {"$ref": "nodes.second.output"}},
                    ),
                ],
                edges=[
                    Edge(edge_id="edge" + str(i), source_node_id=a, target_node_id=b)
                    for i, (a, b) in enumerate(
                        zip(
                            ["start", "first", "barrier", "second"],
                            ["first", "barrier", "second", "finish"],
                        )
                    )
                ],
                budget=Budget(max_tool_calls=1),
            )
            definition = scheduled(app, owner, clock, graph_definition=workflow)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            await asyncio.wait_for(entered.wait(), 5)
            await scheduler.close()
            monkeypatch.setattr(app.state.workflows.executor, "execute", original)
            restored = app.state.scheduler = DurableScheduler(app, clock=clock)
            await restored.tick()
            preview = await restored.recovery_preview(row["occurrence_id"], owner)
            assert preview["can_resume"], preview
            await restored.resume(row["occurrence_id"], owner, preview["revision"])
            final = await run_to_completion(app, restored, owner, row["occurrence_id"])
            assert final["status"] == "FAILED" and final["last_error"] == "WORKFLOW_TOOL_BUDGET", (
                final
            )
            assert calls == ["workspace.read"]
            await restored.close()

    asyncio.run(run())


def test_mid_flush_failure_rolls_back_occurrence_binding_and_workflow_rows(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)

            def fail(mapper, connection, target):
                raise RuntimeError("synthetic failure during workflow node flush")

            event.listen(WorkflowNodeRow, "before_insert", fail)
            try:
                await scheduler.tick()
            finally:
                event.remove(WorkflowNodeRow, "before_insert", fail)
            final = scheduler.store.occurrence(row["occurrence_id"], owner)
            assert final["status"] == "NEEDS_REVIEW" and not final["workflow_run_id"], final
            reserved = final["summary"]["workflow_run_id"]
            with SessionLocal() as db:
                assert db.get(WorkflowRunRow, reserved) is None
                assert (
                    db.scalar(
                        select(func.count())
                        .select_from(WorkflowNodeRow)
                        .where(WorkflowNodeRow.run_id == reserved)
                    )
                    == 0
                )
            assert not app.state.workflows.tasks

    asyncio.run(run())
