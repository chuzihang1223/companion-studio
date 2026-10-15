import asyncio
import base64
import json
import time
from io import BytesIO
from types import SimpleNamespace
import wave

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import chat_stream_v4 as stream_module
from app.api import voice as voice_api
from app.database.session import Base
from app.main import app
from app.models.message import Message
from app.models.message_voice import MessageVoice
from app.services.voice import message_cache


class FakeStreamingLLM:
    async def stream_chat(self, messages):
        for part in ("真的", "？我替你", "高兴！"):
            yield part
            if part == "？我替你":
                await asyncio.sleep(
                    0.05
                )  # Simulate model tokens still arriving after the first sentence.


class FakeLongStreamingLLM:
    async def stream_chat(self, messages):
        yield "太好了，我们一起庆祝吧！"
        await asyncio.sleep(0.15)
        yield "接下来也慢慢来。"


class FakeVoice:
    available = True
    ready = True
    name = "indextts-2.5-expressive"

    def __init__(self):
        self.calls = []

    def synthesize(self, text, control):
        self.calls.append((text, control.reference))
        output = BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(32000)
            writer.writeframes(b"\x10\x00" * 3200)
        return {
            "audio_base64": base64.b64encode(output.getvalue()).decode(),
            "sample_rate": 32000,
            "generation_ms": 12,
            "engine": self.name,
        }

    def stop(self):
        pass

    def start_background(self):
        pass


def test_stream_saves_text_then_generates_voice_in_background(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'stream.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(stream_module, "SessionLocal", factory)
    monkeypatch.setattr(voice_api, "SessionLocal", factory)
    monkeypatch.setattr(message_cache, "settings", SimpleNamespace(data_root=tmp_path))
    fake_voice = FakeVoice()
    try:
        with TestClient(app) as client:
            app.state.llm_service = FakeStreamingLLM()
            app.state.voice_client = fake_voice
            app.state.voice_jobs.voice = fake_voice
            app.state.voice_jobs.session_factory = factory
            app.state.voice_jobs.data_root = tmp_path
            response = client.post(
                "/api/chat/stream",
                json={
                    "character_id": "assistant",
                    "message": "我面试通过了！",
                    "expression": "rich",
                },
            )
            assert response.status_code == 200
            events = []
            for block in response.text.strip().split("\n\n"):
                lines = block.splitlines()
                events.append((lines[0][7:], json.loads(lines[1][6:])))
            assert [name for name, _ in events].count("text_delta") >= 3
            assert (
                "".join(payload["text"] for name, payload in events if name == "text_delta")
                == "真的？我替你高兴！"
            )
            assert events[-1][0] == "done"
            completed = [payload for name, payload in events if name == "completed"]
            assert len(completed) == 1
            assert not [payload for name, payload in events if name == "voice_job"]
            assert len([payload for name, payload in events if name == "voice_saved"]) == 1
            audio = client.get("/api/voice/audio/" + completed[0]["message_id"])
            assert audio.content[:4] == b"RIFF"
            assert fake_voice.calls
            assert (tmp_path / "audio" / f"{completed[0]['message_id']}.wav").is_file()
        with factory() as db:
            assert [row.role for row in db.query(Message).order_by(Message.created_at).all()] == [
                "user",
                "assistant",
            ]
            assert db.get(MessageVoice, completed[0]["message_id"]).status == "ready"
    finally:
        engine.dispose()


def test_completed_first_sentence_can_play_before_all_text_finishes(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'early.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(stream_module, "SessionLocal", factory)
    monkeypatch.setattr(voice_api, "SessionLocal", factory)
    monkeypatch.setattr(message_cache, "settings", SimpleNamespace(data_root=tmp_path))
    with TestClient(app) as client:
        app.state.llm_service = FakeLongStreamingLLM()

        class LiveVoice(FakeVoice):
            config = SimpleNamespace(speed=1)

            async def synthesize_live(self, queue, speed, on_pcm):
                parts = []
                while (text := await queue.get()) is not None:
                    self.calls.append(text)
                    raw = b"\x10\x00" * 3200
                    parts.append(raw)
                    await on_pcm(raw, 32000, 1)
                return parts, 32000, "test-live"

        fake_voice = LiveVoice()
        app.state.voice_client = fake_voice
        app.state.voice_jobs.voice = fake_voice
        app.state.voice_jobs.session_factory = factory
        app.state.voice_jobs.data_root = tmp_path
        result = client.post(
            "/api/chat/stream", json={"character_id": "assistant", "message": "我成功了！"}
        )
        assert result.status_code == 200
        events = []
        for block in result.text.strip().split("\n\n"):
            lines = block.splitlines()
            events.append((lines[0][7:], json.loads(lines[1][6:])))
        names = [name for name, _ in events]
        assert "audio_pcm" in names, names
        assert names.index("audio_pcm") < names.index("completed")
        text_positions = [index for index, name in enumerate(names) if name == "text_delta"]
        assert names.index("audio_pcm") < text_positions[1], names
        assert "voice_job" not in names
        assert fake_voice.calls
    engine.dispose()
