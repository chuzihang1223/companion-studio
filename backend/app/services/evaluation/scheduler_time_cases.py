"""SCH001–012 exercise production time rules and persisted occurrence creation with fake UTC."""

from datetime import datetime, timedelta, timezone

from app.services.scheduling.contracts import Trigger
from app.services.scheduling.time_rules import next_fire, wall_instant, preview, utc


async def run(fx, index):
    scheduler, clock = fx.scheduler, fx.clock
    definition = fx.definition()
    if index == 4:
        definition = fx.definition("DAILY", Trigger(time="09:00"))
    if index == 5:
        definition = fx.definition("WEEKLY", Trigger(time="09:00", weekdays=[0]))
    if index == 6:
        definition = fx.definition("INTERVAL", Trigger(interval_seconds=300))
    saved = scheduler.save(definition)
    if index == 1:
        return {
            "stored": saved["revision"] == 1,
            "not_yet_fired": not fx.rows(),
            "utc_due": saved["next_due"].endswith("+00:00"),
        }
    if index in {2, 3}:
        if index == 2:
            definition = definition.model_copy(
                update={
                    "trigger_config": Trigger(
                        at=(clock() + timedelta(seconds=120)).replace(tzinfo=None).isoformat()
                    )
                }
            )
            scheduler.save(definition, saved["revision"])
            clock.advance(60)
            await scheduler.tick()
            if fx.rows():
                return {"old_time_invalidated": False}
        clock.advance(60)
        await scheduler.tick()
        result = await fx.complete(fx.rows()[0])
        await scheduler.tick()
        return {
            "exact_due": utc(result["scheduled_for"]) == clock(),
            "completed": result["status"] == "COMPLETED",
            "once_only": len(fx.rows()) == 1,
        }
    if index in {4, 5, 6}:
        first = utc(saved["next_due"])
        clock.advance((first - clock()).total_seconds())
        await scheduler.tick()
        await fx.complete(fx.rows(definition)[0])
        following = utc(scheduler.store.get(definition.schedule_id, fx.owner)["next_due"])
        expected = timedelta(days=7 if index == 5 else 1) if index != 6 else timedelta(minutes=5)
        return {
            "first_fired": len(fx.rows(definition)) == 1,
            "calendar_interval": following - first == expected,
        }
    if index == 7:
        changed = definition.model_copy(update={"timezone": "Asia/Shanghai"})
        rows = preview(changed, clock())
        return {
            "local_utc_mapping": rows[0]["local"].endswith("+08:00"),
            "instant": rows[0]["utc"].startswith("2026-10-18T16:01"),
        }
    if index == 8:
        changed = definition.model_copy(update={"timezone": "Asia/Shanghai"})
        old = scheduler.run_now(definition.schedule_id, fx.owner)
        edited = scheduler.save(changed, saved["revision"])
        return {
            "revision": edited["revision"] == 2,
            "old_pending_cancelled": scheduler.store.occurrence(old["occurrence_id"], fx.owner)[
                "status"
            ]
            == "CANCELLED",
            "new_utc": utc(edited["next_due"])
            == clock() - timedelta(hours=8) + timedelta(seconds=60),
        }
    if index in {9, 10}:
        changed = fx.definition(
            "DAILY", Trigger(time="02:30" if index == 9 else "01:30")
        ).model_copy(update={"timezone": "America/New_York"})
        if index == 9:
            after = datetime(2026, 3, 8, tzinfo=timezone.utc)
            due = next_fire(changed, after, after)
            return {
                "gap_detected": wall_instant(datetime(2026, 3, 8, 2, 30), changed.timezone) is None,
                "gap_skipped": due == datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc),
            }
        first = wall_instant(datetime(2026, 11, 1, 1, 30), changed.timezone)
        return {
            "first_fold": first.hour == 5,
            "not_second_fold": next_fire(changed, first, first)
            == datetime(2026, 11, 2, 6, 30, tzinfo=timezone.utc),
        }
    if index == 11:
        clock.advance(60)
        await scheduler.tick()
        await fx.complete(fx.rows()[0])
        clock.advance(-3600)
        await scheduler.tick()
        return {"no_duplicate_on_rollback": len(fx.rows()) == 1}
    if index == 12:
        clock.advance(259200)
        await scheduler.tick()
        rows = fx.rows()
        return {
            "coalesced": len(rows) == 1,
            "missed_not_replayed": rows[0]["status"] == "MISSED",
            "no_workflow": rows[0]["workflow_run_id"] is None,
        }
    raise RuntimeError("SCHEDULER_UNKNOWN_FIXTURE")
