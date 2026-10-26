"""Concurrent deletion must preserve the durable lifetime event ledger."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.database.write_gate import register_write_engine
from app.models import Conversation
from app.models.agent_trace import AgentTrace, TraceEvent, TraceProjectionTotals, utc_now
from app.services.trace.repository import TraceRepository
from app.services.trace.store import TraceStore


@pytest.fixture
def deletion(tmp_path):
    path = tmp_path / "deletion.db"
    engine = create_engine(
        "sqlite:///" + path.as_posix(), connect_args={"check_same_thread": False, "timeout": 8}
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=8000")

    gate = register_write_engine(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    owner, other = str(uuid4()), str(uuid4())
    traces = [str(uuid4()), str(uuid4())]
    with factory.begin() as db:
        db.add_all(
            [
                Conversation(id=key, character_id="assistant", conversation_type="work")
                for key in (owner, other)
            ]
        )
        for trace in traces:
            db.add(
                AgentTrace(
                    id=trace,
                    task_id=str(uuid4()),
                    conversation_id=owner,
                    status="completed",
                    event_count=1,
                )
            )
            db.add(
                TraceEvent(
                    trace_id=trace,
                    sequence=1,
                    event_type="TASK_CREATED",
                    phase="task",
                    status="completed",
                )
            )
        db.add(TraceProjectionTotals(id=1, legacy_events=2))
    repository = TraceRepository(SimpleNamespace(factory=factory, metrics={"last_query_ms": 0}))
    yield SimpleNamespace(
        path=path,
        engine=engine,
        gate=gate,
        factory=factory,
        owner=owner,
        other=other,
        traces=traces,
        repository=repository,
    )
    assert engine.pool.checkedout() == 0
    assert not gate.snapshot()["owned"] and not gate.snapshot()["waiting"]
    engine.dispose()


def accounting(h):
    with h.factory() as db:
        totals = db.get(TraceProjectionTotals, 1)
        events = db.scalar(select(func.count()).select_from(TraceEvent))
        traces = db.scalar(select(func.count()).select_from(AgentTrace))
        assert (
            events
            == totals.legacy_events
            + totals.committed_events
            + totals.recovery_events
            - totals.pruned_events
        )
        return events, traces, totals.pruned_events, totals.pruned_traces


def external_writer(h):
    writer = sqlite3.connect(h.path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    return writer


def race(h, first, second):
    """Overlap real writers; all test barriers precede application admission."""
    writer = external_writer(h)
    start = threading.Barrier(3)

    def run(operation):
        start.wait(timeout=3)
        return operation()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run, first), pool.submit(run, second)
        try:
            start.wait(timeout=3)
            deadline = time.perf_counter() + 2
            while time.perf_counter() < deadline:
                state = h.gate.snapshot()
                if state["owned"] and state["waiting"] >= 1:
                    break
                time.sleep(0.002)
            else:
                raise AssertionError("Actual writer contention was not observed")
        finally:
            writer.commit()
            writer.close()
        return a.result(timeout=5), b.result(timeout=5)


def test_DB_FIX_001_concurrent_deletions_preserve_both_prune_counts(deletion):
    h = deletion
    a, b = race(h, *(lambda key=key: h.repository.remove(key, h.owner) for key in h.traces))
    assert a == b == {"deleted": True}
    assert accounting(h) == (0, 0, 2, 2)


def test_DB_FIX_002_delete_and_actual_projection_retention_share_fresh_totals(deletion):
    h = deletion
    with h.factory.begin() as db:
        db.get(AgentTrace, h.traces[1]).started_at = utc_now() - timedelta(days=2)
    store = TraceStore(h.factory, retention_days=1)
    assert store.enqueue(
        "create",
        {
            "trace_id": str(uuid4()),
            "task_id": str(uuid4()),
            "conversation_id": h.owner,
            "event_type": "TASK_CREATED",
        },
    )
    batch = store._take()
    try:
        result, _ = race(
            h,
            lambda: h.repository.remove(h.traces[0], h.owner),
            lambda: store._persist(batch, time.perf_counter() + 2),
        )
        assert result == {"deleted": True} and accounting(h) == (1, 1, 2, 2)
        journal = store.journal.snapshot()
        assert journal["accepted"] == journal["committed"] == 1
        assert (
            journal["pending"]
            == journal["pending_commands"]
            == journal["pending_payload_bytes"]
            == 0
        )
    finally:
        for _ in batch:
            store.queue.task_done()
        store.close_idle()


def test_DB_FIX_003_external_writer_commits_before_deletion_reads_totals(deletion):
    h = deletion
    writer = external_writer(h)
    writer.execute("DELETE FROM trace_events WHERE trace_id=?", (h.traces[1],))
    writer.execute("DELETE FROM agent_traces WHERE id=?", (h.traces[1],))
    writer.execute("UPDATE trace_projection_totals SET pruned_events=1,pruned_traces=1 WHERE id=1")
    began, selected = threading.Event(), threading.Event()

    @event.listens_for(h.engine, "before_cursor_execute")
    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if threading.current_thread().name.startswith("deletion-probe"):
            if statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
                began.set()
            if statement.lstrip().upper().startswith("SELECT"):
                selected.set()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="deletion-probe") as pool:
        task = pool.submit(h.repository.remove, h.traces[0], h.owner)
        try:
            assert began.wait(2)
            time.sleep(0.03)
            assert not selected.is_set() and not task.done()
        finally:
            writer.commit()
            writer.close()
        assert task.result(timeout=5) == {"deleted": True}
    assert accounting(h) == (0, 0, 2, 2)


@pytest.mark.parametrize("reason", ["other-owner", "running"])
def test_DB_FIX_004_005_refused_deletions_roll_back_and_release_lease(deletion, reason):
    h = deletion
    if reason == "running":
        with h.factory.begin() as db:
            db.get(AgentTrace, h.traces[0]).status = "running"
    with pytest.raises(LookupError if reason == "other-owner" else ValueError):
        h.repository.remove(h.traces[0], h.other if reason == "other-owner" else h.owner)
    assert accounting(h) == (2, 2, 0, 0)


def test_DB_FIX_006_mid_delete_error_rolls_back_before_successful_retry(deletion):
    h = deletion

    def fail_after_delete(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("DELETE FROM TRACE_EVENTS"):
            raise RuntimeError("synthetic deletion fault")

    event.listen(h.engine, "after_cursor_execute", fail_after_delete)
    try:
        with pytest.raises(RuntimeError, match="synthetic deletion fault"):
            h.repository.remove(h.traces[0], h.owner)
    finally:
        event.remove(h.engine, "after_cursor_execute", fail_after_delete)
    assert accounting(h) == (2, 2, 0, 0)
    assert h.repository.remove(h.traces[0], h.owner) == {"deleted": True}
    assert accounting(h) == (1, 1, 1, 1)
