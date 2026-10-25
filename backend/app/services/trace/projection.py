"""Atomic, idempotent projection only. It never executes/replays a business tool."""

from datetime import timedelta, timezone
import json
import time
from sqlalchemy import delete, func, select
from app.database.write_gate import claim_write
from app.models.agent_trace import (
    AgentTrace,
    TraceEvent,
    TraceSpan,
    TraceProjectionReceipt,
    TraceProjectionTotals,
    utc_now,
)
from app.models.work import WorkSession
from app.services.trace import privacy
from app.services.trace.types import ERRORS, TERMINAL


class ProjectionResult(set):
    def __init__(self):
        super().__init__()
        self.receipts = []


def projection_totals(db):
    row = db.get(TraceProjectionTotals, 1)
    if row is None:
        row = TraceProjectionTotals(
            id=1,
            committed_commands=0,
            committed_events=0,
            legacy_events=db.scalar(select(func.count()).select_from(TraceEvent)),
            recovery_events=0,
            pruned_events=0,
            pruned_traces=0,
            limited_events=0,
        )
        db.add(row)
    return row


class TraceProjection:
    def __init__(self, factory, owner):
        self.factory, self.owner = factory, owner
        self.last_cleanup = 0.0

    def __getattr__(self, name):
        return getattr(self.owner, name)

    @staticmethod
    def _receipt_capacity(db, incoming):
        # Sources expire into NEEDS_REVIEW after24h. Keep dedupe receipts at
        # least48h; never discard live receipts merely to obtain a passing gate.
        count = db.scalar(select(func.count()).select_from(TraceProjectionReceipt))
        if count + incoming > 500000:
            db.execute(
                delete(TraceProjectionReceipt).where(
                    TraceProjectionReceipt.applied_at < utc_now() - timedelta(days=2)
                )
            )
            count = db.scalar(select(func.count()).select_from(TraceProjectionReceipt))
            if count + incoming > 500000:
                raise ValueError("TRACE_RECEIPT_CAPACITY")

    def write(self, batch):
        sessions = ProjectionResult()
        with self.factory() as db:
            # Reserve the short SQLite write transaction before reading rows.
            # Deferred read->write upgrades can fail immediately with BUSY_SNAPSHOT
            # even when busy_timeout is set, losing an entire projection batch.
            claim_write(db)
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            totals = projection_totals(db)
            self._receipt_capacity(db, len(batch))
            for command, data in batch:
                receipt = db.get(TraceProjectionReceipt, data["_projection_id"])
                if receipt:
                    if (
                        receipt.trace_id != data["trace_id"]
                        or receipt.payload_hash != data["_projection_digest"]
                        or receipt.stream_id != data["_projection_stream"]
                        or receipt.sequence != data["_projection_sequence"]
                    ):
                        raise ValueError("TRACE_RECEIPT_MISMATCH")
                    sessions.receipts.append((receipt.id, receipt.event_written))
                    continue
                latest = db.scalar(
                    select(func.max(TraceProjectionReceipt.sequence)).where(
                        TraceProjectionReceipt.trace_id == data["trace_id"],
                        TraceProjectionReceipt.stream_id == data["_projection_stream"],
                    )
                )
                if (
                    latest is not None
                    and data["_projection_sequence"] > 0
                    and latest >= data["_projection_sequence"]
                ):
                    raise ValueError("TRACE_PROJECTION_OUT_OF_ORDER")
                row = db.get(AgentTrace, data["trace_id"])
                if command == "create" and row is None:
                    row = AgentTrace(
                        id=data["trace_id"],
                        task_id=data["task_id"],
                        conversation_id=data["conversation_id"],
                        started_at=data["created_at"],
                    )
                    db.add(row)
                    db.flush()
                if row is None:
                    raise ValueError("TRACE_PREREQUISITE_MISSING")
                if (
                    (data["task_id"] and data["task_id"] != row.task_id)
                    or (
                        data["conversation_id"]
                        and row.conversation_id not in {None, data["conversation_id"]}
                    )
                    or (
                        data["work_session_id"]
                        and row.work_session_id not in {None, data["work_session_id"]}
                    )
                ):
                    raise ValueError("TRACE_PROJECTION_SCOPE")
                row.updated_at = data["created_at"]
                row.incomplete = row.incomplete or data["incomplete"] or row.id in self.lost
                if command == "bind":
                    work = (
                        db.get(WorkSession, data["work_session_id"])
                        if data["work_session_id"]
                        else None
                    )
                    if (
                        work
                        and work.conversation_id == data["conversation_id"]
                        and row.conversation_id in {None, work.conversation_id}
                    ):
                        row.work_session_id, row.conversation_id = work.id, work.conversation_id
                    else:
                        row.incomplete = True
                if command == "state":
                    row.status = data["status"]
                if command == "segment":
                    for span in db.scalars(
                        select(TraceSpan).where(
                            TraceSpan.trace_id == row.id, TraceSpan.completed_at.is_(None)
                        )
                    ):
                        span.status, span.completed_at, span.duration_ms = (
                            "interrupted",
                            data["created_at"],
                            None,
                        )
                    row.incomplete = True
                if command == "finish":
                    row.status, row.completed_at, row.duration_ms = (
                        data["status"],
                        data["created_at"],
                        data["duration_ms"],
                    )
                if command == "span_start" and row.span_count < self.event_limit:
                    parent = (
                        db.get(TraceSpan, data["parent_span_id"])
                        if data["parent_span_id"]
                        else None
                    )
                    db.add(
                        TraceSpan(
                            id=data["span_id"],
                            trace_id=row.id,
                            parent_span_id=parent.id
                            if parent and parent.trace_id == row.id
                            else None,
                            kind=data["kind"],
                            phase=data["phase"],
                            tool_name=data["tool_name"],
                            model_name=data["model_name"],
                            started_at=data["created_at"],
                            metadata_json=json.dumps(data["metadata"]),
                        )
                    )
                    row.span_count += 1
                elif command == "span_start":
                    row.incomplete = True
                    row.dropped_events += 1
                if command == "span_finish":
                    span = db.get(TraceSpan, data["span_id"])
                    if span and span.trace_id == row.id:
                        span.completed_at, span.duration_ms, span.status = (
                            data["created_at"],
                            data["duration_ms"],
                            data["status"],
                        )
                        span.error_code = data["error_code"]
                        span.error_summary = ERRORS.get(data["error_code"])
                        span.verification_status = data["verification_status"]
                        span.retry_count = int(data["metadata"].get("attempt", 0))
                        span.metadata_json = json.dumps(
                            privacy.metadata({**json.loads(span.metadata_json), **data["metadata"]})
                        )
                        span.evidence_json = json.dumps(data["evidence"])
                event_written = bool(data["event_type"]) and row.event_count < self.event_limit
                owned_span_id = data["span_id"]
                if owned_span_id:
                    event_span = db.get(TraceSpan, owned_span_id)
                    if event_span is None or event_span.trace_id != row.id:
                        # An event may arrive before its root span. Omit an
                        # unavailable reference; never link another trace's tree.
                        owned_span_id = None
                        row.incomplete = row.incomplete or event_span is not None
                if data["event_type"]:
                    if row.event_count < self.event_limit:
                        row.event_count += 1
                        db.add(
                            TraceEvent(
                                trace_id=row.id,
                                sequence=row.event_count,
                                span_id=owned_span_id,
                                source_work_event_id=data["source_work_event_id"],
                                event_type=data["event_type"],
                                phase=data["phase"],
                                status=data["status"],
                                created_at=data["created_at"],
                                data_json=json.dumps(
                                    {
                                        key: data[key]
                                        for key in (
                                            "metadata",
                                            "evidence",
                                            "duration_ms",
                                            "error_code",
                                            "verification_status",
                                        )
                                    }
                                ),
                            )
                        )
                    else:
                        row.dropped_events += 1
                        row.incomplete = True
                if row.work_session_id:
                    sessions.add(row.work_session_id)
                db.flush()
                event_id = (
                    db.scalar(
                        select(TraceEvent.id).where(
                            TraceEvent.trace_id == row.id,
                            TraceEvent.sequence == row.event_count,
                        )
                    )
                    if event_written
                    else None
                )
                db.add(
                    TraceProjectionReceipt(
                        id=data["_projection_id"],
                        trace_id=row.id,
                        stream_id=data["_projection_stream"],
                        sequence=data["_projection_sequence"],
                        payload_hash=data["_projection_digest"],
                        event_written=event_written,
                        event_id=event_id,
                    )
                )
                totals.committed_commands += 1
                totals.committed_events += int(event_written)
                totals.limited_events += int(bool(data["event_type"]) and not event_written)
                sessions.receipts.append((data["_projection_id"], event_written))
            if time.perf_counter() - self.last_cleanup >= 5:
                self._cleanup(db)
                self.last_cleanup = time.perf_counter()
            db.commit()
        return sessions

    def _cleanup(self, db):
        cutoff = utc_now() - timedelta(days=self.retention_days)
        rows = db.scalars(select(AgentTrace).order_by(AgentTrace.started_at.desc())).all()
        total = sum(row.event_count for row in rows)
        remove = []
        for index, row in enumerate(rows):
            created = row.started_at.replace(tzinfo=timezone.utc)
            if created < cutoff or index >= self.trace_limit:
                remove.append(row.id)
                total -= row.event_count
        for row in reversed(rows):
            if total <= self.total_limit:
                break
            if row.id not in remove and row.status in TERMINAL | {"ready", "waiting_permission"}:
                remove.append(row.id)
                total -= row.event_count
        if remove:
            totals = projection_totals(db)
            totals.pruned_events += db.execute(
                delete(TraceEvent).where(TraceEvent.trace_id.in_(remove))
            ).rowcount
            db.execute(delete(TraceSpan).where(TraceSpan.trace_id.in_(remove)))
            totals.pruned_traces += db.execute(
                delete(AgentTrace).where(AgentTrace.id.in_(remove))
            ).rowcount

    def recover(self, excluded=()):
        """No offline wall-clock inference and absolutely no tool replay."""
        with self.factory() as db:
            claim_write(db)
            totals = projection_totals(db)
            rows = db.scalars(select(AgentTrace).where(AgentTrace.status.not_in(TERMINAL))).all()
            rows = [row for row in rows if row.id not in excluded]
            for row in rows:
                row.incomplete = True
                if row.status not in {"ready", "waiting_permission"}:
                    row.status, row.completed_at = "interrupted", utc_now()
                    if row.event_count >= self.event_limit:
                        row.dropped_events += 1
                    else:
                        row.event_count += 1
                        totals.recovery_events += 1
                        db.add(
                            TraceEvent(
                                trace_id=row.id,
                                sequence=row.event_count,
                                event_type="TASK_INTERRUPTED",
                                phase="task",
                                status="interrupted",
                                data_json=json.dumps(
                                    {
                                        "error_code": "PROCESS_RESTARTED",
                                        "metadata": {
                                            "incomplete": True,
                                            "reason": "process_restarted",
                                        },
                                    }
                                ),
                            )
                        )
                for span in db.scalars(
                    select(TraceSpan).where(
                        TraceSpan.trace_id == row.id, TraceSpan.completed_at.is_(None)
                    )
                ):
                    span.status, span.completed_at, span.error_code = (
                        "interrupted",
                        utc_now(),
                        "PROCESS_RESTARTED",
                    )
                    span.error_summary = ERRORS["PROCESS_RESTARTED"]
                    span.duration_ms = None
            db.commit()
