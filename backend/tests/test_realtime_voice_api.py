import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.realtime_voice import router
from app.models.agent_trace import TraceEvent
from app.services.realtime_voice.sessions import VoiceSessionManager
from app.services.trace.recorder import TraceRecorder
from app.services.trace.repository import TraceRepository
from app.services.trace.store import TraceStore
from test_realtime_voice_core import MockASR, packet
from test_trace_persistence import trace_database as trace_database


def test_voice_token_owner_final_and_read_only_trace(trace_database, monkeypatch):
    factory, conversation, _ = trace_database
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "local-test-control")
    monkeypatch.setattr("app.api.realtime_voice.SessionLocal", factory)
    store = TraceStore(factory)

    @asynccontextmanager
    async def lifespan(app):
        store.start()
        yield
        await app.state.realtime_voice.close()
        await store.stop()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.state.trace_recorder = TraceRecorder(store)
    app.state.trace_repository = TraceRepository(store)
    app.state.characters = {"assistant": {}}
    app.state.realtime_voice = VoiceSessionManager(app, MockASR)
    owner = str(uuid4())
    base = "/api/realtime-voice"
    with TestClient(app) as client:
        body = {"owner_id": owner, "conversation_id": conversation, "character_id": "assistant"}
        assert client.post(base + "/start", json=body).status_code == 403
        client.headers["X-Companion-Control-Token"] = "local-test-control"
        assert (
            client.post(base + "/start", json={**body, "conversation_id": str(uuid4())}).status_code
            == 404
        )
        response = client.post(base + "/start", json=body)
        assert response.status_code == 200
        scope = {"owner_id": owner, "voice_session_id": response.json()["voice_session_id"]}
        turn = client.post(base + "/begin", json=scope).json()
        scope.update({key: turn[key] for key in ("turn_id", "generation_id")})
        assert (
            client.post(
                base + "/frame", json={**scope, **packet(), "timestamp": 0, "path": "unexpected"}
            ).status_code
            == 422
        )
        assert (
            client.post(base + "/begin", json={**scope, "owner_id": str(uuid4())}).status_code
            == 404
        )
        for sequence in range(3):
            result = client.post(
                base + "/frame", json={**scope, **packet(sequence=sequence), "timestamp": 0}
            )
            assert result.status_code == 200
        final = client.post(base + "/finalize", json=scope)
        assert final.status_code == 200 and final.json()["text"] == "Synthetic final transcript"
        # Generation never starts on ASR final, and a frontend playback claim
        # cannot mark an unsubmitted FINAL as completed or speaking.
        assert (
            client.post(base + "/event", json={**scope, "event": "playback_started"}).status_code
            == 409
        )
        assert client.post(base + "/stop", json=scope).status_code == 200
        assert client.post(base + "/finalize", json=scope).status_code == 404
        # Flush is run on this TestClient's application event loop.
        client.portal.call(store.flush)
        listing = client.post(
            base + "/trace/read", json={"operation": "list", "conversation_id": conversation}
        )
        assert listing.status_code == 200 and len(listing.json()["items"]) == 1
        trace = listing.json()["items"][0]
        events = client.post(
            base + "/trace/read",
            json={
                "operation": "events",
                "conversation_id": conversation,
                "trace_id": trace["trace_id"],
            },
        )
        assert events.status_code == 200
        encoded = events.text
        assert "ASR_FINAL_RECEIVED" in encoded and "generation_id" in encoded
        assert "Synthetic final transcript" not in encoded and "pcm_base64" not in encoded
        assert (
            client.post(
                base + "/trace/read",
                json={
                    "operation": "events",
                    "conversation_id": str(uuid4()),
                    "trace_id": trace["trace_id"],
                },
            ).status_code
            == 404
        )
    with factory() as db:
        assert db.scalars(
            select(TraceEvent).where(TraceEvent.event_type == "ASR_FINAL_RECEIVED")
        ).all()


def test_remote_tts_requires_its_own_consent(trace_database, monkeypatch):
    factory, conversation, _ = trace_database
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "local-test-control")
    monkeypatch.setattr("app.api.realtime_voice.SessionLocal", factory)
    app = FastAPI()
    app.include_router(router)
    fish = SimpleNamespace(available=True)
    app.state.fish_voice = fish
    app.state.voice_client = SimpleNamespace(choose=lambda: fish)
    app.state.characters = {"assistant": {}}
    app.state.realtime_voice = VoiceSessionManager(app, MockASR)
    body = {
        "owner_id": str(uuid4()),
        "conversation_id": conversation,
        "character_id": "assistant",
        "tts_provider": "configured",
    }
    with TestClient(app) as client:
        client.headers["X-Companion-Control-Token"] = "local-test-control"
        assert client.post("/api/realtime-voice/start", json=body).status_code == 403
        assert not app.state.realtime_voice.rows
