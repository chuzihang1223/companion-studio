"""Atomic occurrence deduplication, admission and terminal summaries. Never executes work."""

from datetime import timedelta
import json
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select

from app.models.schedules import (
    ScheduleRow,
    ScheduledOccurrenceRow as Occurrence,
    ScheduledRunRow,
    SchedulerNotificationRow,
    SchedulerStateRow,
)
from app.services.recovery.checkpoints import digest, encoded
from app.services.scheduling.contracts import ACTIVE, PENDING, TERMINAL, ScheduleError
from app.services.scheduling.time_rules import stamp, utc
from app.services.scheduling.admission import AdmissionStore


def occurrence_dto(row):
    return {
        "occurrence_id": row.id,
        "schedule_id": row.schedule_id,
        "owner_id": row.owner_id,
        "revision": row.revision,
        "scheduled_for": row.scheduled_for,
        "status": row.status,
        "run_id": row.run_id,
        "workflow_run_id": row.workflow_run_id,
        "attempt": row.attempt,
        "lease_owner": row.lease_owner,
        "lease_token": row.lease_token,
        "lease_expires_at": row.lease_expires_at,
        "body": json.loads(row.body),
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "last_error": row.last_error,
    }


def notification(db, row, kind, now):
    key = str(uuid5(UUID(row.id), "notification:" + kind))
    if (
        not db.get(SchedulerNotificationRow, key)
        and json.loads(row.body)["definition"]["notification"]
    ):
        db.add(
            SchedulerNotificationRow(
                id=key,
                owner_id=row.owner_id,
                schedule_id=row.schedule_id,
                occurrence_id=row.id,
                kind=kind,
                created_at=stamp(now),
                delivery="PENDING",
            )
        )


def finish(db, row, status, code, now):
    from app.services.scheduling.store import event

    row.status, row.last_error = status, code
    if status in TERMINAL:
        row.finished_at = stamp(now)
    if row.run_id:
        run = db.get(ScheduledRunRow, row.run_id)
        if run:
            run.state = status
            run.body = encoded(
                json.loads(run.body)
                | {"status": status, "end_time": row.finished_at, "error_code": code}
            )
    kind = {
        "COMPLETED": "RUN_COMPLETED",
        "FAILED": "RUN_FAILED",
        "SKIPPED": "RUN_SKIPPED",
        "MISSED": "MISFIRE_DETECTED",
    }.get(status, "SCHEDULER_RECOVERED")
    event(db, row.owner_id, row.schedule_id, kind, now, row.id)
    if status in TERMINAL | {"WAITING_PERMISSION", "WAITING_ENVIRONMENT"}:
        notification(db, row, status, now)


class OccurrenceStore(AdmissionStore):
    def invalidate(self, db, key, now):
        rows = db.scalars(
            select(Occurrence).where(
                Occurrence.schedule_id == key,
                Occurrence.workflow_run_id.is_(None),
                Occurrence.status.in_(
                    PENDING | {"WAITING_PERMISSION", "WAITING_ENVIRONMENT", "CLAIMED"}
                ),
            )
        )
        for row in rows:
            finish(db, row, "CANCELLED", "SCHEDULE_REVISION_CHANGED", now)

    def enqueue(
        self,
        selected,
        scheduled_for,
        next_due,
        condition_state,
        now,
        *,
        manual=False,
        condition_fingerprint=None,
        status="QUEUED",
        code=None,
    ):
        from app.services.scheduling.store import event

        key = selected["definition"]["schedule_id"]
        fingerprint = digest(
            {
                "revision": selected["revision"],
                "time": stamp(scheduled_for),
                "condition": condition_fingerprint,
                "manual": str(uuid4()) if manual else None,
            }
        )
        occurrence_id = str(uuid5(UUID(key), fingerprint))
        with self.transaction() as db:
            schedule = db.get(ScheduleRow, key)
            if (
                not schedule
                or schedule.revision != selected["revision"]
                or schedule.state == "ARCHIVED"
            ):
                return None
            if not manual:
                if schedule.state != "ENABLED" or schedule.next_due != selected["next_due"]:
                    return None
                schedule.next_due = stamp(next_due) if next_due else None
                schedule.condition_state = encoded(condition_state)
            if condition_fingerprint is False:
                return None  # A poll updates its durable baseline, not the execution count.
            if db.get(Occurrence, occurrence_id):
                return occurrence_id
            pending = db.scalar(
                select(func.count())
                .select_from(Occurrence)
                .where(Occurrence.status.in_(PENDING | ACTIVE))
            )
            if pending >= 32:
                status, code = "SKIPPED", "SCHEDULE_QUEUE_FULL"
            backlog = db.scalar(
                select(func.count())
                .select_from(Occurrence)
                .where(Occurrence.schedule_id == schedule.id, Occurrence.status.in_(PENDING))
            )
            if backlog >= selected["definition"]["max_backlog"]:
                status, code = "SKIPPED", "SCHEDULE_BACKLOG_LIMIT"
            recent = db.scalar(
                select(func.count())
                .select_from(Occurrence)
                .where(
                    Occurrence.owner_id == schedule.owner_id,
                    Occurrence.created_at > stamp(now - timedelta(hours=1)),
                )
            )
            if recent >= 12:
                status, code = "SKIPPED", "SCHEDULE_HOURLY_LIMIT"
            if schedule.generated >= selected["definition"]["max_consecutive_runs"]:
                schedule.state, schedule.next_due = "PAUSED", None
                status, code = "SKIPPED", "SCHEDULE_RUN_LIMIT"
            schedule.generated += 1
            row = Occurrence(
                id=occurrence_id,
                schedule_id=key,
                owner_id=schedule.owner_id,
                revision=schedule.revision,
                scheduled_for=stamp(scheduled_for),
                fingerprint=fingerprint,
                status=status,
                body=encoded(
                    {
                        "definition": selected["definition"],
                        "snapshot": selected["snapshot"],
                        "manual": manual,
                        "permission": None,
                    }
                ),
                created_at=stamp(now),
                attempt=0,
            )
            db.add(row)
            event(db, row.owner_id, key, "OCCURRENCE_CREATED", now, row.id)
            event(db, row.owner_id, key, "TRIGGER_DUE", now, row.id)
            if status in TERMINAL:
                finish(db, row, status, code, now)
            else:
                event(db, row.owner_id, key, "RUN_QUEUED", now, row.id)
        return occurrence_id

    def occurrences(self, owner=None, schedule_id=None, statuses=None, limit=100):
        with self.factory() as db:
            query = select(Occurrence)
            if owner:
                query = query.where(Occurrence.owner_id == owner)
            if schedule_id:
                query = query.where(Occurrence.schedule_id == schedule_id)
            if statuses:
                query = query.where(Occurrence.status.in_(statuses))
            return [
                occurrence_dto(row)
                for row in db.scalars(
                    query.order_by(Occurrence.created_at.desc()).limit(min(limit, 1000))
                )
            ]

    def occurrence(self, key, owner):
        with self.factory() as db:
            row = db.get(Occurrence, key)
            if not row or row.owner_id != owner:
                raise ScheduleError("SCHEDULE_OCCURRENCE_NOT_FOUND", 404)
            run = db.get(ScheduledRunRow, row.run_id) if row.run_id else None
            return occurrence_dto(row) | {"summary": json.loads(run.body) if run else None}

    def transition(self, key, owner, status, now, *, code=None, updates=None, expected=None):
        with self.transaction() as db:
            row = db.get(Occurrence, key)
            if not row or row.owner_id != owner:
                raise ScheduleError("SCHEDULE_OCCURRENCE_NOT_FOUND", 404)
            if expected and row.status not in expected:
                return False
            if row.status in TERMINAL:
                return False
            if updates:
                row.body = encoded(json.loads(row.body) | updates)
            finish(db, row, status, code, now)
            return True

    def settle(self, key, owner, status, summary, now, code=None, expected=None):
        with self.transaction() as db:
            row = db.get(Occurrence, key)
            if not row or row.owner_id != owner:
                raise ScheduleError("SCHEDULE_OCCURRENCE_NOT_FOUND", 404)
            if row.status in TERMINAL or expected and row.status != expected:
                return False
            run = db.get(ScheduledRunRow, row.run_id) if row.run_id else None
            if run:
                run.body = encoded(json.loads(run.body) | summary)
            if status == row.status:
                return
            if status == "RUNNING" and row.started_at is None:
                row.started_at = stamp(now)
            finish(db, row, status, code, now)

            return True
