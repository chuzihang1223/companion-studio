"""Durable, bounded Trace projection. Business tools are never replayed here."""

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime
import queue
import threading
import time
from uuid import uuid4
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout
from sqlalchemy.orm import sessionmaker
from app.core.file_logging import file_logger
from app.database.session import engine as business_engine
from app.database.write_gate import WriteGateTimeout, register_write_engine, write_deadline
from app.models.agent_trace import utc_now
from app.services.trace import privacy
from app.services.trace.command import encode
from app.services.trace.journal import TraceJournal
from app.services.trace.projection import TraceProjection
from app.services.trace.types import ERRORS, PHASES, STATUSES, TraceEventType


class WriterUnavailable(RuntimeError):
    pass


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
        journal_dir=None,
    ):
        self.engine = None
        if factory is None:
            self.engine = create_engine(
                business_engine.url,
                connect_args={"check_same_thread": False, "timeout": 0.1},
                pool_timeout=0.1,
            )

            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA busy_timeout=100")

            factory = sessionmaker(self.engine, expire_on_commit=False)
        self.factory = factory
        with factory() as probe:
            bound = probe.get_bind()
        if bound.dialect.name != "sqlite" or bound.url.database in (None, ":memory:"):
            raise ValueError("TRACE_REQUIRES_FILE_DATABASE")
        self.write_gate = register_write_engine(bound)
        self.queue = queue.Queue(maxsize=max(8, min(capacity, 8192)))
        self.event_limit = max(20, min(event_limit, 10_000))
        self.trace_limit = max(1, min(trace_limit, 5000))
        self.total_limit = max(1000, min(total_limit, 500_000))
        self.retention_days = max(1, min(retention_days, 365))
        self.admission_lock = threading.Lock()
        self.worker = None
        self.closed = self.blocked = False
        self.on_commit = None
        self.lost = OrderedDict()
        self.logger = file_logger("app.trace", "agent-trace.log")
        self.metrics = {
            "generated": 0,
            "generated_events": 0,
            "accepted": 0,
            "accepted_events": 0,
            "committed": 0,
            "committed_events": 0,
            "dropped": 0,
            "rejected": 0,
            "write_failures": 0,
            "write_retries": 0,
            "last_write_failure_code": None,
            "batches": 0,
            "write_ms": 0.0,
            "max_write_ms": 0.0,
            "last_query_ms": 0.0,
            "queue_peak": 0,
            "sqlite_busy_transient": 0,
            "sqlite_busy_terminal": 0,
            "recoverably_pending": 0,
            "needs_review": 0,
        }
        self.journal = TraceJournal(bound.url.database, self.queue.maxsize, journal_dir)
        self.projection = TraceProjection(factory, self)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trace-projection")
        self.inflight = None
        self.stopping = None
        self.recovered_trace_ids = set()
        self.new_trace_ids = set()
        self.recovery_complete = False
        try:
            self._load_pending()
            self._refresh_metrics()
        except BaseException:
            self.close_idle()
            raise

    def _reject(self, trace_id):
        self.metrics["rejected"] += 1
        self.metrics["dropped"] += 1
        if trace_id:
            self._lost(trace_id)
        return False

    def enqueue(self, command, data):
        self.metrics["generated"] += 1
        self.metrics["generated_events"] += int(
            data.get("event_type") in TraceEventType._value2member_map_
        )
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
            return self._reject(None)
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
        with self.admission_lock:
            if self.closed or self.blocked or self.queue.full():
                return self._reject(clean["trace_id"])
            try:
                item = self.journal.accept(command, clean)
            except Exception as error:
                self.logger.error("trace_admission_failed type=%s", type(error).__name__)
                return self._reject(clean["trace_id"])
            self.queue.put_nowait(item)
            if not self.recovery_complete:
                self.new_trace_ids.add(clean["trace_id"])
            self.metrics["accepted"] += 1
            self.metrics["accepted_events"] += int(bool(clean["event_type"]))
            self.metrics["queue_peak"] = max(self.metrics["queue_peak"], self.queue.qsize())
            return True

    def _lost(self, trace_id):
        self.lost[trace_id] = True
        while len(self.lost) > 2048:
            self.lost.popitem(last=False)

    def _load_pending(self):
        for item in self.journal.recover():
            self.queue.put_nowait(item)
            self.recovered_trace_ids.add(item[1]["trace_id"])
        self.metrics["queue_peak"] = max(self.metrics["queue_peak"], self.queue.qsize())

    def _refresh_metrics(self):
        state = self.journal.snapshot()
        for key in ("accepted", "accepted_events", "committed", "committed_events"):
            self.metrics[key] = state[key]
        # A restart cannot erase previously recorded terminal attempts. Keep
        # in-process failures too if a disk error prevented updating the log.
        self.metrics["write_failures"] = max(
            self.metrics["write_failures"], state["write_failures"]
        )
        self.metrics["recoverably_pending"] = sum(
            state["states"].get(s, 0) for s in ("QUEUED", "PERSISTING", "RETRY_PENDING")
        )
        self.metrics["needs_review"] = state["states"].get("NEEDS_REVIEW", 0) + state["states"].get(
            "FAILED", 0
        )
        return state

    def start(self):
        if not self.closed and (self.worker is None or self.worker.done()):
            # Startup recovery precedes the worker. Tests/embedded hosts can
            # start a new empty writer without invoking recover explicitly.
            self.new_trace_ids.clear()
            self.recovery_complete = True
            self.worker = asyncio.create_task(self._run())

    def retry_pending(self):
        if self.closed or self.inflight and not self.inflight.done():
            raise RuntimeError("TRACE_WRITER_NOT_IDLE")
        # One explicit recovery attempt, not an unbounded retry timer. Rebuild
        # solely from durable sources; no tool, model or business action runs.
        self._clear_memory_queue()
        self._load_pending()
        self.blocked = False
        self.start()

    def _take(self):
        batch = []
        for _ in range(50):
            try:
                batch.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return batch

    async def _run(self):
        while True:
            await asyncio.sleep(0.05)
            if self.blocked:
                continue
            batch = self._take()
            if not batch:
                continue
            began = time.perf_counter()
            try:
                sessions = await self._write_bounded(batch)
                if self.on_commit:
                    for session in sessions:
                        try:
                            self.on_commit(session)
                        except Exception:
                            pass
            except Exception as error:
                self._record_failure(batch, error)
            finally:
                elapsed = (time.perf_counter() - began) * 1000
                self.metrics["write_ms"] += elapsed
                self.metrics["max_write_ms"] = max(self.metrics["max_write_ms"], elapsed)
                self.metrics["batches"] += 1
                for _ in batch:
                    self.queue.task_done()

    def _record_failure(self, batch, error):
        code = getattr(getattr(error, "orig", None), "sqlite_errorcode", None)
        self.metrics["last_write_failure_code"] = code
        self.metrics["write_failures"] += 1
        self.metrics["sqlite_busy_terminal"] += int(code is not None and code & 0xFF in (5, 6))
        self.blocked = True
        try:
            self.journal.failure(
                [data["_projection_id"] for _, data in batch],
                isinstance(error, (WriteGateTimeout, WriterUnavailable, PoolTimeout))
                or code is not None
                and code & 0xFF in (5, 6),
            )
        except Exception as journal_error:
            # The original FULL-admitted source still exists, possibly in its
            # previous PERSISTING state. Never turn an ACK/disk error into success.
            self.logger.error("trace_journal_status_failed type=%s", type(journal_error).__name__)
        self.logger.error(
            "trace_batch_failed type=%s sqlite_code=%s count=%d recoverable_source=True",
            type(error).__name__,
            code,
            len(batch),
        )

    async def _write_bounded(self, batch):
        deadline = time.perf_counter() + 2
        work = self.executor.submit(copy_context().run, self._persist, batch, deadline)
        self.inflight = work
        wrapped = asyncio.wrap_future(work)
        cancelled = None
        while True:
            try:
                result = await asyncio.shield(wrapped)
                break
            except asyncio.CancelledError as error:
                if work.cancelled():
                    failure = WriterUnavailable("TRACE_WRITER_NOT_STARTED")
                    if cancelled:
                        self._record_failure(batch, failure)
                        raise cancelled from None
                    raise failure from None
                cancelled = error
                if wrapped.cancelled():
                    # A cancelled wrapper is not evidence that a running thread
                    # ended. Join the actual concurrent Future through a new view.
                    wrapped = asyncio.wrap_future(work)
            except Exception as error:
                if cancelled:
                    self._record_failure(batch, error)
                    raise cancelled from None
                raise
        if cancelled:
            raise cancelled
        return result

    def _persist(self, batch, deadline):
        self.journal.mark([data["_projection_id"] for _, data in batch], "PERSISTING")
        token = write_deadline.set(deadline)
        try:
            result = self._write_retry(batch, deadline)
        finally:
            write_deadline.reset(token)
        # Main transaction/gate has ended before opening the journal ACK. The
        # atomic main receipts make a crash or unknown ACK safe to reconcile.
        changed, events = self.journal.acknowledge(result.receipts)
        self.metrics["committed"] += changed
        self.metrics["committed_events"] += events
        return result

    def _write_retry(self, batch, deadline):
        for attempt in range(16):
            try:
                return self._write(batch)
            except (OperationalError, PoolTimeout) as error:
                code = getattr(getattr(error, "orig", None), "sqlite_errorcode", 0)
                transient = isinstance(error, (WriteGateTimeout, PoolTimeout)) or code & 0xFF in (
                    5,
                    6,
                )
                if not transient or attempt == 15 or time.perf_counter() >= deadline:
                    raise
                self.metrics["write_retries"] += 1
                self.metrics["sqlite_busy_transient"] += int(code & 0xFF in (5, 6))
                time.sleep(min(0.1, 0.025 * (attempt + 1), max(0, deadline - time.perf_counter())))

    def _write(self, batch):
        for command, data in batch:
            if "_projection_id" not in data:
                # Private direct-writer tests also get a stable receipt on replay
                # of the same envelope. Normal admission always supplies all IDs.
                data.update(
                    _projection_id=str(uuid4()),
                    _projection_sequence=0,
                    _projection_stream=self.journal.stream,
                    _projection_digest=encode(command, data)[1],
                )
            if encode(command, data)[1] != data["_projection_digest"]:
                raise ValueError("TRACE_PROJECTION_PAYLOAD_MISMATCH")
        return self.projection.write(batch)

    def _cleanup(self, db):
        return self.projection._cleanup(db)

    async def flush(self, timeout=5):
        end = time.perf_counter() + timeout
        while self.queue.unfinished_tasks and not self.blocked and time.perf_counter() < end:
            await asyncio.sleep(0.01)
        state = self._refresh_metrics()
        return self.queue.unfinished_tasks == 0 and state["pending"] == 0

    def _clear_memory_queue(self):
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break

    async def stop(self):
        # Close admission before the first await. Pending sources remain durable
        # even if the bounded drain cannot finish; RAM accounting is not success.
        with self.admission_lock:
            self.closed = True
        if self.stopping is None:
            self.stopping = asyncio.create_task(self._stop_owned(), name="trace-stop")
        cancelled = False
        while True:
            try:
                await asyncio.shield(self.stopping)
                break
            except asyncio.CancelledError:
                if self.stopping.cancelled():
                    raise
                # A caller may be cancelled repeatedly. It cannot cancel our
                # transaction owner or abandon the journal's open descriptors.
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def _stop_owned(self):
        await self.flush()
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self._clear_memory_queue()
        self._refresh_metrics()
        self.close_idle()

    def close_idle(self):
        if self.worker and not self.worker.done() or self.inflight and not self.inflight.done():
            raise RuntimeError("TRACE_WRITER_NOT_IDLE")
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.journal.close()
        if self.engine:
            self.engine.dispose()

    def recover(self):
        # Called in the existing startup thread before new runtime work. Restore
        # sources first; only then mark genuinely unfinished historical traces.
        end = time.perf_counter() + 5
        while self.queue.unfinished_tasks and not self.blocked and time.perf_counter() < end:
            batch = self._take()
            try:
                self._persist(batch, min(end, time.perf_counter() + 2))
            except Exception as error:
                self._record_failure(batch, error)
            finally:
                for _ in batch:
                    self.queue.task_done()
        pending = {data["trace_id"] for _, data in self.journal.recover()}
        self.projection.recover(excluded=pending | self.new_trace_ids)
        self.new_trace_ids.clear()
        self.recovery_complete = True
        self._refresh_metrics()
