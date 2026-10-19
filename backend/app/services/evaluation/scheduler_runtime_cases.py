"""SCH019–024 use real SQLite admission and original coroutine ownership."""

import asyncio

from app.services.scheduling.coordinator import DurableScheduler
from app.services.scheduling.contracts import Trigger


async def run(fx, index):
    service, scheduler = fx.app.state.workflows, fx.scheduler
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.executor.execute

    async def barrier(node, *args):
        if node.type == "transform":
            entered.set()
            await release.wait()
        return await original(node, *args)

    service.executor.execute = barrier
    try:
        if index == 21:
            for n in range(33):
                definition = fx.definition(owner=fx.owner if n == 0 else fx.add_owner())
                scheduler.save(definition)
                scheduler.run_now(definition.schedule_id, definition.conversation_id)
            rows = scheduler.store.occurrences(limit=100)
            return {
                "bounded_queue": sum(r["status"] == "QUEUED" for r in rows) == 32,
                "overflow_skipped": sum(r["last_error"] == "SCHEDULE_QUEUE_FULL" for r in rows)
                == 1,
                "no_dispatch_in_enqueue": all(not r["workflow_run_id"] for r in rows),
            }
        definition = fx.definition("INTERVAL", Trigger(interval_seconds=300), max_backlog=5)
        scheduler.save(definition)
        first = scheduler.run_now(definition.schedule_id, fx.owner)
        await scheduler.tick()
        await asyncio.wait_for(entered.wait(), 5)
        first = scheduler.store.occurrence(first["occurrence_id"], fx.owner)
        if index == 19:
            for _ in range(2):
                another = fx.definition(owner=fx.add_owner())
                scheduler.save(another)
                scheduler.run_now(another.schedule_id, another.conversation_id)
            await scheduler.tick()
            rows = scheduler.store.occurrences()
            active = [r for r in rows if r["workflow_run_id"]]
            return {
                "two_active": len(active) == 2,
                "one_queued": sum(r["status"] == "QUEUED" for r in rows) == 1,
                "distinct_owner": len({r["owner_id"] for r in active}) == 2,
            }
        if index == 20:
            second = scheduler.run_now(definition.schedule_id, fx.owner)
            await scheduler.tick()
            second = scheduler.store.occurrence(second["occurrence_id"], fx.owner)
            return {
                "overlap_skipped": second["status"] == "SKIPPED",
                "original_only": len(service.tasks) == 1,
                "overlap_code": second["last_error"] == "SCHEDULE_OVERLAP",
            }
        if index == 22:
            await scheduler.close()
            interrupted = service.repository.run(first["workflow_run_id"], fx.owner)
            service.executor.execute = original
            fx.scheduler = fx.app.state.scheduler = scheduler = DurableScheduler(
                fx.app, clock=fx.clock
            )
            await scheduler.tick()
            preview = await scheduler.recovery_preview(first["occurrence_id"], fx.owner)
            await scheduler.resume(first["occurrence_id"], fx.owner, preview["revision"])
            final = await fx.complete(first)
            return {
                "persisted_interruption": interrupted["state"] == "INTERRUPTED",
                "explicit_resume": preview["can_resume"],
                "same_original_run": final["workflow_run_id"] == first["workflow_run_id"],
                "completed": final["status"] == "COMPLETED",
                "attempt_budget_preserved": final["summary"]["recovery_count"] == 1,
            }
        fx.clock.advance(31)
        if index == 23:
            await scheduler.tick()
            current = scheduler.store.occurrence(first["occurrence_id"], fx.owner)
            return {
                "expired_lease_not_replayed": current["workflow_run_id"]
                == first["workflow_run_id"],
                "one_attempt": current["attempt"] == 1,
                "one_original_task": len(service.tasks) == 1,
            }
        other = DurableScheduler(fx.app, clock=fx.clock)
        await other.tick()
        try:
            return {
                "second_not_leader": not other.running,
                "original_leader": scheduler.running,
                "one_original_task": len(service.tasks) == 1,
            }
        finally:
            await other.close()
    finally:
        service.executor.execute = original
        release.set()
        # Wait for actual coroutine cleanup; do not interpret pending cancellation as resource release.
        if service.tasks:
            await asyncio.gather(*list(service.tasks.values()), return_exceptions=True)
        scheduler.reconcile()
