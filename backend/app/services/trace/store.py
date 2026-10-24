"""Bounded projection writer. Only its worker thread opens trace transactions."""

import asyncio
from collections import OrderedDict
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
import json
import queue
import time

from sqlalchemy import create_engine, delete, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.file_logging import file_logger
from app.database.session import engine as business_engine
from app.models.agent_trace import AgentTrace, TraceEvent, TraceSpan, utc_now
from app.models.work import WorkSession
from app.services.trace import privacy
from app.services.trace.types import ERRORS, PHASES, STATUSES, TERMINAL, TraceEventType


class TraceStore:
    def __init__(
        self,
        factory=None,
        *,
        capacity=2048,
        event_limit=5000,
        trace_limit=1000,
        total_limit=200_000,
        retention_days=30,
    ):
        self.engine = None
        if factory is None:
            # Separate connection policy: tracing must not wait eight seconds for
            # a busy business transaction or change the business pool's timeout.
            self.engine = create_engine(
                business_engine.url, connect_args={"check_same_thread": False, "timeout": 0.1}
            )

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA busy_timeout=100")

            factory = sessionmaker(self.engine, expire_on_commit=False)
        self.factory = factory
        self.queue = queue.Queue(maxsize=max(8, min(capacity, 8192)))
        self.event_limit = max(20, min(event_limit, 10_000))
        self.trace_limit = max(1, min(trace_limit, 5000))
        self.total_limit = max(1000, min(total_limit, 500_000))
        self.retention_days = max(1, min(retention_days, 365))
        self.metrics = {
            "dropped": 0,
            "write_failures": 0,
            "write_retries": 0,
            "last_write_failure_code": None,
            "batches": 0,
            "write_ms": 0.0,
            "max_write_ms": 0.0,
            "last_query_ms": 0.0,
        }
        self.worker = None
        self.closed = False
        self.on_commit = None
        self.last_cleanup = 0.0
        self.lost = OrderedDict()
        self.logger = file_logger("app.trace", "agent-trace.log")

    def enqueue(self, command: str, data: dict) -> bool:
        """Admission is synchronous and nonblocking; no raw results enter RAM."""
        if self.closed:
            return False
        clean = {
            key: privacy.uuid_value(data.get(key))
            for key in (
                "trace_id",
                "task_id",
                "conversation_id",
                "work_session_id",
                "span_id",
                "parent_span_id",
            )
        }
        if not clean["trace_id"]:
            return False
        clean.update(
            kind=data.get("kind") if data.get("kind") in PHASES else "task",
            phase=data.get("phase") if data.get("phase") in PHASES else "task",
            status=data.get("status") if data.get("status") in STATUSES | {"denied"} else "running",
            tool_name=privacy.identifier(data.get("tool_name")),
            model_name=privacy.identifier(data.get("model_name")),
            metadata=privacy.metadata(data.get("metadata")),
            evidence=privacy.evidence(data.get("evidence")),
            error_code=data.get("error_code") if data.get("error_code") in ERRORS else None,
            verification_status=data.get("verification_status")
            if data.get("verification_status")
            in {"passed", "rule_passed", "failed", "not_provided"}
            else "not_provided",
        )
        clean["event_type"] = (
            str(data["event_type"])
            if data.get("event_type") in TraceEventType._value2member_map_
            else None
        )
        clean["duration_ms"] = privacy.metadata({"duration_ms": data.get("duration_ms")}).get(
            "duration_ms"
        )
        clean["created_at"] = (
            data.get("created_at") if isinstance(data.get("created_at"), datetime) else utc_now()
        )
        clean["source_work_event_id"] = (
            data.get("source_work_event_id")
            if isinstance(data.get("source_work_event_id"), int)
            else None
        )
        clean["incomplete"] = data.get("incomplete") is True
        try:
            self.queue.put_nowait((command, clean))
            return True
        except queue.Full:
            self.metrics["dropped"] += 1
            self._lost(clean["trace_id"])
            return False

    def _lost(self, trace_id):
        self.lost[trace_id] = True
        while len(self.lost) > 2048:
            self.lost.popitem(last=False)

    def start(self):
        if self.worker is None:
            self.worker = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            await asyncio.sleep(0.05)
            batch = []
            for _ in range(50):
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                continue
            start = time.perf_counter()
            try:
                sessions = await self._write_bounded(batch)
                if self.on_commit:
                    for session in sessions:
                        try:
                            self.on_commit(session)
                        except Exception:
                            pass  # A closed UI subscription cannot affect execution.
            except Exception as error:
                self.metrics["last_write_failure_code"] = getattr(
                    getattr(error, "orig", None), "sqlite_errorcode", None
                )
                self.metrics["write_failures"] += 1
                self.metrics["dropped"] += len(batch)
                for _, data in batch:
                    self._lost(data["trace_id"])
                self.logger.warning(
                    "trace_batch_failed type=%s sqlite_code=%s count=%d",
                    type(error).__name__,
                    self.metrics["last_write_failure_code"],
                    len(batch),
                )
            finally:
                elapsed = (time.perf_counter() - start) * 1000
                self.metrics["write_ms"] += elapsed
                self.metrics["max_write_ms"] = max(self.metrics["max_write_ms"], elapsed)
                self.metrics["batches"] += 1
                for _ in batch:
                    self.queue.task_done()

    async def _write_bounded(self, batch):
        # The business loop can be inside its own synchronous SQLite wait.
        # Retry in the writer thread so collecting a BUSY exception does not
        # depend on that loop becoming available before the existing deadline.
        deadline = time.perf_counter() + 2
        # An executor Future is not an auxiliary Task that loop shutdown can
        # independently cancel while its thread is still using the database.
        work = asyncio.get_running_loop().run_in_executor(
            None, copy_context().run, self._write_retry, batch, deadline
        )
        cancelled = None
        while True:
            try:
                result = await asyncio.shield(work)
                break
            except asyncio.CancelledError as error:
                # Even repeated cancellation must join the real transaction
                # before queue.task_done or engine disposal can run.
                cancelled = error
            except Exception:
                if cancelled:
                    raise cancelled from None
                raise
        if cancelled:
            raise cancelled
        return result

    def _write_retry(self, batch, deadline):
        # SQLite may reject a read-to-write transaction upgrade while a business
        # checkpoint commits. Retry the rolled-back projection transaction only;
        # the queue stays bounded and no tool or business transaction is repeated.
        for attempt in range(16):
            try:
                return self._write(batch)
            except OperationalError as error:
                code = getattr(error.orig, "sqlite_errorcode", 0)
                if code & 0xFF not in {5, 6} or attempt == 15 or time.perf_counter() >= deadline:
                    raise
                self.metrics["write_retries"] += 1
                time.sleep(min(0.1, 0.025 * (attempt + 1), max(0, deadline - time.perf_counter())))

    def _write(self, batch):
        sessions = set()
        with self.factory() as db:
            # Reserve the short SQLite write transaction before reading rows.
            # Deferred read->write upgrades can fail immediately with BUSY_SNAPSHOT
            # even when busy_timeout is set, losing an entire projection batch.
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            for command, data in batch:
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
                    continue  # Earlier trace creation may have failed; never retry a tool.
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
                if data["event_type"]:
                    if row.event_count < self.event_limit:
                        row.event_count += 1
                        db.add(
                            TraceEvent(
                                trace_id=row.id,
                                sequence=row.event_count,
                                span_id=data["span_id"],
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
            db.execute(delete(TraceEvent).where(TraceEvent.trace_id.in_(remove)))
            db.execute(delete(TraceSpan).where(TraceSpan.trace_id.in_(remove)))
            db.execute(delete(AgentTrace).where(AgentTrace.id.in_(remove)))

    async def flush(self, timeout=5) -> bool:
        end = time.perf_counter() + timeout
        while self.queue.unfinished_tasks and time.perf_counter() < end:
            await asyncio.sleep(0.01)
        return self.queue.unfinished_tasks == 0

    async def stop(self):
        await self.flush()
        self.closed = True
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        if self.engine:
            await asyncio.to_thread(self.engine.dispose)

    def recover(self):
        """No offline wall-clock inference and absolutely no tool replay."""
        with self.factory() as db:
            rows = db.scalars(select(AgentTrace).where(AgentTrace.status.not_in(TERMINAL))).all()
            for row in rows:
                row.incomplete = True
                if row.status not in {"ready", "waiting_permission"}:
                    row.status, row.completed_at = "interrupted", utc_now()
                    if row.event_count >= self.event_limit:
                        row.dropped_events += 1
                    else:
                        row.event_count += 1
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
