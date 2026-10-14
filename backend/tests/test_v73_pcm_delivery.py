"""First audio must deliver its last PCM frame before playback completion."""

import asyncio
import base64
from io import BytesIO
import json
import time
import wave

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database.session import Base
from app.models import Conversation
from app.api import chat_stream_v4
from app.services.voice import message_cache


@pytest.mark.parametrize("finish_llm_first", [True, False])
def test_pcm_completion_never_overtakes_last_chunk(tmp_path, monkeypatch, finish_llm_first):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'stream.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Conversation(id="test-stream", character_id="assistant"))
        db.commit()
    monkeypatch.setattr(chat_stream_v4, "SessionLocal", factory)
    from types import SimpleNamespace

    monkeypatch.setattr(message_cache, "settings", SimpleNamespace(data_root=tmp_path))
    pcm = b"\x01\x00" * 4410
    output = BytesIO()
    with wave.open(output, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes(pcm)

    class FakeLLM:
        async def stream_chat(self, _messages):
            yield "我在这里，一直陪着你。"
            if not finish_llm_first:
                await asyncio.sleep(0.15)

    class FakeVoice:
        name = "fake"
        config = SimpleNamespace(speed=1)

        async def synthesize_live(self, queue, _speed, on_pcm):
            while await queue.get() is not None:
                pass
            await asyncio.sleep(0.03)
            parts = []
            for start in range(0, len(pcm), 882):
                raw = pcm[start : start + 882]
                parts.append(raw)
                await on_pcm(raw, 44100, 1)
            return parts, 44100, "test-live"

        def stop(self):
            pass

    with TestClient(app) as client:
        app.state.llm_service = FakeLLM()
        app.state.voice_client = FakeVoice()
        monkeypatch.setattr(
            app.state.voice_jobs,
            "enqueue",
            lambda message_id, **_kwargs: {
                "job_id": "test-job",
                "message_id": message_id,
                "status": "queued",
            },
        )
        response = client.post(
            "/api/chat/stream",
            json={
                "character_id": "assistant",
                "conversation_id": "test-stream",
                "message": "陪陪我",
                "voice_enabled": True,
            },
        )
        events = []
        for block in response.text.split("\n\n"):
            if not block.strip():
                continue
            lines = block.splitlines()
            events.append((lines[0][7:], json.loads(lines[1][6:])))
        names = [name for name, _data in events]
        assert "error" not in names
        assert names.count("audio_end") == 1
        frames = [
            base64.b64decode(data["pcm_base64"]) for name, data in events if name == "audio_pcm"
        ]
        assert b"".join(frames) == pcm
        assert names.index("audio_end") > max(
            i for i, name in enumerate(names) if name == "audio_pcm"
        )
        assert names[-1] == "done"
        # Restore shutdown-capable provider before lifespan cleanup.
        app.state.voice_client = type("StoppedVoice", (), {"stop": lambda self: None})()
    engine.dispose()
