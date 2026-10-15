import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.agent_traces import router
from app.models import Conversation, WorkEvent
from app.models.agent_trace import AgentTrace, utc_now
from app.services.trace.repository import TraceRepository
from app.services.trace.store import TraceStore
from app.services.work_state import WorkState
from test_trace_persistence import trace_database as trace_database


def test_trace_api_auth_scope_pagination_and_deletion(trace_database, monkeypatch):
    factory, conversation, session = trace_database
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "local-test-control")
    trace, other = str(uuid4()), str(uuid4())
    store = TraceStore(factory)

    @asynccontextmanager
    async def lifespan(app):
        store.start()
        store.enqueue(
            "create",
            {
                "trace_id": trace,
                "task_id": str(uuid4()),
                "conversation_id": conversation,
                "event_type": "TASK_CREATED",
            },
        )
        store.enqueue(
            "bind", {"trace_id": trace, "conversation_id": conversation, "work_session_id": session}
        )
        for _ in range(3):
            store.enqueue(
                "event",
                {
                    "trace_id": trace,
                    "event_type": "OBSERVATION_CREATED",
                    "metadata": {"prompt": "private input", "Authorization": "private credential"},
                },
            )
        assert await store.flush()
        yield
        await store.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.trace_repository = TraceRepository(store)
    app.include_router(router)
    with factory() as db:
        db.add(Conversation(id=other, character_id="assistant", conversation_type="work"))
        db.commit()
    with TestClient(app) as client:
        base = "/api/agent/traces"
        params = {"conversation_id": conversation}
        headers = {"X-Companion-Control-Token": "local-test-control"}
        assert client.get(base, params=params).status_code == 403
        assert (
            client.get(
                base, params=params, headers={"X-Companion-Control-Token": "wrong"}
            ).status_code
            == 403
        )
        client.headers.update(headers)
        assert client.get(base).status_code == 422
        assert client.get(base, params={"conversation_id": "../../escape"}).status_code == 422
        assert client.get(base, params={**params, "limit": 1000}).status_code == 422
        assert (
            client.get(
                base, params={**params, "since": "2026-11-01", "until": "2026-01-01"}
            ).status_code
            == 422
        )
        assert (
            client.get(base, params={**params, "tool_name": "https://private.invalid"}).status_code
            == 422
        )
        assert client.get(base, params={**params, "work_session_id": other}).status_code == 404
        assert client.get(base, params=params).json()["items"][0]["trace_id"] == trace
        for suffix in ["", "/events", "/summary"]:
            assert (
                client.get(
                    base + "/" + trace + suffix, params={"conversation_id": other}
                ).status_code
                == 404
            )
        first = client.get(base + "/" + trace + "/events", params={**params, "limit": 2}).json()
        assert first["schema_version"] == 1 and first["next_cursor"] == 2
        assert "private" not in json.dumps(first)
        second = client.get(base + "/" + trace + "/events", params={**params, "after": 2}).json()
        assert [row["sequence"] for row in first["items"] + second["items"]] == [1, 2, 3, 4]
        assert client.delete(base + "/" + trace, params=params).status_code == 409
        with factory() as db:
            db.get(AgentTrace, trace).status = "completed"
            db.commit()
        assert client.delete(base + "/" + trace, params=params).json() == {"deleted": True}
        assert client.get(base, params=params).json()["items"] == []
        with factory() as db:
            assert db.get(Conversation, conversation) is not None


def test_1000_event_write_query_performance_and_retention(trace_database):
    factory, conversation, session = trace_database

    async def run():
        store = TraceStore(factory, trace_limit=2)
        store.start()
        trace = str(uuid4())
        store.enqueue(
            "create", {"trace_id": trace, "task_id": str(uuid4()), "conversation_id": conversation}
        )
        started = time.perf_counter()
        for _ in range(1000):
            assert store.enqueue(
                "event",
                {
                    "trace_id": trace,
                    "event_type": "OBSERVATION_CREATED",
                    "metadata": {"verified": True},
                },
            )
        enqueue_ms = (time.perf_counter() - started) * 1000
        assert await store.flush(timeout=10)
        repo = TraceRepository(store)
        all_rows = []
        for after in range(0, 1000, 250):
            all_rows.extend(repo.events(trace, conversation, after=after, limit=250)["items"])
        assert len(all_rows) == 1000
        assert store.metrics["dropped"] == 0
        assert len(json.dumps(all_rows)) < 2_000_000
        assert enqueue_ms < 3000 and store.metrics["last_query_ms"] < 1500
        print(
            json.dumps(
                {
                    "trace_performance": {
                        "events": 1000,
                        "enqueue_ms": enqueue_ms,
                        **store.metrics,
                        "dto_bytes": len(json.dumps(all_rows)),
                    }
                }
            )
        )
        with factory() as db:
            db.get(AgentTrace, trace).started_at = utc_now() - timedelta(days=40)
            db.add(WorkEvent(session_id=session, type="workflow_updated", detail_json="{}"))
            store._cleanup(db)
            db.commit()
            assert db.get(AgentTrace, trace) is None
            assert db.scalar(select(WorkEvent)) is not None
        await store.stop()

    asyncio.run(run())


def test_trace_notification_shares_sse_without_task_progress(trace_database, monkeypatch):
    factory, _, session = trace_database
    from app.services import work_state

    monkeypatch.setattr(work_state, "SessionLocal", factory)

    async def run():
        work = WorkState()
        stream = work.stream(session)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        started = time.perf_counter()
        work.notify_trace(session)
        assert "event: trace_updated" in await asyncio.wait_for(pending, 1)
        print(
            json.dumps({"trace_sse_local_notification_ms": (time.perf_counter() - started) * 1000})
        )
        assert work.snapshot(session)["trace_revision"] == 1
        with factory() as db:
            assert db.scalar(select(WorkEvent)) is None
        await stream.aclose()

    asyncio.run(run())
