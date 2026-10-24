"""Real SQLite contention: projection retries must not depend on the business loop."""

import asyncio
import sqlite3
import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database.migrations import migrate
from app.models.agent_trace import AgentTrace
from app.services.trace.store import TraceStore


@pytest.mark.parametrize("cancel", [False, True])
def test_owned_projection_retries_and_cancel_join_survive_loop_delay(tmp_path, cancel):
    path = tmp_path / "trace.db"
    engine = create_engine(
        f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 0.1}
    )
    migrate(engine)
    held, release, busy, collect = (threading.Event() for _ in range(4))

    def hold():
        with sqlite3.connect(path) as db:
            db.execute("BEGIN IMMEDIATE")
            held.set()
            assert release.wait(10)
            db.rollback()

    def on_error(context):
        if getattr(context.original_exception, "sqlite_errorcode", 0) & 0xFF == 5:
            busy.set()
            assert collect.wait(5)  # first real error is ready before loop stalls

    event.listen(engine, "handle_error", on_error)
    owner = threading.Thread(target=hold)
    owner.start()
    assert held.wait(2)
    store = TraceStore(sessionmaker(engine, expire_on_commit=False))
    key = str(uuid4())
    assert store.enqueue("create", {"trace_id": key, "task_id": key, "status": "running"})
    timer = threading.Timer(0.25, release.set)

    async def run():
        store.start()
        for _ in range(2000):
            if busy.is_set():
                break
            await asyncio.sleep(0.001)
        assert busy.is_set(), "Expected a real SQLITE_BUSY before releasing the owned lock"
        timer.start()
        collect.set()
        if cancel:
            store.worker.cancel()
            await asyncio.sleep(0.02)
            store.worker.cancel()  # repeated cancellation must still join the thread
            await asyncio.gather(store.worker, return_exceptions=True)
        else:
            # Simulates an existing synchronous business SQLite wait. The
            # release timer is independent of this deliberately stalled loop.
            time.sleep(2.2)
            assert await store.flush()
        with store.factory() as db:
            assert db.scalar(select(AgentTrace).where(AgentTrace.id == key)) is not None
        assert store.metrics["write_retries"] > 0
        assert store.metrics["write_failures"] == store.metrics["dropped"] == 0
        await store.stop()

    try:
        asyncio.run(run())
    finally:
        collect.set()
        release.set()
        owner.join(2)
        if timer.ident is not None:
            timer.join(2)
        engine.dispose()
    assert not owner.is_alive()


@pytest.mark.parametrize("persistent_busy", [True, False])
def test_failed_projection_is_bounded_and_next_batch_can_run(tmp_path, persistent_busy):
    path = tmp_path / "failed-projection.db"
    engine = create_engine(
        f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 0.1}
    )
    migrate(engine)
    store = TraceStore(sessionmaker(engine, expire_on_commit=False))
    lock = sqlite3.connect(path)
    if persistent_busy:
        lock.execute("BEGIN IMMEDIATE")
    key = str(uuid4())
    store.enqueue("create", {"trace_id": key, "task_id": key if persistent_busy else None})

    async def run():
        store.start()
        began = time.monotonic()
        assert await store.flush(timeout=4)
        elapsed = time.monotonic() - began
        assert store.metrics["write_failures"] == store.metrics["dropped"] == 1
        if persistent_busy:
            assert 1.5 <= elapsed < 3.5 and 0 < store.metrics["write_retries"] <= 15
        else:
            assert store.metrics["write_retries"] == 0  # NOT NULL is not BUSY
        lock.rollback()
        good = str(uuid4())
        store.enqueue("create", {"trace_id": good, "task_id": good})
        assert await store.flush()
        with store.factory() as db:
            assert db.get(AgentTrace, key) is None
            assert db.get(AgentTrace, good) is not None
        await store.stop()

    try:
        asyncio.run(run())
    finally:
        lock.close()
        engine.dispose()
