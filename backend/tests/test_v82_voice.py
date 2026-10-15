import asyncio
import base64
from io import BytesIO
import json
from types import SimpleNamespace
import wave
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.session import Base
from app.models import Conversation, Message
from app.services.voice.continuous_pipeline import continuous_chat_events
from app.services.voice.stream_chunker import SpeechStreamChunker
from app.services.voice.speech_filter import StreamingSpeechFilter, speakable_text
from app.services.voice import message_cache


def test_semantic_chunking_preserves_text_and_weak_boundaries():
    text = "我们可以先把今天的小事说清楚，慢慢来，不需要着急。" * 20
    chunker = SpeechStreamChunker(min_chars=20, max_chars=60)
    chunks = []
    for i in range(0, len(text), 3):
        chunks += chunker.feed(text[i : i + 3])
    chunks += chunker.flush()
    assert "".join(chunks) == text
    assert all(20 <= len(chunk) <= 60 for chunk in chunks[:-1])


def test_split_code_fences_cannot_be_spoken():
    filter_ = StreamingSpeechFilter()
    parts = ["你可以这样写：\n`", '``python\nprint("Hello!', '")\n``', "`\n代码已经放好了。"]
    spoken = "".join(filter_.feed(part) for part in parts) + filter_.feed("", final=True)
    assert "print" not in spoken and "Hello" not in spoken
    assert "代码已经放好了" in spoken
    assert "Traceback" not in speakable_text(
        '结果在这里。\nTraceback (most recent call last):\nFile "x.py"'
    )


@pytest.mark.parametrize("fail_middle", [False, True])
def test_one_utterance_preserves_pcm_order_and_never_replays_after_failure(
    tmp_path, monkeypatch, fail_middle
):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "voice.db"), connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(Conversation(id="voice-conversation", character_id="assistant"))
        db.commit()
    monkeypatch.setattr(message_cache, "settings", SimpleNamespace(data_root=tmp_path))
    calls = []

    class Voice:
        name = "fish-test"
        config = SimpleNamespace(speed=1, latency="normal")

        async def synthesize_live(self, queue, speed, on_pcm):
            calls.append("one utterance")
            texts = []
            while (text := await queue.get()) is not None:
                texts.append(text)
            pcm = b"\x01\x00" * 320
            await on_pcm(pcm, 32000, 1)
            if fail_middle:
                raise RuntimeError("connection dropped after audible PCM")
            await on_pcm(pcm, 32000, 1)
            return [pcm, pcm], 32000, "test-live"

    text = "这是一次连续语音测试，我们保持音色一致并把话自然说完。" * 5

    class LLM:
        async def stream_chat(self, _messages):
            for at in range(0, len(text), 7):
                yield text[at : at + 7]

    app = SimpleNamespace(state=SimpleNamespace(voice_client=Voice()))
    payload = SimpleNamespace(
        message="陪我聊聊",
        character_id="assistant",
        expression="natural",
        speed_factor=1,
        voice_enabled=True,
    )

    async def run():
        events = []
        async for event in continuous_chat_events(
            app, LLM(), [], payload, "u", "voice-conversation", "a", factory, lambda e, d: (e, d)
        ):
            events.append(event)
        return events

    events = asyncio.run(run())
    names = [name for name, _ in events]
    assert calls == ["one utterance"]
    assert "".join(data["text"] for name, data in events if name == "text_delta") == text
    assert names[-1] == "done" and names.index("audio_end") > max(
        i for i, name in enumerate(names) if name == "audio_pcm"
    )
    frames = [data for name, data in events if name == "audio_pcm"]
    assert [row["sequence"] for row in frames] == ([0] if fail_middle else [0, 1])
    assert len({row["session_id"] for row in frames}) == 1
    assert ("voice_error" in names) == fail_middle
    assert ("voice_saved" in names) != fail_middle
    with factory() as db:
        assert db.get(Message, "a").content == text
    engine.dispose()
