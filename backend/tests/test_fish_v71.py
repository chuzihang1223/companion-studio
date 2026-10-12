"""Offline contract checks for Fish request headers and safe fallback."""

import base64
import asyncio
from io import BytesIO
import json
from types import SimpleNamespace
import wave

import httpx
import pytest

from app.services.voice.fish_provider import FishAudioError, FishAudioProvider, FishConfig
from app.services.voice.router import VoiceRouter
from app.api import fish_settings


def _wav() -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\x00\x00" * 4000)
    return output.getvalue()


def test_recent_fish_failure_degrades_presence_and_success_recovers():
    from app.api.presence import presence
    failing = True
    def reply(_request):
        return httpx.Response(503) if failing else httpx.Response(200, content=_wav())
    fish = FishAudioProvider(FishConfig(api_key='isolated-test-key', reference_id='test-voice',
                                      transport_mode='standard'), httpx.MockTransport(reply))
    state = SimpleNamespace(llm_service=SimpleNamespace(config=SimpleNamespace(api_key='mock', model='mock')),
                            voice_client=fish, task_runtime=object())
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    with pytest.raises(FishAudioError):
        fish.synthesize('你好。', None)
    status = presence(request)
    assert status['backend'] and status['llm']['available'] and status['agent']
    assert not status['voice']['available']
    failing = False
    fish.synthesize('你好。', None)
    assert presence(request)['voice']['available']


def test_free_model_and_reference_are_sent_in_the_right_places():
    captured = []

    def respond(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, content=_wav())

    provider = FishAudioProvider(FishConfig(api_key="  test-key\n", reference_id="voice_123", transport_mode="standard"),
                                 httpx.MockTransport(respond))
    audio = provider.synthesize("你好呀。", None)
    assert provider.authorized is True
    request = captured[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert str(request.url) == "https://api.fish.audio/v1/tts"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["model"] == "s2.1-pro-free"
    assert body["reference_id"] == "voice_123"
    assert "model" not in body
    assert body["format"] == "wav"
    assert base64.b64decode(audio["audio_base64"]) == _wav()
    assert "test-key" not in json.dumps(provider.public())


def test_streamed_wav_placeholder_length_is_replaced_with_actual_audio_length():
    streamed = bytearray(_wav())
    streamed[4:8] = b"\xff" * 4
    streamed[40:44] = b"\xff" * 4
    provider = FishAudioProvider(
        FishConfig(api_key="key", reference_id="voice_123", transport_mode="standard"),
        httpx.MockTransport(lambda _: httpx.Response(200, content=bytes(streamed))),
    )
    result = provider.synthesize("你好呀。", None)
    normalized = base64.b64decode(result["audio_base64"])
    with wave.open(BytesIO(normalized), "rb") as reader:
        assert reader.getnframes() == 4000
        assert reader.getframerate() == 24000
    assert len(normalized) == len(_wav())


def test_websocket_transport_and_technical_fallback():
    seen = []
    def websocket(text, speed):
        seen.append((text, speed))
        yield _wav()
    provider = FishAudioProvider(FishConfig(api_key="key", reference_id="voice_123"),
                                 websocket_stream=websocket)
    assert base64.b64decode(provider.synthesize("你好。", None)["audio_base64"]) == _wav()
    assert seen == [("你好。", 1.0)]

    calls = []
    def broken_websocket(text, speed):
        raise FishAudioError(0, "transport failed")
        yield b""
    provider = FishAudioProvider(FishConfig(api_key="key", reference_id="voice_123"),
                                 httpx.MockTransport(lambda req: (calls.append(req), httpx.Response(200, content=_wav()))[1]),
                                 websocket_stream=broken_websocket)
    assert base64.b64decode(provider.synthesize("你好。", None)["audio_base64"]) == _wav()
    assert len(calls) == 1
    assert calls[0].headers["model"] == "s2.1-pro-free"


def test_official_sdk_websocket_receives_explicit_free_model_and_voice(monkeypatch):
    import fishaudio
    captured = {}

    class FakeTTS:
        def stream_websocket(self, text_stream, **options):
            captured.update(options)
            captured["text"] = list(text_stream)
            yield _wav()

    class FakeClient:
        def __init__(self, *, api_key, timeout):
            assert api_key == "key" and timeout == 90.0
            self.tts = FakeTTS()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(fishaudio, "FishAudio", FakeClient)
    provider = FishAudioProvider(FishConfig(api_key="key", reference_id="voice_123", latency="normal"))
    result = provider.synthesize("你好。", None)
    assert result["transport"] == "stream"
    assert captured["model"] == "s2.1-pro-free"
    assert captured["reference_id"] == "voice_123"
    assert captured["config"].format == "wav"
    assert captured["config"].latency == "normal"
    assert captured["text"] == ["你好。"]


def test_websocket_auth_failure_does_not_retry_http():
    def unauthorized(text, speed):
        raise FishAudioError(401, "unauthorized")
        yield b""
    provider = FishAudioProvider(FishConfig(api_key="wrong", reference_id="voice_123"),
                                 httpx.MockTransport(lambda _: pytest.fail("HTTP retry must not happen")),
                                 websocket_stream=unauthorized)
    with pytest.raises(FishAudioError) as failure:
        provider.synthesize("你好。", None)
    assert failure.value.status_code == 401


def test_invalid_websocket_audio_uses_http():
    calls = []
    provider = FishAudioProvider(
        FishConfig(api_key="key", reference_id="voice_123"),
        httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(200, content=_wav()))[1]),
        websocket_stream=lambda _text, _speed: iter((b"not-a-wav",)),
    )
    assert base64.b64decode(provider.synthesize("你好。", None)["audio_base64"]) == _wav()
    assert len(calls) == 1


def test_authorization_failures_never_use_local_fallback():
    provider = FishAudioProvider(FishConfig(api_key="wrong", reference_id="voice_123"),
                                 httpx.MockTransport(lambda _: httpx.Response(401)))
    router = VoiceRouter(object(), provider)
    router.selected_engine = "fish"
    with pytest.raises(FishAudioError) as failure:
        provider.test_wav()
    assert failure.value.status_code == 401
    assert router.fallback(provider, failure.value) is None
    assert router.fallback(provider, FishAudioError(404, "reference unavailable")) is None


def test_temporary_service_failure_can_use_local_fallback():
    local = object()
    provider = FishAudioProvider(FishConfig(api_key="key", reference_id="voice_123"))
    router = VoiceRouter(local, provider)
    assert router.fallback(provider, FishAudioError(503, "temporarily down")) is local
    assert router.fallback(provider, FishAudioError(0, "connection timeout")) is local


def test_minimal_authorization_wav_uses_free_header():
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_wav())

    provider = FishAudioProvider(FishConfig(api_key="key", reference_id="voice_123"),
                                 httpx.MockTransport(respond))
    content, _, _ = provider.test_wav()
    assert content == _wav()
    assert seen[0].headers["model"] == "s2.1-pro-free"
    body = json.loads(seen[0].content)
    assert body["text"] == "你好，这是一次声音测试。"
    assert body["format"] == "wav"


def test_account_reason_does_not_echo_the_key():
    response = httpx.MockTransport(lambda _: httpx.Response(402, json={"reason": "Plan denied for secret-key"}))
    provider = FishAudioProvider(FishConfig(api_key="secret-key", reference_id="voice_123"), response)
    with pytest.raises(FishAudioError) as failure:
        provider.test_wav()
    assert failure.value.status_code == 402
    assert "secret-key" not in failure.value.reason
    assert "[redacted]" in failure.value.reason


def test_character_samples_resume_after_a_transient_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(fish_settings, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(fish_settings, "require_control", lambda _: None)

    class FakeFish:
        config = FishConfig(reference_id="voice_123")
        authorized = True
        calls: list[str] = []
        failed_once = False

        def generate_wav(self, line):
            self.calls.append(line)
            if line == fish_settings.CHARACTER_LINES[1] and not self.failed_once:
                self.failed_once = True
                raise FishAudioError(0, "timeout")
            return _wav(), 100, 200

    fish = FakeFish()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(fish_voice=fish)))
    first = asyncio.run(fish_settings.character_test(request, "test-token"))
    assert first["complete"] is False
    assert first["failed_index"] == 2
    assert len(first["samples"]) == 1

    resumed = asyncio.run(fish_settings.character_test(request, "test-token"))
    assert resumed["complete"] is True
    assert len(resumed["samples"]) == 5
    assert resumed["samples"][0]["cached"] is True
    assert fish.calls.count(fish_settings.CHARACTER_LINES[0]) == 1


def test_free_model_comparison_resumes_without_repeating_saved_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(fish_settings, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(fish_settings, "require_control", lambda _: None)

    class FakeFish:
        config = FishConfig(reference_id="voice_123")
        authorized = True
        calls = []
        failed_once = False

        def generate_wav(self, text, temperature, latency):
            self.calls.append((text, temperature, latency))
            if temperature == 0.7 and latency == "balanced" and not self.failed_once:
                self.failed_once = True
                raise FishAudioError(0, "timeout")
            return _wav(), 100, 200

    fish = FakeFish()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(fish_voice=fish)))
    first = asyncio.run(fish_settings.compare_fish(request, "test-token"))
    assert first["complete"] is False and len(first["samples"]) == 1
    resumed = asyncio.run(fish_settings.compare_fish(request, "test-token"))
    assert resumed["complete"] is True and len(resumed["samples"]) == 4
    assert fish.calls.count((fish_settings.COMPARE_TEXT, 0.5, "balanced")) == 1
    assert resumed["samples"][0]["cached"] is True


def test_stream_diagnostic_reports_actual_transport_and_writes_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(fish_settings, "settings", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(fish_settings, "require_control", lambda _: None)

    class FakeFish:
        config = FishConfig(reference_id="voice_123")

        def synthesize(self, text, control):
            assert text and control is None
            return {"audio_base64": base64.b64encode(_wav()).decode(),
                    "transport": "standard", "generation_ms": 123}

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(fish_voice=FakeFish())))
    result = asyncio.run(fish_settings.stream_test(request, "test-token"))
    assert result["transport"] == "standard"
    assert (tmp_path / "voice_test" / "fish_stream_test.wav").read_bytes() == _wav()
