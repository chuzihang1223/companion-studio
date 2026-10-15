import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.models import Conversation, WorkSession
from app.models.agent_trace import AgentTrace, TraceEvent, TraceSpan
from app.services.trace.store import TraceStore
from app.services.trace.repository import TraceRepository


@pytest.fixture
def trace_database(tmp_path):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "trace.db"), connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    conversation, session = str(uuid4()), str(uuid4())
    with factory() as db:
        db.add(Conversation(id=conversation, character_id="assistant", conversation_type="work"))
        db.add(
            WorkSession(
                id=session, conversation_id=conversation, goal="private goal not copied into traces"
            )
        )
        db.commit()
    yield factory, conversation, session
    engine.dispose()


def test_trace_persistence_order_scope_and_second_pass_redaction(trace_database):
    factory, conversation, session = trace_database
    trace, task, span = (str(uuid4()) for _ in range(3))

    async def scenario():
        store = TraceStore(factory)
        store.start()
        store.enqueue(
            "create",
            {
                "trace_id": trace,
                "task_id": task,
                "conversation_id": conversation,
                "event_type": "TASK_CREATED",
            },
        )
        store.enqueue(
            "bind", {"trace_id": trace, "conversation_id": conversation, "work_session_id": session}
        )
        store.enqueue(
            "span_start",
            {
                "trace_id": trace,
                "span_id": span,
                "kind": "tool",
                "tool_name": "workspace.read",
                "event_type": "TOOL_STARTED",
                "metadata": {
                    "path": "C:/Users/Test/private/file.txt",
                    "Authorization": "Bearer hidden",
                    "executed": True,
                },
            },
        )
        store.enqueue(
            "span_finish",
            {
                "trace_id": trace,
                "span_id": span,
                "status": "failed",
                "event_type": "TOOL_FAILED",
                "error_code": "TIMEOUT",
                "duration_ms": 17.0,
            },
        )
        store.enqueue(
            "finish",
            {
                "trace_id": trace,
                "status": "failed",
                "event_type": "TASK_FAILED",
                "duration_ms": 20.0,
            },
        )
        assert await store.flush()
        repository = TraceRepository(store)
        assert repository.detail(trace, conversation, session)["task_id"] == task
        first = repository.events(trace, conversation, limit=2)
        assert first["has_more"] and first["next_cursor"] == 2
        second = repository.events(trace, conversation, after=2, limit=2)
        assert [event["sequence"] for event in first["items"] + second["items"]] == [1, 2, 3, 4]
        assert second["items"][0]["span"]["duration_ms"] == 17
        output = json.dumps(first)
        assert "private" not in output and "Authorization" not in output and "hidden" not in output
        with factory() as db:
            saved = db.get(TraceSpan, span)
            saved.metadata_json = json.dumps({"prompt": "DO NOT RETURN", "executed": True})
            saved.error_summary = "API credential: DO NOT RETURN"
            db.commit()
        assert "DO NOT RETURN" not in json.dumps(repository.events(trace, conversation))
        with pytest.raises(LookupError):
            repository.events(trace, str(uuid4()))
        assert repository.summary(trace, conversation)["tools_executed"] == 1
        await store.stop()

    asyncio.run(scenario())
    with factory() as db:
        assert db.get(AgentTrace, trace).status == "failed"


def test_event_limit_and_writer_failure_are_bounded(trace_database, monkeypatch):
    factory, conversation, _ = trace_database

    async def scenario():
        store = TraceStore(factory, capacity=32, event_limit=20)
        trace = str(uuid4())
        store.start()
        store.enqueue(
            "create", {"trace_id": trace, "task_id": str(uuid4()), "conversation_id": conversation}
        )
        for _ in range(25):
            store.enqueue("event", {"trace_id": trace, "event_type": "OBSERVATION_CREATED"})
        assert await store.flush()
        with factory() as db:
            row = db.get(AgentTrace, trace)
            assert row.event_count == 20 and row.incomplete and row.dropped_events == 5

        def broken(_):
            raise RuntimeError("sensitive SQL input must not be logged")

        monkeypatch.setattr(store, "_write", broken)
        for _ in range(50):
            store.enqueue("event", {"trace_id": trace, "event_type": "OBSERVATION_CREATED"})
        assert store.queue.qsize() <= 32
        assert await store.flush()
        assert store.metrics["write_failures"] and store.metrics["dropped"]
        await store.stop()

    asyncio.run(scenario())


def test_restart_marks_interrupted_without_inventing_duration(trace_database):
    factory, conversation, _ = trace_database
    trace, span = str(uuid4()), str(uuid4())
    with factory() as db:
        db.add(
            AgentTrace(
                id=trace, task_id=str(uuid4()), conversation_id=conversation, status="running"
            )
        )
        db.add(TraceSpan(id=span, trace_id=trace, kind="model", phase="model"))
        db.commit()
    store = TraceStore(factory)
    store.recover()
    store.recover()
    with factory() as db:
        assert db.get(AgentTrace, trace).status == "interrupted"
        assert db.get(TraceSpan, span).duration_ms is None
        events = list(db.scalars(select(TraceEvent).where(TraceEvent.trace_id == trace)))
        assert len(events) == 1 and events[0].event_type == "TASK_INTERRUPTED"
