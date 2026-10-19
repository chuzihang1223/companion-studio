"""Explicit scheduled resume delegates all side-effect reconciliation to original Recovery."""

from datetime import timedelta

from app.services.recovery.checkpoints import digest
from app.services.scheduling.contracts import ScheduleDefinition, ScheduleError
from app.services.scheduling.time_rules import stamp


class SchedulerRecovery:
    async def recovery_preview(self, key, owner):
        row = self.store.occurrence(key, owner)
        if not row["workflow_run_id"] or row["status"] not in {
            "INTERRUPTED",
            "NEEDS_REVIEW",
            "FAILED",
        }:
            raise ScheduleError("SCHEDULE_NOT_RESUMABLE")
        definition = ScheduleDefinition.model_validate(row["body"]["definition"])
        self.validate(definition)
        if self.permissions.snapshot(definition) != row["body"]["snapshot"]:
            raise ScheduleError("SCHEDULE_DEPENDENCY_CHANGED")
        preview = await self.app.state.workflows.recovery.preview(row["workflow_run_id"], owner)
        return preview | {"occurrence_id": key, "schedule_id": row["schedule_id"]}

    async def resume(self, key, owner, revision, binding=None):
        if binding is not None and self.approval_overview(key, owner)["binding"] != binding:
            raise ScheduleError("SCHEDULE_APPROVAL_EXPIRED")
        preview = await self.recovery_preview(key, owner)
        if not preview["can_resume"] or preview["revision"] != revision:
            raise ScheduleError("SCHEDULE_RESUME_BLOCKED")
        row = self.store.occurrence(key, owner)
        self.store.resume_claim(
            key,
            owner,
            self.owner,
            self.token,
            {
                "binding": digest(row["body"]["snapshot"]),
                "expires_at": stamp(self.now() + timedelta(minutes=5)),
            },
            self.now(),
        )
        try:
            self.owned_runs[row["workflow_run_id"]] = owner
            original = await self.app.state.workflows.resume(
                row["workflow_run_id"], owner, revision
            )
            self.store.settle(
                key, owner, "RUNNING", {"recovery_count": original["attempt"] - 1}, self.now()
            )
            return self.store.occurrence(key, owner)
        except BaseException:
            self.store.transition(
                key, owner, "NEEDS_REVIEW", self.now(), code="SCHEDULE_RESUME_BLOCKED"
            )
            raise
