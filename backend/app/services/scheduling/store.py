"""Short SQLite transactions; no filesystem work, await, runtime or notification inside them."""

from contextlib import contextmanager
from datetime import timedelta
import json
from uuid import uuid4

from sqlalchemy import select, text, update

from app.database.session import SessionLocal
from app.models.schedules import (
    ScheduleRow,
    SchedulerLeaseRow,
    SchedulerStateRow,
    SchedulerEventRow,
)
from app.services.recovery.checkpoints import encoded
from app.services.scheduling.contracts import ScheduleError
from app.services.scheduling.time_rules import stamp, utc
from app.services.scheduling.occurrences import OccurrenceStore


def schedule_dto(row):
    return {
        "definition": json.loads(row.body),
        "revision": row.revision,
        "state": row.state,
        "snapshot": json.loads(row.snapshot),
        "next_due": row.next_due,
        "condition_state": json.loads(row.condition_state),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "generated": row.generated,
    }


def event(db, owner, schedule_id, kind, at, occurrence_id=None):
    db.add(
        SchedulerEventRow(
            owner_id=owner,
            schedule_id=schedule_id,
            occurrence_id=occurrence_id,
            kind=kind,
            created_at=stamp(at),
        )
    )


class ScheduleStore(OccurrenceStore):
    def __init__(self, factory=SessionLocal):
        self.factory = factory

    @contextmanager
    def transaction(self):
        with self.factory() as db:
            # A write reservation before reading makes quotas, CAS and leases
            # atomic across duplicate backend processes, not just asyncio tasks.
            db.execute(text("BEGIN IMMEDIATE"))
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def lease(self, owner, identity, token, now, alive):
        with self.transaction() as db:
            row = db.get(SchedulerLeaseRow, "leader")
            if row and (row.owner != owner or row.token != token):
                if alive(row.process_identity):
                    return False
            values = {
                "owner": owner,
                "process_identity": identity,
                "token": token,
                "expires_at": stamp(now + timedelta(seconds=30)),
            }
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
            else:
                db.add(SchedulerLeaseRow(id="leader", **values))
        return True

    def leader(self, db, owner, token, now):
        row = db.get(SchedulerLeaseRow, "leader")
        if not row or row.owner != owner or row.token != token or utc(row.expires_at) <= now:
            raise ScheduleError("SCHEDULER_LEASE_LOST")

    def release(self, owner, token):
        with self.transaction() as db:
            row = db.get(SchedulerLeaseRow, "leader")
            if row and row.owner == owner and row.token == token:
                db.delete(row)

    def settings(self, changes=None):
        with self.transaction() as db:
            row = db.get(SchedulerStateRow, "global")
            body = json.loads(row.body) if row else {"paused": False}
            if changes:
                body.update(changes)
            if not row:
                db.add(SchedulerStateRow(id="global", body=encoded(body)))
            elif changes:
                row.body = encoded(body)
            return body

    def listing(self, owner=None, *, due=None):
        with self.factory() as db:
            query = select(ScheduleRow)
            if owner:
                query = query.where(ScheduleRow.owner_id == owner)
            if due:
                query = query.where(
                    ScheduleRow.state == "ENABLED", ScheduleRow.next_due <= stamp(due)
                )
            else:
                query = query.where(ScheduleRow.state != "ARCHIVED")
            return [
                schedule_dto(row)
                for row in db.scalars(query.order_by(ScheduleRow.created_at).limit(100))
            ]

    def get(self, key, owner):
        with self.factory() as db:
            row = db.get(ScheduleRow, key)
            if not row or row.owner_id != owner:
                raise ScheduleError("SCHEDULE_NOT_FOUND", 404)
            return schedule_dto(row)

    def save(self, definition, revision, snapshot, due, now):
        with self.transaction() as db:
            row = db.get(ScheduleRow, definition.schedule_id)
            if revision == 0:
                if (
                    row
                    or len(
                        list(
                            db.scalars(
                                select(ScheduleRow.id)
                                .where(ScheduleRow.state != "ARCHIVED")
                                .limit(100)
                            )
                        )
                    )
                    >= 100
                ):
                    raise ScheduleError("SCHEDULE_LIMIT_OR_CONFLICT")
                row = ScheduleRow(
                    id=definition.schedule_id,
                    owner_id=definition.conversation_id,
                    revision=1,
                    state="ENABLED" if definition.enabled else "DISABLED",
                    body=definition.model_dump_json(),
                    snapshot=encoded(snapshot),
                    next_due=stamp(due) if due else None,
                    created_at=stamp(now),
                    updated_at=stamp(now),
                    condition_state="{}",
                )
                db.add(row)
                event(db, row.owner_id, row.id, "SCHEDULE_CREATED", now)
            else:
                if (
                    not row
                    or row.owner_id != definition.conversation_id
                    or row.revision != revision
                    or row.state == "ARCHIVED"
                ):
                    raise ScheduleError("SCHEDULE_EDIT_CONFLICT")
                self.invalidate(db, row.id, now)
                row.revision += 1
                row.state = "ENABLED" if definition.enabled else "DISABLED"
                row.body, row.snapshot = definition.model_dump_json(), encoded(snapshot)
                row.next_due = stamp(due) if due else None
                row.condition_state, row.updated_at, row.generated = "{}", stamp(now), 0
                event(db, row.owner_id, row.id, "SCHEDULE_UPDATED", now)
        return self.get(definition.schedule_id, definition.conversation_id)

    def state(self, key, owner, revision, value, now, due=None):
        with self.transaction() as db:
            row = db.get(ScheduleRow, key)
            if (
                not row
                or row.owner_id != owner
                or row.revision != revision
                or row.state == "ARCHIVED"
            ):
                raise ScheduleError("SCHEDULE_EDIT_CONFLICT")
            self.invalidate(db, key, now)
            row.revision += 1
            row.state, row.updated_at = value, stamp(now)
            body = json.loads(row.body) | {"enabled": value == "ENABLED"}
            row.body = encoded(body)
            if value == "ENABLED":
                row.next_due, row.condition_state, row.generated = (
                    stamp(due) if due else None,
                    "{}",
                    0,
                )
            event(
                db,
                owner,
                key,
                "SCHEDULE_ENABLED" if value == "ENABLED" else "SCHEDULE_DISABLED",
                now,
            )
        return self.get(key, owner)

    def journal(self, key, owner, after=0):
        self.get(key, owner)
        with self.factory() as db:
            return [
                {
                    "cursor": row.id,
                    "kind": row.kind,
                    "created_at": row.created_at,
                    "occurrence_id": row.occurrence_id,
                }
                for row in db.scalars(
                    select(SchedulerEventRow)
                    .where(
                        SchedulerEventRow.schedule_id == key,
                        SchedulerEventRow.owner_id == owner,
                        SchedulerEventRow.id > after,
                    )
                    .order_by(SchedulerEventRow.id)
                    .limit(200)
                )
            ]
