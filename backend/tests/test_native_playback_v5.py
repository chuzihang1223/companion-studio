import base64
from io import BytesIO
import wave
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import voice as voice_api
from app.database.session import Base
from app.main import app
from app.models import Conversation, Message
from app.services.voice import message_cache
from app.services.voice.message_cache import save_message_audio


def test_fish_native_audio_urls_are_limited_to_saved_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_api, "settings", SimpleNamespace(data_root=tmp_path))
    folder = tmp_path / "voice_test"
    folder.mkdir()
    test_audio = folder / "fish_auth_test.mp3"
    sample_audio = folder / "fish_character_3.mp3"
    test_audio.write_bytes(b"ID3test")
    sample_audio.write_bytes(b"ID3sample")
    assert voice_api.native_audio_path("/api/voice/fish/test/audio", None) == test_audio
    assert (
        voice_api.native_audio_path("/api/voice/fish/character-test/3/audio", None) == sample_audio
    )
    for url in ("/api/voice/fish/character-test/6/audio", "/api/voice/fish/../test/audio"):
        with pytest.raises(HTTPException) as error:
            voice_api.native_audio_path(url, None)
        assert error.value.status_code == 400


def test_secured_native_audio_controls_replayable_pcm(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "test-control-token")
    monkeypatch.setattr(message_cache, "settings", type("Settings", (), {"data_root": tmp_path})())
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(voice_api, "SessionLocal", factory)
    with factory() as db:
        conversation = Conversation(character_id="assistant")
        db.add(conversation)
        db.flush()
        message = Message(
            character_id="assistant",
            conversation_id=conversation.id,
            role="assistant",
            content="你好呀",
        )
        db.add(message)
        db.commit()
        output = BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(22050)
            writer.writeframes(b"\x00\x00" * 22050)
        save_message_audio(
            db,
            message,
            [(base64.b64encode(output.getvalue()).decode(), 0, 0)],
            "indextts-2.5-expressive",
        )
        message_id = message.id
    headers = {"X-Companion-Control-Token": "test-control-token"}
    with TestClient(app) as client:
        url = f"/api/voice/audio/{message_id}"
        assert client.get(url).headers["content-type"].startswith("audio/wav")
        assert client.get(f"{url}/diagnostic").json()["bit_depth"] == 16
        assert client.post("/api/voice/native/play", json={"audio_url": url}).status_code == 403
        started = client.post(
            "/api/voice/native/play", headers=headers, json={"audio_url": url, "volume": 0.5}
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "playing"
        assert client.post("/api/voice/native/pause", headers=headers).json()["status"] == "paused"
        assert (
            client.post("/api/voice/native/resume", headers=headers).json()["status"] == "playing"
        )
        assert client.post("/api/voice/native/stop", headers=headers).json()["status"] == "stopped"
