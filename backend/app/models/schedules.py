"""Additive scheduling journal. Definition, trigger opportunity and execution are distinct."""

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database.session import Base


class ScheduleRow(Base):
    __tablename__ = "schedule_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(24), index=True)
    body: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[str] = mapped_column(Text)
    next_due: Mapped[str | None] = mapped_column(String(40), index=True)
    condition_state: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    generated: Mapped[int] = mapped_column(Integer, default=0)


class ScheduledOccurrenceRow(Base):
    __tablename__ = "scheduled_occurrences"
    __table_args__ = (UniqueConstraint("schedule_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(String(36), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[str] = mapped_column(String(40), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[str | None] = mapped_column(String(40))
    attempt_id: Mapped[str | None] = mapped_column(String(36))
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[str | None] = mapped_column(String(40))
    finished_at: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(String(80))


class ScheduledRunRow(Base):
    __tablename__ = "scheduled_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurrence_id: Mapped[str] = mapped_column(String(36), unique=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    state: Mapped[str] = mapped_column(String(24))
    body: Mapped[str] = mapped_column(Text)


class SchedulerLeaseRow(Base):
    __tablename__ = "scheduler_leases"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100))
    process_identity: Mapped[str] = mapped_column(String(100))
    token: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[str] = mapped_column(String(40))


class SchedulerStateRow(Base):
    __tablename__ = "scheduler_settings"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    body: Mapped[str] = mapped_column(Text)


class SchedulerSourceRow(Base):
    __tablename__ = "scheduler_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    body: Mapped[str] = mapped_column(Text)


class SchedulerEventRow(Base):
    __tablename__ = "scheduler_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    schedule_id: Mapped[str] = mapped_column(String(36), index=True)
    occurrence_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40))


class SchedulerNotificationRow(Base):
    __tablename__ = "scheduler_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    schedule_id: Mapped[str] = mapped_column(String(36))
    occurrence_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[str] = mapped_column(String(40))
    delivery: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
