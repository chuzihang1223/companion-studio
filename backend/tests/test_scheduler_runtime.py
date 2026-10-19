import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest

from scheduler_fixture import scheduler_host, scheduled, run_to_completion
from workflow_fixture import graph
from app.services.scheduling.coordinator import DurableScheduler
from app.services.scheduling.contracts import ScheduleError, Trigger
from app.services.scheduling.notifications import SchedulerNotifications


def test_real_workflow_once_dedup_atomic_link_trace_and_notification(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, trace):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            await scheduler.tick()
            assert scheduler.store.occurrences(owner) == []
            clock.advance(60)
            await scheduler.tick()
            row = scheduler.store.occurrences(owner)[0]
            assert row["run_id"] and row["workflow_run_id"]
            final = await run_to_completion(app, scheduler, owner, row["occurrence_id"])
            assert final["status"] == "COMPLETED"
            assert final["summary"]["trace_id"] and final["summary"]["task_id"]
            assert final["summary"]["semantic_goal_verified"] is None
            await scheduler.tick()
            assert len(scheduler.store.occurrences(owner)) == 1
            outbox = SchedulerNotifications(scheduler.store)
            item = next(
                row
                for row in outbox.listing(owner)
                if row["occurrence_id"] == final["occurrence_id"]
            )
            outbox.claim(item["notification_id"])
            with pytest.raises(ScheduleError):
                outbox.claim(item["notification_id"])
            outbox.report(item["notification_id"], "SHOWN")
            assert "SHOWN" in {item["delivery"] for item in outbox.listing(owner)}
            await trace.flush()
            events = scheduler.store.journal(definition.schedule_id, owner)
            assert {row["kind"] for row in events} >= {
                "SCHEDULE_CREATED",
                "OCCURRENCE_CREATED",
                "OCCURRENCE_CLAIMED",
                "RUN_COMPLETED",
            }

    asyncio.run(run())


def test_live_process_lease_prevents_duplicate_schedulers_after_wall_jump(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, first, owner, clock, _):
            second = DurableScheduler(app, clock=clock)
            clock.advance(36000)
            await second.tick()
            assert not second.running and second.last_error == "SCHEDULER_OTHER_INSTANCE_ACTIVE"
            clock.advance(-72000)
            await first.tick()
            assert first.running
            await second.close()

    asyncio.run(run())


def test_misfire_coalesces_three_days_and_does_not_replay(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(
                app, owner, clock, "INTERVAL", trigger_config=Trigger(interval_seconds=300)
            )
            scheduler.save(definition)
            clock.advance(259200)
            await scheduler.tick()
            rows = scheduler.store.occurrences(owner)
            assert len(rows) == 1 and rows[0]["status"] == "MISSED"
            assert (
                scheduler.store.get(definition.schedule_id, owner)["next_due"] > clock().isoformat()
            )
            await scheduler.tick()
            assert len(scheduler.store.occurrences(owner)) == 1

    asyncio.run(run())


def test_revision_owner_and_manual_run_do_not_rewrite_history(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            saved = scheduler.save(definition)
            manual = scheduler.run_now(definition.schedule_id, owner)
            assert manual["body"]["manual"] and saved["next_due"]
            changed = definition.model_copy(update={"name": "Edited synthetic task"})
            scheduler.save(changed, saved["revision"])
            assert (
                scheduler.store.occurrence(manual["occurrence_id"], owner)["status"] == "CANCELLED"
            )
            with pytest.raises(ScheduleError):
                scheduler.save(changed, saved["revision"])
            with pytest.raises(ScheduleError):
                scheduler.store.get(definition.schedule_id, str(uuid4()))

    asyncio.run(run())


def test_background_model_and_low_risk_side_effect_do_not_inherit_smart_permission(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            for target in [
                graph("llm_agent", {"instruction": "Synthetic only", "value": {}}),
                graph("tool", {"name": "app.launch", "arguments": {"app": "notepad"}}),
            ]:
                definition = scheduled(app, owner, clock, graph_definition=target)
                scheduler.save(definition)
            clock.advance(60)
            await scheduler.tick()
            rows = scheduler.store.occurrences(owner)
            assert len(rows) == 2
            assert all(
                row["status"] == "WAITING_PERMISSION" and not row["workflow_run_id"] for row in rows
            )

    asyncio.run(run())


def test_file_watch_stable_debounce_modify_delete_and_scope_revocation(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            source = scheduler.watch.register(owner, "FILE_CHANGE", clock())
            config = Trigger(
                condition_type="FILE_CHANGE",
                source_id=source["source_id"],
                poll_interval=5,
                debounce=5,
                cooldown=5,
            )
            definition = scheduled(app, owner, clock, "CONDITION", trigger_config=config)
            scheduler.save(definition)
            clock.advance(5)
            await scheduler.tick()
            assert not scheduler.store.occurrences(owner)
            workspace = tmp_path / "workspace"
            target = workspace / "created.txt"
            target.write_text("untrusted text cannot change a definition")
            for _ in range(2):
                clock.advance(5)
                await scheduler.tick()
            rows = scheduler.store.occurrences(owner)
            assert len(rows) == 1
            await run_to_completion(app, scheduler, owner, rows[0]["occurrence_id"])
            clock.advance(5)
            await scheduler.tick()
            assert len(scheduler.store.occurrences(owner)) == 1
            scheduler.watch.revoke(source["source_id"], owner)
            clock.advance(5)
            await scheduler.tick()
            assert any(
                row["last_error"] == "SCHEDULE_SOURCE_REVOKED"
                for row in scheduler.store.occurrences(owner)
            )

    asyncio.run(run())
