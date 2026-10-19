"""Durable local notification outbox; an unknown native delivery is never blindly repeated."""

from sqlalchemy import select
from app.models.schedules import SchedulerNotificationRow
from app.services.scheduling.contracts import ScheduleError


def dto(row):
    return {
        "notification_id": row.id,
        "conversation_id": row.owner_id,
        "schedule_id": row.schedule_id,
        "occurrence_id": row.occurrence_id,
        "kind": row.kind,
        "created_at": row.created_at,
        "delivery": row.delivery,
    }


class SchedulerNotifications:
    def __init__(self, store):
        self.store = store

    def pending(self):
        with self.store.factory() as db:
            return [
                dto(row)
                for row in db.scalars(
                    select(SchedulerNotificationRow)
                    .where(SchedulerNotificationRow.delivery == "PENDING")
                    .order_by(SchedulerNotificationRow.created_at)
                    .limit(10)
                )
            ]

    def claim(self, key):
        with self.store.transaction() as db:
            row = db.get(SchedulerNotificationRow, key)
            if not row or row.delivery != "PENDING":
                raise ScheduleError("SCHEDULE_NOTIFICATION_ALREADY_CLAIMED")
            row.delivery = "ATTEMPTED"
            return dto(row)

    def report(self, key, result):
        with self.store.transaction() as db:
            row = db.get(SchedulerNotificationRow, key)
            if not row or row.delivery != "ATTEMPTED":
                raise ScheduleError("SCHEDULE_NOTIFICATION_STATE_CONFLICT")
            row.delivery = result
            return dto(row)

    def listing(self, owner):
        with self.store.factory() as db:
            return [
                dto(row)
                for row in db.scalars(
                    select(SchedulerNotificationRow)
                    .where(SchedulerNotificationRow.owner_id == owner)
                    .order_by(SchedulerNotificationRow.created_at.desc())
                    .limit(100)
                )
            ]
