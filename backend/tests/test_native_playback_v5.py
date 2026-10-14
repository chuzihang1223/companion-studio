"""原生播放契约回归：读取真实 PCM，只替换 Windows 设备/输出边界。

测试验证送往播放 API 的音频与控制状态，不声称 CI 实际发出了声音。
"""

from array import array
import base64
import ctypes
from io import BytesIO
from pathlib import Path
import sys
import wave
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import voice as voice_api
from app.database.session import Base
from app.models import Conversation, Message
from app.services.voice import message_cache
from app.services.voice import native_playback
from app.services.voice.message_cache import save_message_audio


@pytest.fixture
def windows_audio_boundary(monkeypatch):
    """替换声卡枚举和声音输出，真实播放器/PCM 准备代码仍然运行。

    替换模块本地 sys/time 引用，不修改全局平台或事件循环的单调时钟，
    因而这份硬件边界测试也能在没有 Windows 声卡的环境运行。
    """
    devices = Mock(return_value=1)
    sound = Mock()
    clock = [100.0]
    monkeypatch.setattr(native_playback, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(native_playback, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(winmm=SimpleNamespace(waveOutGetNumDevs=devices)),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "winsound",
        SimpleNamespace(PlaySound=sound, SND_FILENAME=0x20000, SND_ASYNC=1, SND_NODEFAULT=2),
    )
    return SimpleNamespace(devices=devices, sound=sound, clock=clock, flags=0x20003)


@pytest.fixture
def native_audio_api(tmp_path, monkeypatch, windows_audio_boundary):
    """仅加载真实语音路由与播放器，数据库、音频和控制令牌均为合成数据。"""
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "test-control-token")
    monkeypatch.setattr(message_cache, "settings", SimpleNamespace(data_root=tmp_path))
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(voice_api, "SessionLocal", factory)
    samples = array("h", [2000, -1000] * 11025)
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
            writer.writeframes(samples.tobytes())
        save_message_audio(
            db,
            message,
            [(base64.b64encode(output.getvalue()).decode(), 0, 0)],
            "indextts-2.5-expressive",
        )
        message_id = message.id
    application = FastAPI()
    application.include_router(voice_api.router)
    player = native_playback.NativeAudioPlayer(tmp_path)
    application.state.native_player = player
    try:
        with TestClient(application) as client:
            yield SimpleNamespace(
                client=client,
                player=player,
                url=f"/api/voice/audio/{message_id}",
                headers={"X-Companion-Control-Token": "test-control-token"},
                samples=samples,
                hardware=windows_audio_boundary,
            )
    finally:
        player.stop()
        engine.dispose()


def read_pcm(path):
    """读取实际生成的 WAV，断言数据准备而非只检查模拟方法是否被调用。"""
    with wave.open(str(path), "rb") as reader:
        samples = array("h")
        samples.frombytes(reader.readframes(reader.getnframes()))
        return reader.getparams(), samples


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


def test_secured_native_audio_controls_replayable_pcm(native_audio_api):
    api = native_audio_api
    client, headers, hardware = api.client, api.headers, api.hardware
    response = client.get(api.url)
    assert response.headers["content-type"].startswith("audio/wav")
    with wave.open(BytesIO(response.content), "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (
            1,
            2,
            22050,
        )
        assert reader.getnframes() == len(api.samples)
        assert reader.readframes(reader.getnframes()) == api.samples.tobytes()
    assert client.get(f"{api.url}/diagnostic").json()["bit_depth"] == 16
    # 缺失或错误令牌均不能到达设备枚举/声音输出边界，也不能改变控制状态。
    for denied_headers in ({}, {"X-Companion-Control-Token": "wrong-token"}):
        for action in ("play", "pause", "resume", "stop"):
            result = client.post(
                f"/api/voice/native/{action}", headers=denied_headers, json={"audio_url": api.url}
            )
            assert result.status_code == 403
        assert client.get("/api/voice/native/status", headers=denied_headers).status_code == 403
    hardware.devices.assert_not_called()
    hardware.sound.assert_not_called()
    started = client.post(
        "/api/voice/native/play", headers=headers, json={"audio_url": api.url, "volume": 0.5}
    )
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "playing"
    assert started.json()["duration"] == 1.0
    hardware.devices.assert_called_once_with()
    rendered = Path(hardware.sound.call_args.args[0])
    assert hardware.sound.call_args.args[1] == hardware.flags
    params, pcm = read_pcm(rendered)
    assert (params.framerate, params.nchannels, params.sampwidth) == (22050, 1, 2)
    assert pcm == array("h", (round(sample * 0.5) for sample in api.samples))
    hardware.clock[0] += 0.25
    paused = client.post("/api/voice/native/pause", headers=headers).json()
    assert paused["status"] == "paused" and paused["position"] == 0.25
    assert not rendered.exists()
    assert hardware.sound.call_args.args == (None, 0)
    resumed = client.post("/api/voice/native/resume", headers=headers).json()
    assert resumed["status"] == "playing" and resumed["position"] == 0.25
    resumed_file = Path(hardware.sound.call_args.args[0])
    _params, remainder = read_pcm(resumed_file)
    offset = int(0.25 * 22050)
    assert remainder == array("h", (round(sample * 0.5) for sample in api.samples[offset:]))
    status = client.get("/api/voice/native/status", headers=headers).json()
    assert status["status"] == "playing" and status["position"] == 0.25
    stopped = client.post("/api/voice/native/stop", headers=headers).json()
    assert stopped["status"] == "stopped" and stopped["position"] == stopped["duration"] == 0
    assert not resumed_file.exists()
    assert api.player.source is None and api.player.temporary is None


def test_native_audio_without_output_device_returns_503_without_playing(native_audio_api):
    api = native_audio_api
    api.hardware.devices.return_value = 0
    response = api.client.post(
        "/api/voice/native/play", headers=api.headers, json={"audio_url": api.url}
    )
    assert response.status_code == 503
    assert "No Windows audio output device" in response.json()["detail"]
    api.hardware.devices.assert_called_once_with()
    api.hardware.sound.assert_not_called()
    assert api.player.state == "stopped" and api.player.source is None
    assert api.player.temporary is None and not api.player.cache.exists()


@pytest.mark.parametrize("channels", [1, 2])
def test_render_preserves_real_pcm_at_offset_and_does_not_touch_audio_device(
    tmp_path, channels, windows_audio_boundary
):
    # 当前没有 seek API；这里测试真实 _render(position) 的偏移/裁剪准备，
    # 与暂停后恢复共用同一低层函数，不能描述为实际声音播放或新增 seek 能力。
    source = tmp_path / "input.wav"
    samples = array("h", [2400, -1200] * (4000 * channels))
    with wave.open(str(source), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(samples.tobytes())
    player = native_playback.NativeAudioPlayer(tmp_path)
    player.source = source
    assert player._render(0) == source
    player.volume = 0.25
    rendered = player._render(0.5)
    params, pcm = read_pcm(rendered)
    assert (params.framerate, params.nchannels, params.sampwidth, params.nframes) == (
        8000,
        channels,
        2,
        4000,
    )
    assert pcm == array("h", (round(sample * 0.25) for sample in samples[4000 * channels :]))
    assert read_pcm(source)[1] == samples
    windows_audio_boundary.devices.assert_not_called()
    windows_audio_boundary.sound.assert_not_called()
