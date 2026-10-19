"""Atomic bounded admission and explicit same-run resume; execution stays in Workflow."""

from datetime import timedelta
import json
from uuid import UUID, uuid4, uuid5
from sqlalchemy import select, func
from app.models.schedules import (
    ScheduleRow,
    ScheduledOccurrenceRow as Occurrence,
    ScheduledRunRow,
    SchedulerStateRow,
)
from app.services.recovery.checkpoints import encoded
from app.services.scheduling.contracts import ACTIVE, PENDING, ScheduleError
from app.services.scheduling.time_rules import stamp, utc


class AdmissionStore:
    def claim(self, key, owner, token, now):
        from app.services.scheduling.occurrences import finish, occurrence_dto
        from app.services.scheduling.store import event

        with self.transaction() as db:
            self.leader(db, owner, token, now)
            settings = db.get(SchedulerStateRow, "global")
            if settings and json.loads(settings.body)["paused"]:
                return None
            row = db.get(Occurrence, key)
            if not row or row.status not in PENDING or row.run_id:
                return None
            schedule = db.get(ScheduleRow, row.schedule_id)
            if (now - utc(row.scheduled_for)).total_seconds() > json.loads(row.body)["definition"][
                "expiry_window"
            ]:
                finish(db, row, "MISSED", "SCHEDULE_QUEUE_EXPIRED", now)
                return None
            if (
                not schedule
                or schedule.revision != row.revision
                or (schedule.state != "ENABLED" and not json.loads(row.body)["manual"])
            ):
                finish(db, row, "CANCELLED", "SCHEDULE_REVISION_CHANGED", now)
                return None
            active = list(
                db.scalars(
                    select(Occurrence).where(
                        Occurrence.run_id.is_not(None), Occurrence.status.in_(ACTIVE)
                    )
                )
            )
            same = [item for item in active if item.schedule_id == row.schedule_id]
            if same:
                policy = json.loads(row.body)["definition"]["concurrency_policy"]
                waiting = db.scalar(
                    select(func.count())
                    .select_from(Occurrence)
                    .where(
                        Occurrence.schedule_id == row.schedule_id, Occurrence.status.in_(PENDING)
                    )
                )
                if policy == "QUEUE_ONE":
                    oldest = db.scalar(
                        select(Occurrence.id)
                        .where(
                            Occurrence.schedule_id == row.schedule_id,
                            Occurrence.status.in_(PENDING),
                        )
                        .order_by(Occurrence.created_at, Occurrence.id)
                        .limit(1)
                    )
                    if row.id == oldest:
                        return None
                finish(
                    db,
                    row,
                    "FAILED" if policy == "REJECT_OVERLAP" else "SKIPPED",
                    "SCHEDULE_OVERLAP",
                    now,
                )
                return None
            if len(active) >= 2 or any(item.owner_id == row.owner_id for item in active):
                return None
            row.status, row.lease_owner, row.lease_token = "CLAIMED", owner, str(uuid4())
            row.lease_expires_at = stamp(now + timedelta(seconds=30))
            row.attempt, row.attempt_id = row.attempt + 1, str(uuid4())
            row.started_at = stamp(now)
            row.run_id = str(uuid5(UUID(row.id), "scheduled-run"))
            workflow_id = str(uuid5(UUID(row.id), "workflow-run"))
            db.add(
                ScheduledRunRow(
                    id=row.run_id,
                    occurrence_id=row.id,
                    owner_id=row.owner_id,
                    state="CLAIMED",
                    body=encoded(
                        {
                            "workflow_run_id": workflow_id,
                            "task_id": None,
                            "trace_id": None,
                            "status": "CLAIMED",
                            "verification_result": None,
                            "recovery_count": 0,
                        }
                    ),
                )
            )
            event(db, row.owner_id, row.schedule_id, "OCCURRENCE_CLAIMED", now, row.id)
            return occurrence_dto(row) | {"reserved_workflow_id": workflow_id}

    def renew_runs(self, owner, token, now):
        with self.transaction() as db:
            self.leader(db, owner, token, now)
            for row in db.scalars(
                select(Occurrence).where(
                    Occurrence.lease_owner == owner, Occurrence.status.in_(ACTIVE)
                )
            ):
                row.lease_expires_at = stamp(now + timedelta(seconds=30))

    def resume_claim(self, key, owner_id, leader, token, permission, now):
        with self.transaction() as db:
            self.leader(db, leader, token, now)
            row = db.get(Occurrence, key)
            if (
                not row
                or row.owner_id != owner_id
                or row.status not in {"INTERRUPTED", "NEEDS_REVIEW", "FAILED"}
                or not row.workflow_run_id
            ):
                raise ScheduleError("SCHEDULE_RESUME_CONFLICT")
            settings = db.get(SchedulerStateRow, "global")
            schedule = db.get(ScheduleRow, row.schedule_id)
            if (
                not schedule
                or schedule.state != "ENABLED"
                or schedule.revision != row.revision
                or settings
                and json.loads(settings.body)["paused"]
            ):
                raise ScheduleError("SCHEDULE_EXECUTION_REVOKED")
            active = list(
                db.scalars(
                    select(Occurrence).where(
                        Occurrence.status.in_(ACTIVE), Occurrence.run_id.is_not(None)
                    )
                )
            )
            if len(active) >= 2 or any(item.owner_id == owner_id for item in active):
                raise ScheduleError("SCHEDULE_CAPACITY_UNAVAILABLE")
            row.status, row.lease_owner, row.lease_token = "CLAIMED", leader, str(uuid4())
            row.lease_expires_at = stamp(now + timedelta(seconds=30))
            row.attempt += 1
            row.attempt_id = str(uuid4())
            row.finished_at = None
            row.body = encoded(json.loads(row.body) | {"permission": permission})
            run = db.get(ScheduledRunRow, row.run_id)
            run.state = "CLAIMED"
