"""TRACE-FIX001–030: adversarial outcomes, not writer implementation mirrors.

Cloud providers, desktop observations and PCM are synthetic in the six original
runtime scenarios. Crash probes run only a newly created local Python child.
"""

import asyncio
import copy
import logging
import sqlite3
import threading
import time
from uuid import UUID, uuid4

from fastapi import FastAPI
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.database.write_gate import register_write_engine
from app.models import Conversation, WorkSession
from app.models.agent_trace import AgentTrace, TraceEvent, TraceSpan
from app.services.trace.journal import TraceJournal
from app.services.trace.repository import TraceRepository
from trace_hotfix_cases import (
    TraceCase,
    balanced,
    crash_child,
    exercise,
    file_snapshot,
    receipt_rows,
    recover_child,
    runtime_case,
    until,
)


@pytest.fixture
def database(tmp_path):
    return TraceCase(tmp_path)


def test_TRACE_FIX_001_single_writer_commits_source_and_projection(database):
    h = database

    async def scenario():
        store = h.store()
        key = h.create(store)
        assert store.journal.snapshot()["accepted"] == 1
        assert h.count(AgentTrace, id=key) == 0
        store.start()
        assert await store.flush()
        assert balanced(store)["committed_events"] == 1
        assert len(receipt_rows(h, key)) == h.count(TraceEvent, trace_id=key) == 1

    exercise(h, scenario)


def test_TRACE_FIX_002_same_file_engines_coordinate_actual_writers(database):
    h = database

    async def scenario():
        gate = register_write_engine(h.business)
        store = h.store()
        with h.business_factory() as business:
            business.execute(
                text("UPDATE conversations SET preferred_mode='work' WHERE id=:id"), {"id": h.owner}
            )
            key = h.create(store)
            store.start()
            await until(lambda: gate.snapshot()["waiting"] == 1)
            assert h.count(AgentTrace, id=key) == 0
            business.commit()
        assert await store.flush()
        balanced(store)
        assert store.metrics["write_failures"] == 0

    exercise(h, scenario)


def test_TRACE_FIX_003_real_short_busy_is_retried_without_duplicate(database):
    h = database

    async def scenario():
        store, lock = h.store(), h.hold()
        key = h.create(store)
        store.start()
        await until(lambda: store.metrics["write_retries"] > 0)
        lock.rollback()
        assert await store.flush()
        assert balanced(store)["accepted"] == 1
        assert h.count(TraceEvent, trace_id=key) == 1
        assert store.metrics["write_failures"] == 0

    exercise(h, scenario)


def test_TRACE_FIX_004_terminal_busy_is_explicit_durable_pending(database):
    h = database

    async def scenario():
        store = h.store()
        h.hold()
        key = h.create(store)
        began = time.monotonic()
        store.start()
        await until(lambda: store.metrics["write_failures"] == 1)
        assert time.monotonic() - began < 3.5
        assert not await store.flush(timeout=0.01)
        value = store.journal.snapshot()
        assert value["accepted"] == value["pending"] == 1 and value["committed"] == 0
        assert value["states"]["RETRY_PENDING"] == 1
        assert store.metrics["dropped"] == 0 and h.count(AgentTrace, id=key) == 0

    exercise(h, scenario)


def test_TRACE_FIX_005_real_transaction_failure_rolls_back_projection_and_receipt(database):
    h = database

    async def scenario():
        with h.business.begin() as db:
            db.exec_driver_sql(
                "CREATE TRIGGER qa_reject BEFORE INSERT ON trace_events BEGIN SELECT RAISE(ABORT,'controlled projection rejection'); END"
            )
        store = h.store()
        key = h.create(store)
        store.start()
        await until(lambda: store.metrics["write_failures"] == 1)
        assert not await store.flush(timeout=0.01)
        assert h.count(AgentTrace, id=key) == h.count(TraceEvent, trace_id=key) == 0
        assert receipt_rows(h, key) == []
        assert store.journal.snapshot()["pending"] == 1
        with h.business.begin() as db:
            db.exec_driver_sql("DROP TRIGGER qa_reject")
        assert h.engine.pool.checkedout() == 0

    exercise(h, scenario)


def test_TRACE_FIX_006_pool_connections_return_after_repeated_batches(database):
    h = database

    async def scenario():
        store = h.store()
        key = h.create(store)
        store.start()
        for _ in range(4):
            for _ in range(20):
                assert store.enqueue(
                    "event", {"trace_id": key, "event_type": "OBSERVATION_CREATED"}
                )
            assert await store.flush()
            assert h.engine.pool.checkedout() == 0
        assert h.count(TraceEvent, trace_id=key) == 81
        assert balanced(store)["committed"] == 81

    exercise(h, scenario)


def test_TRACE_FIX_007_each_writer_session_has_one_thread_owner(database):
    h = database

    async def scenario():
        observed = {}
        main = threading.get_ident()

        def owner(session, transaction, connection):
            if connection.engine is h.engine:
                observed.setdefault(session, set()).add(threading.get_ident())

        event.listen(Session, "after_begin", owner)
        try:
            store = h.store()
            h.create(store)
            store.start()
            assert await store.flush()
        finally:
            event.remove(Session, "after_begin", owner)
        assert observed and all(len(threads) == 1 for threads in observed.values())
        assert any(main not in threads for threads in observed.values())
        balanced(store)

    exercise(h, scenario)


def test_TRACE_FIX_008_independent_projection_engine_error_preserves_business(database):
    h = database

    async def scenario():
        def fail(connection, cursor, statement, *_):
            if statement.lstrip().upper().startswith("BEGIN IMMEDIATE"):
                raise OSError("synthetic engine failure must not escape into business")

        event.listen(h.engine, "before_cursor_execute", fail)
        store = h.store()
        h.create(store)
        store.start()
        try:
            await until(lambda: store.metrics["write_failures"] == 1)
            assert not await store.flush(timeout=0.01)
            with h.business_factory.begin() as db:
                db.execute(
                    text("UPDATE conversations SET preferred_mode='work' WHERE id=:id"),
                    {"id": h.owner},
                )
            assert store.journal.snapshot()["states"]["NEEDS_REVIEW"] == 1
        finally:
            event.remove(h.engine, "before_cursor_execute", fail)

    exercise(h, scenario)


def test_TRACE_FIX_009_cancel_during_sql_joins_real_writer(database):
    h = database

    async def scenario():
        entered, release = threading.Event(), threading.Event()
        h.releases.append(release)

        def barrier(connection, cursor, statement, *_):
            if statement.lower().startswith("insert into agent_traces"):
                entered.set()
                assert release.wait(4)

        event.listen(h.engine, "before_cursor_execute", barrier)
        store = h.store()
        key = h.create(store)
        store.start()
        try:
            await until(entered.is_set)
            store.worker.cancel()
            await asyncio.sleep(0.01)
            store.worker.cancel()
            await asyncio.sleep(0.01)
            assert not store.worker.done() and store.queue.unfinished_tasks > 0
            release.set()
            result = await asyncio.gather(store.worker, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError)
            assert h.count(AgentTrace, id=key) == 1
            balanced(store)
        finally:
            release.set()
            event.remove(h.engine, "before_cursor_execute", barrier)

    exercise(h, scenario)


def test_TRACE_FIX_010_cancel_after_commit_still_acknowledges_once(database, monkeypatch):
    h = database

    async def scenario():
        store = h.store()
        entered, release = threading.Event(), threading.Event()
        h.releases.append(release)
        original = store.journal.acknowledge

        def ack(receipts):
            entered.set()
            assert release.wait(4)
            return original(receipts)

        monkeypatch.setattr(store.journal, "acknowledge", ack)
        key = h.create(store)
        store.start()
        await until(entered.is_set)
        assert h.count(TraceEvent, trace_id=key) == 1
        store.worker.cancel()
        await asyncio.sleep(0.01)
        assert not store.worker.done()
        release.set()
        await asyncio.gather(store.worker, return_exceptions=True)
        assert balanced(store)["committed"] == 1 and len(receipt_rows(h, key)) == 1

    exercise(h, scenario)


def test_TRACE_FIX_011_cancel_then_real_busy_failure_remains_recoverable(database):
    h = database

    async def scenario():
        store, lock = h.store(), h.hold()
        key = h.create(store)
        store.start()
        await until(lambda: store.metrics["write_retries"] > 0)
        store.worker.cancel()
        result = await asyncio.gather(store.worker, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert store.metrics["write_failures"] == 1 and store.metrics["dropped"] == 0
        assert store.journal.snapshot()["pending"] == 1
        lock.rollback()
        await store.stop()
        restored = h.store()
        restored.recover()
        restored.start()
        assert await restored.flush()
        assert balanced(restored)["write_failures"] == 1
        assert h.count(AgentTrace, id=key) == 1

    exercise(h, scenario)


def test_TRACE_FIX_012_loop_shutdown_keeps_accepted_source(tmp_path):
    folder, key = crash_child(tmp_path, "loop")
    recover_child(folder, key)


def test_TRACE_FIX_013_executor_cancelled_future_does_not_spin_forever(tmp_path):
    folder, key = crash_child(tmp_path, "executor")
    recover_child(folder, key)


def test_TRACE_FIX_014_hard_process_exit_leaves_durable_accepted_commands(tmp_path):
    folder, _ = crash_child(tmp_path, "queued")
    journal = TraceJournal(folder / "trace.db", 2048)
    try:
        value = journal.snapshot()
        assert value["accepted"] == value["pending"] == 2 and value["committed"] == 0
        with sqlite3.connect(folder / "trace.db") as db:
            assert db.execute("SELECT count(*) FROM agent_traces").fetchone()[0] == 0
    finally:
        journal.close()


def test_TRACE_FIX_015_restart_replays_pending_projection_not_business(tmp_path):
    folder, key = crash_child(tmp_path, "queued")
    recover_child(folder, key)


def test_TRACE_FIX_016_unknown_commit_replay_queries_receipt_before_mutation(tmp_path):
    folder, key = crash_child(tmp_path, "committed")
    recover_child(folder, key)


def test_TRACE_FIX_017_stable_command_ids_are_unique_in_durable_receipts(database):
    h = database

    async def scenario():
        store = h.store()
        key = h.create(store)
        for _ in range(49):
            assert store.enqueue("event", {"trace_id": key, "event_type": "OBSERVATION_CREATED"})
        ids = [data["_projection_id"] for _, data in list(store.queue.queue)]
        assert len(set(ids)) == 50 and all(str(UUID(key)) == key for key in ids)
        store.start()
        assert await store.flush()
        assert {row.id for row in receipt_rows(h, key)} == set(ids)
        assert balanced(store)["committed"] == 50

    exercise(h, scenario)


def test_TRACE_FIX_018_accepted_commands_events_and_sql_rows_reconcile(database):
    h = database

    async def scenario():
        store = h.store()
        for _ in range(3):
            key = h.create(store)
            for _ in range(7):
                assert store.enqueue(
                    "event", {"trace_id": key, "event_type": "OBSERVATION_CREATED"}
                )
            assert store.enqueue("state", {"trace_id": key, "status": "running"})
            assert store.enqueue(
                "finish", {"trace_id": key, "status": "completed", "event_type": "TASK_COMPLETED"}
            )
        store.start()
        assert await store.flush()
        value = balanced(store)
        assert value["accepted"] == value["committed"] == 30
        assert value["accepted_events"] == value["committed_events"] == 27
        assert h.count(TraceEvent) == 27

    exercise(h, scenario)


def test_TRACE_FIX_019_same_envelope_redelivery_is_idempotent(database):
    h = database

    async def scenario():
        store = h.store()
        key = h.create(store)
        batch = list(store.queue.queue)
        store._write(batch)
        store._write(copy.deepcopy(batch))
        store.start()
        assert await store.flush()
        assert h.count(TraceEvent, trace_id=key) == len(receipt_rows(h, key)) == 1
        assert balanced(store)["committed"] == 1

    exercise(h, scenario)


def test_TRACE_FIX_020_cross_conversation_ownership_and_receipt_binding(database):
    h = database

    async def scenario():
        other, session = str(uuid4()), str(uuid4())
        with h.factory.begin() as db:
            db.add(Conversation(id=other, character_id="assistant", conversation_type="work"))
            db.add(WorkSession(id=session, conversation_id=other, goal="other synthetic"))
        store = h.store()
        first = h.create(store)
        second = h.create(store, conversation_id=other)
        assert store.enqueue(
            "bind", {"trace_id": first, "conversation_id": h.owner, "work_session_id": h.session}
        )
        assert store.enqueue(
            "bind", {"trace_id": second, "conversation_id": other, "work_session_id": session}
        )
        original = list(store.queue.queue)
        store.start()
        assert await store.flush()
        repo = TraceRepository(store)
        assert repo.detail(first, h.owner)["work_session_id"] == h.session
        with pytest.raises(LookupError):
            repo.events(first, other)
        forged = copy.deepcopy(original[0])
        forged[1]["trace_id"] = second
        with pytest.raises(Exception):
            store._write([forged])
        assert h.count(TraceEvent, trace_id=first) == h.count(TraceEvent, trace_id=second) == 1
        balanced(store)

    exercise(h, scenario)


def test_TRACE_FIX_021_span_parent_tree_cannot_cross_traces(database):
    h = database

    async def scenario():
        store = h.store()
        first, second = h.create(store), h.create(store)
        parent, foreign, child, isolated = (str(uuid4()) for _ in range(4))
        for trace, span, ancestor in (
            (first, parent, None),
            (second, foreign, None),
            (first, child, parent),
            (first, isolated, foreign),
        ):
            assert store.enqueue(
                "span_start",
                {
                    "trace_id": trace,
                    "span_id": span,
                    "parent_span_id": ancestor,
                    "kind": "tool",
                    "phase": "tool",
                },
            )
        store.start()
        assert await store.flush()
        with h.factory() as db:
            assert db.get(TraceSpan, child).parent_span_id == parent
            assert db.get(TraceSpan, isolated).parent_span_id is None
            assert db.get(TraceSpan, child).trace_id == first
        balanced(store)
        admitted = store.enqueue(
            "event",
            {
                "trace_id": first,
                "span_id": foreign,
                "event_type": "OBSERVATION_CREATED",
            },
        )
        flushed = await store.flush()
        if admitted and not flushed:
            assert store.journal.snapshot()["pending"] == 1
        with h.factory() as db:
            for item in db.scalars(select(TraceEvent).where(TraceEvent.trace_id == first)):
                if item.span_id:
                    span = db.get(TraceSpan, item.span_id)
                    assert span is not None and span.trace_id == item.trace_id
        if admitted and flushed:
            balanced(store)

    exercise(h, scenario)


def test_TRACE_FIX_022_recovery_checkpoint_references_actual_trace(tmp_path):
    from unified_skill_fixture import unified_host
    from app.models.recovery import RecoveryTask, AgentCheckpoint

    async def scenario():
        async with unified_host(tmp_path) as h:
            plan, result = await h.agent()
            assert result["tools"] and await h.trace.flush()
            with h.trace.factory() as db:
                task = db.get(RecoveryTask, plan["plan_id"])
                assert task and task.trace_id
                trace = db.get(AgentTrace, task.trace_id)
                assert trace.task_id == task.id and trace.conversation_id == h.owner
                points = list(
                    db.scalars(select(AgentCheckpoint).where(AgentCheckpoint.task_id == task.id))
                )
                assert len(points) == 1 and points[0].state == "COMMITTED"
            balanced(h.trace)

    asyncio.run(scenario())


def test_TRACE_FIX_023_evaluation_history_is_read_only_and_keeps_trace_reference(tmp_path):
    from sqlalchemy import create_engine
    from app.database.migrations import migrate
    from app.schemas.evaluation import RunRequest
    from app.services.evaluation.engine import AgentEvaluationEngine
    from app.services.evaluation.repository import EvaluationRepository

    engine = create_engine(f"sqlite:///{(tmp_path / 'evaluation.db').as_posix()}")
    migrate(engine)
    repo = EvaluationRepository(sessionmaker(engine, expire_on_commit=False))
    runner = AgentEvaluationEngine(tmp_path / "e", repo)

    async def scenario():
        try:
            await runner.initialize()
            created = await runner.start(RunRequest(case_ids=["EVAL-003"]), FastAPI())
            await asyncio.gather(*list(runner.tasks.values()))
            rows = repo.results(created["run_id"], 0, 10)["items"]
            assert len(rows) == 1 and rows[0]["execution_status"] == "PASS", rows
            row = rows[0]
            archive = runner.environment.path(created["run_id"], row["attempt_id"])
            before = file_snapshot(archive)
            assert "data/chat.db.trace-pending/pending.sqlite3" in before
            with runner.environment.trace_repository(
                created["run_id"], row["attempt_id"]
            ) as reader:
                detail = reader.detail(row["trace_id"], row["conversation_id"])
                events = reader.events(row["trace_id"], row["conversation_id"])["items"]
                assert detail["task_id"] == row["task_id"] and events
            assert file_snapshot(archive) == before
            assert not runner.environment.active
        finally:
            await runner.close()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_TRACE_FIX_024_private_payload_and_failure_message_never_reach_logs(database, monkeypatch):
    h = database

    async def scenario():
        store = h.store()
        secret = "SYNTHETIC_PRIVATE_INPUT_CANARY_DO_NOT_LOG"
        key = h.create(
            store,
            metadata={"prompt": secret, "path": "C:/Users/Test/private.txt", "executed": True},
        )
        with store.journal.transaction() as db:
            assert secret not in db.execute("SELECT payload FROM commands").fetchone()[0]
        captured = []

        class Capture(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        handler = Capture()
        store.logger.addHandler(handler)

        def fail(_):
            raise RuntimeError(secret)

        monkeypatch.setattr(store, "_write", fail)
        try:
            store.start()
            await until(lambda: store.metrics["write_failures"] == 1)
            assert not await store.flush(timeout=0.01)
            assert captured and all(
                secret not in message and "C:/Users" not in message for message in captured
            )
            assert h.count(AgentTrace, id=key) == 0 and store.journal.snapshot()["pending"] == 1
        finally:
            store.logger.removeHandler(handler)

    exercise(h, scenario)


@pytest.mark.parametrize(
    "kind",
    ["agent", "workflow", "mcp", "scheduler", "computer", "voice"],
    ids=[f"TRACE-FIX-{i:03d}" for i in range(25, 31)],
)
def test_trace_runtime_regression_025_to_030(tmp_path, kind):
    asyncio.run(runtime_case(tmp_path, kind))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("id", str(uuid4())),
        ("sequence", 1729),
        ("trace_id", str(uuid4())),
        ("event", 0),
        ("created", 4102444800.0),
        ("digest", "0" * 64),
        ("envelope_hash", "0" * 64),
    ],
    ids=[
        "command-id",
        "sequence",
        "trace-owner",
        "event-flag",
        "created-time",
        "payload-digest",
        "envelope-checksum",
    ],
)
def test_extra_journal_header_damage_is_preserved_for_review(database, field, replacement):
    """Additional adversarial cases; these are not extra TRACE-FIX identifiers."""
    h = database
    store = h.store()
    key = h.create(store)
    path = store.journal.path
    store.close_idle()
    with sqlite3.connect(path) as db:
        # Column names are a fixed test-owned allowlist, never external input.
        db.execute(f"UPDATE commands SET {field}=?", (replacement,))
    restored = h.store()
    try:
        value = restored.journal.snapshot()
        assert value["accepted"] == value["pending"] == 1
        assert value["committed"] == 0 and value["states"]["NEEDS_REVIEW"] == 1
        assert restored.queue.empty() and h.count(AgentTrace, id=key) == 0
    finally:
        restored.close_idle()
        h.dispose()


def test_extra_repeated_stop_caller_cancellation_joins_commit_and_releases_owner(database):
    h = database

    async def scenario():
        entered, release = threading.Event(), threading.Event()
        h.releases.append(release)

        def barrier(connection, cursor, statement, *_):
            if statement.lower().startswith("insert into agent_traces"):
                entered.set()
                assert release.wait(4)

        event.listen(h.engine, "before_cursor_execute", barrier)
        store = h.store()
        key = h.create(store)
        store.start()
        try:
            await until(entered.is_set)
            stopper = asyncio.create_task(store.stop())
            await until(lambda: store.closed)
            stopper.cancel()
            await asyncio.sleep(0.01)
            stopper.cancel()
            await asyncio.sleep(0.01)
            assert not stopper.done(), "Caller cancellation abandoned its committing writer"
            release.set()
            result = await asyncio.wait_for(
                asyncio.gather(stopper, return_exceptions=True), timeout=3
            )
            assert isinstance(result[0], asyncio.CancelledError)
            assert store.worker.done() and store.queue.unfinished_tasks == 0
            assert store.journal.connection is None and h.engine.pool.checkedout() == 0
            assert h.count(AgentTrace, id=key) == 1
            reopened = TraceJournal(h.path, 2048)
            try:
                value = reopened.snapshot()
                assert value["accepted"] == value["committed"] == 1
                assert value["pending"] == 0
            finally:
                reopened.close()
        finally:
            release.set()
            event.remove(h.engine, "before_cursor_execute", barrier)

    exercise(h, scenario)


def test_extra_evaluation_partial_archive_failure_keeps_pending_until_retry(tmp_path, monkeypatch):
    from contextlib import closing
    import json
    from types import SimpleNamespace
    from app.services.evaluation.environment import EnvironmentManager

    manager = EnvironmentManager(tmp_path / "evaluation")
    run_id, attempt_id = str(uuid4()), str(uuid4())
    home = manager.root / "_w" / "0123456789abcdef"
    (home / "data").mkdir(parents=True)
    (home / "owner.json").write_text(
        json.dumps({"run_id": run_id, "attempt_id": attempt_id}), encoding="utf-8"
    )
    h = TraceCase(home / "data", "chat.db")
    store = h.store()
    key = h.create(store)
    assert store.enqueue(
        "finish", {"trace_id": key, "status": "completed", "event_type": "TASK_COMPLETED"}
    )
    store.close_idle()
    h.dispose()
    attached = []
    repository = SimpleNamespace(
        attempt=lambda run, attempt: None,
        attach_recovered_trace=lambda run, attempt, links: attached.append(links),
    )
    archive = manager.path(run_id, attempt_id)
    original = TraceJournal.archive

    def fail_queue_copy(*_):
        raise OSError("Controlled queue-copy failure after main database backup")

    monkeypatch.setattr(TraceJournal, "archive", staticmethod(fail_queue_copy))
    manager.cleanup_abandoned(repository)
    assert home.is_dir() and (archive / "data" / "chat.db").is_file()
    assert not attached
    with closing(sqlite3.connect(store.journal.path)) as db:
        assert (
            db.execute("SELECT count(*) FROM commands WHERE state!='COMMITTED'").fetchone()[0] == 2
        )
    monkeypatch.setattr(TraceJournal, "archive", staticmethod(original))
    manager.cleanup_abandoned(repository)
    assert not home.exists() and len(attached) == 1
    assert attached[0]["trace_id"] == key
    with manager.trace_repository(run_id, attempt_id) as reader:
        detail = reader.detail(key, h.owner)
        rows = reader.events(key, h.owner)["items"]
        assert detail["status"] == "completed"
        assert [row["event_type"] for row in rows] == ["TASK_CREATED", "TASK_COMPLETED"]
    journal = TraceJournal(archive / "data" / "chat.db", 2048)
    try:
        value = journal.snapshot()
        assert value["accepted"] == value["committed"] == 2 and value["pending"] == 0
    finally:
        journal.close()


def test_extra_failure_state_and_counter_are_atomic_and_survive_reopen(database):
    h = database
    store = h.store()
    h.create(store)
    command_id = list(store.queue.queue)[0][1]["_projection_id"]
    try:
        with store.journal.transaction() as db:
            db.execute(
                "CREATE TRIGGER qa_counter_abort BEFORE UPDATE ON meta "
                "WHEN NEW.key='write_failures' BEGIN "
                "SELECT RAISE(ABORT,'controlled counter update rejection'); END"
            )
        with pytest.raises(sqlite3.IntegrityError):
            store.journal.failure([command_id], True)
        value = store.journal.snapshot()
        assert value["states"] == {"QUEUED": 1} and value["write_failures"] == 0
        with store.journal.transaction() as db:
            db.execute("DROP TRIGGER qa_counter_abort")
        store.journal.failure([command_id], True)
        assert store.journal.snapshot()["states"] == {"RETRY_PENDING": 1}
    finally:
        store.close_idle()
    restored = h.store()
    try:
        assert restored.metrics["write_failures"] == 1
        assert restored.journal.snapshot()["write_failures"] == 1
        assert restored.journal.snapshot()["states"] == {"RETRY_PENDING": 1}
    finally:
        restored.close_idle()
        h.dispose()


def test_extra_readonly_validation_rejects_hardlinked_shm_before_sqlite_opens(database):
    import os
    from pathlib import Path

    h = database
    store = h.store()
    h.create(store)
    path = store.journal.path
    store.close_idle()
    canary = h.root / "controlled-unrelated-canary.bin"
    original = b"SYNTHETIC_CANARY_DO_NOT_MODIFY" * 128
    canary.write_bytes(original)
    sidecar = Path(str(path) + "-shm")
    assert not sidecar.exists()
    os.link(canary, sidecar)
    try:
        assert canary.stat().st_nlink == 2
        with pytest.raises(OSError, match="TRACE_JOURNAL_LINK_REJECTED"):
            TraceJournal(h.path, 2048)
        assert canary.read_bytes() == sidecar.read_bytes() == original
    finally:
        sidecar.unlink()
        h.dispose()
