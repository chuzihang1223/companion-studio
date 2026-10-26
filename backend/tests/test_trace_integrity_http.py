"""Independent public-HTTP proof for durable Trace integrity visibility.

Repository dicts alone cannot prove that FastAPI's response DTO preserves fields.
Only owned SQLite profiles and a local authenticated TestClient are used here.
"""

import asyncio
from contextlib import asynccontextmanager
import json
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.agent_traces import router
from app.models import Conversation, WorkSession
from app.services.trace.repository import TraceRepository
from trace_hotfix_cases import TraceCase, file_snapshot, until


CONTROL = "local-test-control"
BASE = "/api/agent/traces"
PRIVATE = "SYNTHETIC_HTTP_PRIVATE_PAYLOAD_DO_NOT_RETURN"


@pytest.fixture
def http_database(tmp_path):
    case = TraceCase(tmp_path)
    try:
        yield case
    finally:
        case.release_all()
        for store in case.stores:
            if not store.closed:
                store.close_idle()
        case.dispose()


def app_for(repository, lifespan=None):
    app = FastAPI(lifespan=lifespan)
    app.state.trace_repository = repository
    app.include_router(router)
    return app


def authenticated(client):
    client.headers.update({"X-Companion-Control-Token": CONTROL})


@pytest.mark.parametrize("failure", ["busy", "writer"])
def test_integrity_survives_actual_http_response_model_and_keeps_scope(
    http_database, monkeypatch, failure
):
    h = http_database
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", CONTROL)
    other, other_session = str(uuid4()), str(uuid4())
    unused_session = str(uuid4())
    with h.factory.begin() as db:
        db.add(Conversation(id=other, character_id="assistant", conversation_type="work"))
        db.add(WorkSession(id=other_session, conversation_id=other, goal="Synthetic other"))
        db.add(WorkSession(id=unused_session, conversation_id=h.owner, goal="Synthetic unused"))
    store = h.store()
    pending_trace = h.create(
        store,
        work_session_id=h.session,
        metadata={"prompt": PRIVATE, "path": "C:/Users/Test/private.txt"},
    )
    projection_id = list(store.queue.queue)[0][1]["_projection_id"]
    if failure == "busy":
        h.hold()  # Actual independent SQLite writer, not a mocked exception.
    else:

        def failed_writer(_):
            raise OSError(PRIVATE)

        monkeypatch.setattr(store, "_write", failed_writer)

    @asynccontextmanager
    async def lifespan(_):
        store.start()
        try:
            await until(lambda: store.metrics["write_failures"] == 1)
            assert not await store.flush(timeout=0.01)
            yield
        finally:
            await store.stop()

    with TestClient(app_for(TraceRepository(store), lifespan)) as client:
        params = {"conversation_id": h.owner}
        assert client.get(BASE, params=params).status_code == 403
        authenticated(client)
        response = client.get(BASE, params=params)
        assert response.status_code == 200
        page = response.json()
        expected = {
            "pending": int(failure == "busy"),
            "needs_review": int(failure == "writer"),
            "failed": 0,
        }
        assert page["items"] == []
        assert page["integrity"] == expected
        assert all(
            type(value) is int and 0 <= value <= 8192 for value in page["integrity"].values()
        )
        assert (
            client.get(BASE, params={**params, "work_session_id": h.session}).json()["integrity"]
            == expected
        )
        assert client.get(BASE, params={**params, "work_session_id": unused_session}).json()[
            "integrity"
        ] == {"pending": 0, "needs_review": 0, "failed": 0}
        foreign = client.get(BASE, params={"conversation_id": other})
        assert foreign.status_code == 200
        assert foreign.json()["items"] == []
        assert foreign.json()["integrity"] == {"pending": 0, "needs_review": 0, "failed": 0}
        for url, scoped in (
            (BASE, {**params, "work_session_id": other_session}),
            (BASE, {"conversation_id": str(uuid4())}),
            (BASE + "/" + pending_trace, {"conversation_id": other}),
        ):
            rejected = client.get(url, params=scoped)
            assert rejected.status_code == 404
            assert "integrity" not in rejected.json()
        rendered = json.dumps([page, foreign.json()], ensure_ascii=False)
        assert all(
            value not in rendered
            for value in (PRIVATE, "C:/Users", projection_id, pending_trace, other_session)
        )


def test_integrity_http_explicit_event_limit_failure_is_visible(http_database, monkeypatch):
    h = http_database
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", CONTROL)
    store = h.store(event_limit=20)
    trace = h.create(store)
    for _ in range(20):
        assert store.enqueue(
            "event",
            {"trace_id": trace, "conversation_id": h.owner, "event_type": "OBSERVATION_CREATED"},
        )
    command_ids = [data["_projection_id"] for _, data in list(store.queue.queue)]

    @asynccontextmanager
    async def lifespan(_):
        store.start()
        try:
            await until(lambda: store.queue.unfinished_tasks == 0)
            assert not await store.flush(timeout=0.01)
            assert store.journal.snapshot()["states"]["FAILED"] == 1
            yield
        finally:
            await store.stop()

    with TestClient(app_for(TraceRepository(store), lifespan)) as client:
        authenticated(client)
        response = client.get(BASE, params={"conversation_id": h.owner})
        assert response.status_code == 200
        page = response.json()
        assert page["integrity"] == {"pending": 0, "needs_review": 0, "failed": 1}
        assert page["items"][0]["trace_id"] == trace
        assert page["items"][0]["event_count"] == 20
        assert page["items"][0]["incomplete"]
        assert not any(command_id in json.dumps(page) for command_id in command_ids)


def test_readonly_evaluation_http_allows_no_journal_integrity_and_changes_no_files(
    http_database, tmp_path, monkeypatch
):
    from app.services.evaluation.environment import EnvironmentManager

    h = http_database
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", CONTROL)
    store = h.store()
    trace = h.create(store)

    async def seed():
        store.start()
        try:
            assert await store.flush()
        finally:
            await store.stop()

    asyncio.run(seed())
    manager = EnvironmentManager(tmp_path / "archives")
    run_id, attempt_id = str(uuid4()), str(uuid4())
    archive = manager.path(run_id, attempt_id)
    archive.mkdir(parents=True)
    manager.archive_database(h.path, archive)
    before = file_snapshot(archive)
    assert "data/chat.db.trace-pending/pending.sqlite3" in before
    with manager.trace_repository(run_id, attempt_id) as reader:
        with TestClient(app_for(reader)) as client:
            authenticated(client)
            response = client.get(BASE, params={"conversation_id": h.owner})
            assert response.status_code == 200
            page = response.json()
            assert page.get("integrity") is None
            assert page["items"][0]["trace_id"] == trace
    assert file_snapshot(archive) == before
