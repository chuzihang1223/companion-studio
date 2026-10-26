"""SSE ownership/connection invariants, using real isolated SQLite sessions."""

import asyncio
import gc
import weakref
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.services import work_state


@pytest.fixture
def streams(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + (tmp_path / "streams.db").as_posix())
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(work_state, "SessionLocal", factory)
    yield work_state.WorkState(), engine
    engine.dispose()


def test_RESOURCE_001_normal_stream_close_releases_last_bucket_and_queue(streams):
    state, _ = streams

    async def scenario():
        session = state.create("Synthetic stream", None, [])
        stream = state.stream(session)
        await anext(stream)
        queue = weakref.ref(next(iter(state.subscribers[session])))
        await stream.aclose()
        gc.collect()
        assert not state.subscribers and queue() is None

    asyncio.run(scenario())


def test_RESOURCE_002_cancelled_listener_releases_its_bucket(streams):
    state, _ = streams

    async def scenario():
        session = str(uuid4())
        stream = state.stream(session)
        waiting = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        assert len(state.subscribers[session]) == 1
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert session not in state.subscribers

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["client-disconnect", "database-query"])
def test_RESOURCE_003_exception_disconnect_cleans_without_leaking_connection(
    streams, monkeypatch, failure
):
    state, engine = streams

    async def scenario():
        session = state.create("Synthetic stream", None, [])
        stream = state.stream(session)
        if failure == "client-disconnect":
            await anext(stream)
            with pytest.raises(ConnectionError):
                await stream.athrow(ConnectionError("synthetic disconnect"))
        else:

            def unavailable():
                raise ConnectionError("synthetic database unavailable")

            monkeypatch.setattr(work_state, "SessionLocal", unavailable)
            with pytest.raises(ConnectionError):
                await anext(stream)
        assert not state.subscribers and engine.pool.checkedout() == 0

    asyncio.run(scenario())


def test_RESOURCE_004_shared_and_distinct_sessions_preserve_live_subscribers(streams):
    state, _ = streams

    async def scenario():
        first = state.create("First", None, [])
        second = state.create("Second", None, [])
        a, b, c = state.stream(first), state.stream(first), state.stream(second)
        try:
            await anext(a)
            await anext(b)
            await anext(c)
            bucket = state.subscribers[first]
            assert len(bucket) == 2
            await a.aclose()
            assert state.subscribers[first] is bucket and len(bucket) == 1
            assert len(state.subscribers[second]) == 1
            state.notify_trace(first)
            assert "event: trace_updated" in await asyncio.wait_for(anext(b), 1)
            await b.aclose()
            assert first not in state.subscribers and second in state.subscribers
        finally:
            await a.aclose()
            await b.aclose()
            await c.aclose()
        assert not state.subscribers

    asyncio.run(scenario())


def test_RESOURCE_005_repeated_stream_cancellation_does_not_accumulate_keys(streams):
    state, engine = streams

    async def scenario():
        queues = []
        for _ in range(250):
            session = str(uuid4())
            stream = state.stream(session)
            waiting = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            queues.append(weakref.ref(next(iter(state.subscribers[session]))))
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
            assert not state.subscribers and engine.pool.checkedout() == 0
        return queues

    # The final gather callback can itself still reference the last cancelled
    # Task until the loop drains. Check reachability after closing that observer.
    queues = asyncio.run(scenario())
    gc.collect()
    assert all(reference() is None for reference in queues)


def test_RESOURCE_011_slow_consumer_does_not_hold_a_database_connection(streams):
    state, engine = streams

    async def scenario():
        session = state.create("Synthetic slow consumer", None, [])
        stream = state.stream(session)
        try:
            assert "id:" in await anext(stream)
            assert engine.pool.checkedout() == 0
            assert len(state.subscribers[session]) == 1
        finally:
            await stream.aclose()
        assert not state.subscribers

    asyncio.run(scenario())
