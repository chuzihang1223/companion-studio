import asyncio
import threading

import pytest

from scheduler_fixture import scheduler_host, scheduled, run_to_completion
from workflow_fixture import graph, settle
from app.services.scheduling.coordinator import DurableScheduler
from app.services.scheduling.contracts import Trigger, ScheduleError


def test_stale_observation_cannot_revive_cancelled_occurrence(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            scheduler.store.transition(row["occurrence_id"], owner, "CANCELLED", clock())
            assert (
                scheduler.store.settle(
                    row["occurrence_id"], owner, "RUNNING", {}, clock(), expected="QUEUED"
                )
                is False
            )
            assert scheduler.store.occurrence(row["occurrence_id"], owner)["status"] == "CANCELLED"

    asyncio.run(run())


def test_bound_run_without_launch_is_reviewed_not_active_forever(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)

            def fail(*_args, **_kwargs):
                raise RuntimeError("synthetic launch gap")

            monkeypatch.setattr(app.state.workflows, "launch", fail)
            await scheduler.tick()
            bound = scheduler.store.occurrence(row["occurrence_id"], owner)
            assert bound["status"] == "NEEDS_REVIEW" and bound["workflow_run_id"]
            scheduler.reconcile()
            assert (
                scheduler.store.occurrence(row["occurrence_id"], owner)["status"] == "NEEDS_REVIEW"
            )
            assert not app.state.workflows.tasks

    asyncio.run(run())


def test_pause_during_preflight_blocks_atomic_claim(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            entered, release = threading.Event(), threading.Event()
            original = scheduler.permissions.inspect

            def barrier(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("fixture barrier timeout")
                return original(*args, **kwargs)

            monkeypatch.setattr(scheduler.permissions, "inspect", barrier)
            pending = asyncio.create_task(scheduler.tick())
            assert await asyncio.to_thread(entered.wait, 5)
            scheduler.store.settings({"paused": True})
            release.set()
            await pending
            assert (
                scheduler.store.occurrence(row["occurrence_id"], owner)["workflow_run_id"] is None
            )

    asyncio.run(run())


def test_expired_queued_work_is_missed_even_after_fresh_consent(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            clock.advance(3601)
            await scheduler.tick()
            result = scheduler.store.occurrence(row["occurrence_id"], owner)
            assert result["status"] == "MISSED" and result["last_error"] == "SCHEDULE_QUEUE_EXPIRED"
            assert result["workflow_run_id"] is None

    asyncio.run(run())


def test_revoked_condition_source_blocks_queued_execution_and_consent(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            source = scheduler.watch.register(owner, "FILE_CHANGE", clock())
            definition = scheduled(
                app,
                owner,
                clock,
                "CONDITION",
                trigger_config=Trigger(condition_type="FILE_CHANGE", source_id=source["source_id"]),
            )
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            scheduler.watch.revoke(source["source_id"], owner)
            await scheduler.tick()
            assert (
                scheduler.store.occurrence(row["occurrence_id"], owner)["workflow_run_id"] is None
            )
            with pytest.raises(ScheduleError):
                scheduler.grant_once(row["occurrence_id"], owner)

    asyncio.run(run())


def test_shutdown_is_interrupt_and_explicit_resume_reuses_original_run(tmp_path, monkeypatch):
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
            before = scheduler.store.occurrence(row["occurrence_id"], owner)
            await scheduler.close()
            original_run = app.state.workflows.repository.run(before["workflow_run_id"], owner)
            assert original_run["state"] == "INTERRUPTED"
            assert (
                scheduler.store.occurrence(row["occurrence_id"], owner)["status"] == "INTERRUPTED"
            )
            monkeypatch.setattr(app.state.workflows.executor, "execute", original)
            restored = app.state.scheduler = DurableScheduler(app, clock=clock)
            await restored.tick()
            preview = await restored.recovery_preview(row["occurrence_id"], owner)
            assert preview["can_resume"]
            await restored.resume(row["occurrence_id"], owner, preview["revision"])
            final = await run_to_completion(app, restored, owner, row["occurrence_id"])
            assert (
                final["status"] == "COMPLETED"
                and final["workflow_run_id"] == before["workflow_run_id"]
            )
            assert final["summary"]["recovery_count"] == 1

    asyncio.run(run())


def test_schedule_timeout_uses_original_workflow_cancellation(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            original = app.state.workflows.executor.execute

            async def barrier(node, *args):
                if node.type == "transform":
                    await asyncio.Event().wait()
                return await original(node, *args)

            monkeypatch.setattr(app.state.workflows.executor, "execute", barrier)
            definition = scheduled(app, owner, clock, execution_timeout=5)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            result = scheduler.store.occurrence(row["occurrence_id"], owner)
            final = await settle(app, owner, result["workflow_run_id"])
            assert final["state"] == "FAILED" and final["error_code"] == "TIMEOUT"

    asyncio.run(run())


def test_non_keyword_knowledge_never_auto_embeds(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(
                app,
                owner,
                clock,
                graph_definition=graph(
                    "knowledge_search",
                    {
                        "query": "synthetic",
                        "collection_ids": ["00000000-0000-0000-0000-000000000001"],
                        "mode": "hybrid",
                    },
                ),
            )
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            checked = scheduler.store.occurrence(row["occurrence_id"], owner)
            assert (
                checked["status"] == "WAITING_PERMISSION"
                and checked["last_error"] == "SCHEDULE_KEYWORD_SEARCH_REQUIRED"
            )
            assert checked["workflow_run_id"] is None

    asyncio.run(run())
