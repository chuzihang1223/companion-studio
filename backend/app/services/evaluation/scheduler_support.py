"""Original MIT fixed scheduler fixtures; runs only inside the existing Evaluation worker."""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.database.session import SessionLocal
from app.models import Conversation
from app.services.scheduling.coordinator import DurableScheduler
from app.services.scheduling.contracts import ScheduleDefinition, Trigger
from app.services.workflows.contracts import Definition
from app.services.workflows.coordinator import WorkflowCoordinator
from app.services.evaluation.workflow_fixtures import linear


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class SchedulerFixture:
    def __init__(self, app, workspace):
        self.app, self.workspace, self.clock = app, workspace, FakeClock()
        state = app.state
        state.external_tools = state.agent_skills = state.knowledge = None
        state.workflows = WorkflowCoordinator(app)
        self.owner = self.add_owner()
        state.work_state.set_workspace(state.work_state.ensure(self.owner), str(workspace))
        self.scheduler = state.scheduler = DurableScheduler(app, clock=self.clock)

    def add_owner(self):
        with SessionLocal.begin() as db:
            row = Conversation(
                id=str(uuid4()),
                character_id="assistant",
                conversation_type="work",
                preferred_mode="work",
            )
            db.add(row)
            return row.id

    def definition(self, kind="ONCE", config=None, graph=None, owner=None, **options):
        graph = graph or linear()
        service = self.app.state.workflows
        saved = service.repository.save(service.catalog.pin(graph))
        published = service.repository.publish(
            Definition.model_validate(saved["definition"]), saved["revision"]
        )
        return ScheduleDefinition(
            name="Synthetic scheduler fixture",
            conversation_id=owner or self.owner,
            workflow_id=graph.workflow_id,
            workflow_version_id=published["version_id"],
            timezone="Etc/UTC",
            trigger_type=kind,
            trigger_config=config
            or Trigger(at=(self.clock() + timedelta(seconds=60)).replace(tzinfo=None).isoformat()),
            **options,
        )

    async def begin(self):
        await self.scheduler.tick()

    def rows(self, definition=None):
        return self.scheduler.store.occurrences(
            self.owner, definition.schedule_id if definition else None
        )

    async def complete(self, row):
        key = row["occurrence_id"]
        for _ in range(400):
            current = self.scheduler.store.occurrence(key, row["owner_id"])
            if current["workflow_run_id"]:
                original = self.app.state.workflows.repository.run(
                    current["workflow_run_id"], row["owner_id"]
                )
                if (
                    original["state"]
                    in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "NEEDS_REVIEW"}
                    and current["workflow_run_id"] not in self.app.state.workflows.tasks
                ):
                    self.scheduler.reconcile()
                    return self.scheduler.store.occurrence(key, row["owner_id"])
            await asyncio.sleep(0.01)
        raise RuntimeError("SCHEDULER_FIXTURE_TIMEOUT")

    async def close(self):
        await self.app.state.scheduler.close()
        await self.app.state.workflows.close()
