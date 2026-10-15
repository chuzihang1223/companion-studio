import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import httpx
import ormsgpack
import pytest
from app.services.voice import fish_live, fish_provider
from app.services.voice.fish_provider import FishAudioError
from app.services.voice.continuous_pipeline import RealtimeVoicePipeline


def provider(client):
    return SimpleNamespace(
        ready=True,
        authorized=False,
        async_client=client,
        credentials=SimpleNamespace(get=lambda: "test-credential"),
        config=SimpleNamespace(
            model="s2.1-pro-free",
            reference_id="test-reference",
            latency="normal",
            transport_mode="auto",
        ),
    )


def test_live_protocol_sends_incremental_text_and_one_flush(monkeypatch):
    async def run():
        sent, received = [], []
        stopped = asyncio.Event()

        class WS:
            async def send_bytes(self, raw):
                event = ormsgpack.unpackb(raw)
                sent.append(event)
                if event["event"] == "stop":
                    stopped.set()

        @asynccontextmanager
        async def connect(*_args, **_kwargs):
            yield WS()

        async def audio(_ws):
            await stopped.wait()
            yield b"\x01\x00" * 30
            yield b"\x02\x00" * 30

        monkeypatch.setattr(fish_live, "aconnect_ws", connect)
        monkeypatch.setattr(fish_live, "aiter_websocket_audio", audio)
        requests = []
        monkeypatch.setattr(fish_provider, "_record_test_request", requests.append)
        async with httpx.AsyncClient() as client:
            queue = asyncio.Queue()
            queue.put_nowait("你好呀。")
            task = asyncio.create_task(
                fish_live.synthesize_live(
                    provider(client), queue, 1, lambda raw, *_args: collect(received, raw)
                )
            )
            await asyncio.sleep(0.02)
            assert any(row["event"] == "text" for row in sent)
            assert not stopped.is_set()
            queue.put_nowait("今天也见到你了。")
            queue.put_nowait(None)
            parts, rate, transport = await asyncio.wait_for(task, 1)
        assert [row["event"] for row in sent] == ["start", "text", "flush", "text", "stop"]
        assert (
            "".join(row["text"] for row in sent if row["event"] == "text")
            == "你好呀。今天也见到你了。"
        )
        assert sent[0]["request"]["format"] == "pcm"
        assert rate == 44100 and transport == "websocket-live"
        assert received == parts and requests == ["websocket-live"]

    asyncio.run(run())


async def collect(received, raw):
    received.append(raw)


def test_partial_network_failure_never_reissues_or_replays(monkeypatch):
    async def run():
        @asynccontextmanager
        async def connect(*_args, **_kwargs):
            yield SimpleNamespace(send_bytes=lambda _raw: asyncio.sleep(0))

        async def audio(_ws):
            yield b"\x01\x00" * 30
            raise OSError("disconnected")

        monkeypatch.setattr(fish_live, "aconnect_ws", connect)
        monkeypatch.setattr(fish_live, "aiter_websocket_audio", audio)
        requests = []
        monkeypatch.setattr(fish_provider, "_record_test_request", requests.append)
        queue = asyncio.Queue()
        queue.put_nowait("第一句。")
        queue.put_nowait(None)
        received = []
        async with httpx.AsyncClient() as client:
            with pytest.raises(FishAudioError):
                await fish_live.synthesize_live(
                    provider(client), queue, 1, lambda raw, *_: collect(received, raw)
                )
        assert requests == ["websocket-live"] and len(received) == 1

    asyncio.run(run())


def test_cancel_closes_socket_and_waiting_sender(monkeypatch):
    async def run():
        closed = []

        @asynccontextmanager
        async def connect(*_args, **_kwargs):
            try:
                yield SimpleNamespace(send_bytes=lambda _raw: asyncio.sleep(0))
            finally:
                closed.append(True)

        async def audio(_ws):
            await asyncio.Event().wait()
            yield b""

        monkeypatch.setattr(fish_live, "aconnect_ws", connect)
        monkeypatch.setattr(fish_live, "aiter_websocket_audio", audio)
        monkeypatch.setattr(fish_provider, "_record_test_request", lambda _: None)
        async with httpx.AsyncClient() as client:
            task = asyncio.create_task(
                fish_live.synthesize_live(
                    provider(client), asyncio.Queue(), 1, lambda raw, *_: asyncio.sleep(0)
                )
            )
            await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.5)
        assert closed == [True]
        assert not [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
        ]

    asyncio.run(run())


def test_disconnect_with_full_text_queue_cannot_hang():
    async def run():
        class Voice:
            name = "fish-test"
            config = SimpleNamespace(speed=1, latency="normal")

            async def synthesize_live(self, *_args):
                await asyncio.Event().wait()

        app = SimpleNamespace(state=SimpleNamespace(voice_client=Voice()))
        payload = SimpleNamespace(
            voice_enabled=True, message="测试", expression="natural", speed_factor=1
        )

        async def deltas():
            for _ in range(200):
                yield "这一句话会填满尚未消费的语音队列。" * 10

        async def complete(_text):
            return {}

        pipeline = RealtimeVoicePipeline(app, payload, "cancel-test", None)
        events = pipeline.events(deltas(), [], complete)
        for _ in range(8):
            await events.__anext__()
        await asyncio.sleep(0.03)
        await asyncio.wait_for(events.aclose(), 0.5)
        assert app.state.live_voice_sessions == {}
        assert not [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
        ]

    asyncio.run(run())
