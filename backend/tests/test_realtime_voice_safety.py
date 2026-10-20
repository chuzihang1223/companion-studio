"""Cross-chunk privacy and actual cancellation acknowledgement, no cloud/audio device."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.database.session import SessionLocal
from app.services.realtime_voice import cache
from app.services.realtime_voice.tts import ConfiguredTTS
from app.services.voice.canonical_text import CanonicalResponseText


def test_every_token_boundary_filters_inline_fences_json_urls_and_credentials():
    text = (
        '说明开始。`inline_do_not_speak`继续。``double_do_not_speak``以及["array_do_not_speak"]。```python\nblocked_code()\n```还有。{"private":"json_do_not_speak"}网址 https://example.invalid/'
        + "url_do_not_speak/" * 30
        + ' 结束。password: "'
        + "secret_do_not_speak " * 30
        + '" 正常结束。'
    )
    for boundary in range(len(text) + 1):
        canonical = CanonicalResponseText()
        spoken = "".join(canonical.feed(part)[1] for part in (text[:boundary], text[boundary:]))
        spoken += canonical.feed("", final=True)[1]
        assert canonical.text == text
        assert "do_not_speak" not in spoken
        assert "blocked_code" not in spoken
        assert "说明开始" in spoken and "正常结束" in spoken


def test_cancelled_local_tts_waits_for_its_owned_thread_and_emits_no_late_pcm():
    entered, finished = threading.Event(), threading.Event()

    class Local:
        name = "local-test"
        config = SimpleNamespace(speed=1)

        def synthesize(self, text, control, cancel_event):
            entered.set()
            try:
                if not cancel_event.wait(2):
                    raise AssertionError("Worker never received cancellation")
                raise RuntimeError("synthetic worker acknowledged cancellation")
            finally:
                finished.set()

    async def run():
        provider, queue, emitted = ConfiguredTTS(Local()), asyncio.Queue(), []
        queue.put_nowait("Synthetic speech.")
        queue.put_nowait(None)

        async def pcm(*args):
            emitted.append(args)

        task = asyncio.create_task(provider.synthesize_live(queue, 1, pcm))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set() and not emitted
        await provider.close()

    asyncio.run(run())


def test_orphan_cache_counts_toward_quota_and_is_not_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(cache, "LIMIT", 20)
    directory = tmp_path / "voice_cache" / "realtime"
    directory.mkdir(parents=True)
    unknown = directory / "unowned.bin"
    unknown.write_bytes(b"0" * 20)
    with SessionLocal() as db, pytest.raises(ValueError, match="VOICE_CACHE_LIMIT"):
        cache.reserve(db, 1, "new-message")
    assert unknown.read_bytes() == b"0" * 20
