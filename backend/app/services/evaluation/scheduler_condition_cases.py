"""SCH013–018 inspect real bounded fixture metadata and original Workflow run states."""

import asyncio
from app.services.scheduling.contracts import Trigger


async def run(fx, index):
    scheduler, clock, owner = fx.scheduler, fx.clock, fx.owner
    watched_run = None
    release = asyncio.Event()
    if index == 16:
        original = fx.app.state.workflows.executor.execute
        entered = asyncio.Event()

        async def barrier(node, *args):
            if node.type == "transform":
                entered.set()
                await release.wait()
            return await original(node, *args)

        fx.app.state.workflows.executor.execute = barrier
        target = fx.definition()
        scheduler.save(target)
        row = scheduler.run_now(target.schedule_id, owner)
        await scheduler.tick()
        await asyncio.wait_for(entered.wait(), 5)
        watched_run = scheduler.store.occurrence(row["occurrence_id"], owner)
        source = scheduler.watch.register(
            owner, "WORKFLOW_STATE", clock(), watched_run["workflow_run_id"]
        )
        kind = "WORKFLOW_STATE"
    else:
        source = scheduler.watch.register(owner, "FILE_CHANGE", clock())
        kind = "FILE_CHANGE"
    definition = fx.definition(
        "CONDITION",
        Trigger(
            condition_type=kind,
            source_id=source["source_id"],
            poll_interval=5,
            debounce=5,
            cooldown=60,
        ),
    )
    scheduler.save(definition)
    clock.advance(5)
    await scheduler.tick()
    before = fx.rows(definition)
    if index == 16:
        release.set()
        fx.app.state.workflows.executor.execute = original
        await fx.complete(watched_run)
    elif index == 18:
        scheduler.watch.revoke(source["source_id"], owner)
    elif index == 14:
        (fx.workspace / "fixture.txt").write_text("modified synthetic fixture")
    else:
        (fx.workspace / "watched.txt").write_text("first synthetic change")
        if index == 15:
            for n in range(10):
                (fx.workspace / "watched.txt").write_text("burst " + str(n))
    for _ in range(2):
        clock.advance(5)
        await scheduler.tick()
    rows = fx.rows(definition)
    if index == 18:
        return {
            "baseline_no_trigger": not before,
            "revoked_blocked": len(rows) == 1 and rows[0]["status"] == "NEEDS_REVIEW",
            "no_execution": rows[0]["workflow_run_id"] is None,
        }
    if not rows:
        return {"expected_transition_fired": False}
    final = await fx.complete(rows[0])
    if index == 17:
        (fx.workspace / "watched.txt").write_text("second change within cooldown")
        for _ in range(3):
            clock.advance(5)
            await scheduler.tick()
        during = len(fx.rows(definition))
        clock.advance(60)
        await scheduler.tick()
        return {
            "cooldown_prevented": during == 1,
            "later_stable_change": len(fx.rows(definition)) == 2,
        }
    clock.advance(5)
    await scheduler.tick()
    return {
        "baseline_no_trigger": not before,
        "transition_completed": final["status"] == "COMPLETED",
        "burst_or_true_not_repeated": len(fx.rows(definition)) == 1,
    }
