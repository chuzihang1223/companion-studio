"""Scheduler tests use the existing original runtime host, a fake UTC clock and real SQLite."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from workflow_fixture import host, graph, settle
from app.services.scheduling.coordinator import DurableScheduler
from app.services.scheduling.contracts import ScheduleDefinition, Trigger
from app.services.workflows.contracts import Definition


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@asynccontextmanager
async def scheduler_host(root):
    async with host(root) as (app, owner, trace):
        clock = Clock()
        scheduler = app.state.scheduler = DurableScheduler(app, clock=clock)
        scheduler.store.settings({"paused": False})
        await scheduler.tick()
        try:
            yield app, scheduler, owner, clock, trace
        finally:
            await scheduler.close()
            if app.state.scheduler is not scheduler:
                await app.state.scheduler.close()
            for item in scheduler.store.listing(owner):
                if item["state"] != "ARCHIVED":
                    scheduler.store.state(
                        item["definition"]["schedule_id"],
                        owner,
                        item["revision"],
                        "ARCHIVED",
                        clock(),
                    )
            scheduler.store.settings({"paused": False})


def scheduled(app, owner, clock, kind="ONCE", graph_definition=None, **options):
    workflow = graph_definition or graph()
    service = app.state.workflows
    saved = service.repository.save(service.catalog.pin(workflow))
    published = service.repository.publish(
        Definition.model_validate(saved["definition"]), saved["revision"]
    )
    return ScheduleDefinition(
        name="Local scheduler fixture",
        conversation_id=owner,
        workflow_id=workflow.workflow_id,
        workflow_version_id=published["version_id"],
        timezone="Etc/UTC",
        trigger_type=kind,
        trigger_config=Trigger(
            at=(clock() + timedelta(seconds=60)).replace(tzinfo=None).isoformat()
        )
        if kind == "ONCE"
        else options.pop("trigger_config"),
        **options,
    )


async def run_to_completion(app, scheduler, owner, occurrence):
    row = scheduler.store.occurrence(occurrence, owner)
    await settle(app, owner, row["workflow_run_id"])
    scheduler.reconcile()
    return scheduler.store.occurrence(occurrence, owner)
