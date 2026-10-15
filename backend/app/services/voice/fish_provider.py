"""Fish Audio TTS adapter. Credentials remain in the backend process."""

import base64
from dataclasses import dataclass, replace
from functools import wraps
from io import BytesIO
import json
import os
from pathlib import Path
import re
import threading
import time
import wave
from typing import Callable, Iterable

import httpx

from .fish_credentials import FishCredentialService
from .fish_style_builder import FishStyleBuilder
from .pcm_stream import PcmStreamDecoder

_test_budget_lock = threading.Lock()


def _track_health(method):
    """Record recent real requests without polling or exposing request details."""

    @wraps(method)
    def run(self, *args, **kwargs):
        try:
            result = method(self, *args, **kwargs)
        except Exception:
            self.last_error = "语音服务最近一次请求失败"
            raise
        self.last_error = ""
        return result

    return run


def _record_test_request(transport: str) -> None:
    """Optional isolated verification cap, including transport fallback requests."""
    target = os.environ.get("COMPANION_TEST_FISH_COUNTER")
    if not target:
        return
    with _test_budget_lock:
        file = Path(target)
        count = int(file.read_text("utf-8")) if file.exists() else 0
        if count >= int(os.environ.get("COMPANION_TEST_FISH_LIMIT", "10")):
            raise FishAudioError(429, "本轮 Fish 验证次数已达到授权上限")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(str(count + 1), encoding="utf-8")
        from app.core.file_logging import file_logger

        file_logger("app.voice.test_budget", "voice-sync.log").info(
            "test_request=%s transport=%s", count + 1, transport
        )


@dataclass(frozen=True)
class FishConfig:
    api_key: str = ""
    reference_id: str = ""
    model: str = "s2.1-pro-free"
    speed: float = 1.0
    fallback_enabled: bool = True
    transport_mode: str = "auto"
    latency: str = "balanced"

    def public(self) -> dict:
        return {
            "has_key": bool(self.api_key),
            "reference_id": self.reference_id,
            "model": self.model,
            "speed": self.speed,
            "fallback_enabled": self.fallback_enabled,
            "transport_mode": self.transport_mode,
            "latency": self.latency,
        }


class FishAudioError(RuntimeError):
    def __init__(self, status_code: int, message: str, reason: str = ""):
        self.status_code = status_code
        self.reason = reason
        super().__init__(message)


class FishAudioProvider:
    name = "fish-s2.1-pro-free"

    def __init__(
        self,
        config: FishConfig | None = None,
        transport: httpx.BaseTransport | None = None,
        websocket_stream: Callable[[str, float], Iterable[bytes]] | None = None,
    ):
        initial = config or FishConfig()
        self.credentials = FishCredentialService()
        self.credentials.update(initial.api_key)
        self.config = replace(initial, api_key="")
        self.transport = transport
        self.websocket_stream = websocket_stream
        self.authorized = False
        self.last_error = ""
        self.async_client = None
        self.http_client = httpx.Client(
            timeout=httpx.Timeout(90.0, connect=10.0), transport=transport
        )

    @property
    def ready(self) -> bool:
        return self.credentials.configured() and bool(self.config.reference_id)

    @property
    def available(self) -> bool:
        return self.ready

    @property
    def error(self) -> str:
        return self.last_error if self.ready else "Fish 密钥或声音 ID 尚未配置"

    @property
    def status(self) -> str:
        return ("error" if self.last_error else "online") if self.ready else "unavailable"

    @property
    def ready_info(self) -> dict:
        return {
            "model_loaded": self.ready,
            "speaker_loaded": self.ready,
            "device": "online",
            "warmup_ms": 0,
        }

    def start_background(self) -> None:
        pass

    def stop(self) -> None:
        self.http_client.close()

    async def aclose(self):
        self.stop()
        if self.async_client is not None:
            await self.async_client.aclose()

    async def synthesize_live(self, queue, speed, on_pcm):
        from .fish_live import synthesize_live

        try:
            result = await synthesize_live(self, queue, speed, on_pcm)
            self.last_error = ""
            return result
        except Exception:
            self.last_error = "语音服务最近一次请求失败"
            raise

    def restart(self) -> None:
        pass

    def health(self) -> dict:
        return {
            "ready": self.ready,
            "configured": self.credentials.configured(),
            "model": self.config.model,
            "transport_mode": self.config.transport_mode,
            "reference_configured": bool(self.config.reference_id),
        }

    def supports_streaming(self) -> bool:
        return True

    def supports_style(self) -> bool:
        return True

    def supports_voice_clone(self) -> bool:
        return True

    def configure(self, config: FishConfig) -> None:
        same_identity = (
            self.credentials.get() == config.api_key.strip()
            and self.config.reference_id == config.reference_id
            and self.config.model == config.model
        )
        self.credentials.update(config.api_key)
        self.config = replace(config, api_key="")
        self.name = f"fish-{config.model}"
        self.authorized = self.authorized and same_identity
        if not same_identity:
            self.last_error = ""

    def public(self) -> dict:
        return self.config.public() | {
            "has_key": self.credentials.configured(),
            "authorized": self.authorized,
            "reference_ready": self.authorized,
        }

    def _request(
        self,
        text: str,
        speed: float,
        audio_format: str = "wav",
        temperature: float = 0.7,
        latency: str | None = None,
    ):
        if not self.ready:
            raise FishAudioError(0, "Fish 密钥或声音 ID 尚未配置")
        if not text.strip() or len(text) > 3000:
            raise ValueError("Fish 文本长度无效")
        payload = {
            "text": text,
            "reference_id": self.config.reference_id,
            "format": audio_format,
            "sample_rate": 44100,
            "temperature": temperature,
            "top_p": 0.7,
            "normalize": True,
            "latency": latency or self.config.latency,
            "condition_on_previous_chunks": True,
            "prosody": {
                "speed": max(0.5, min(2.0, speed)),
                "volume": 0,
                "normalize_loudness": True,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.credentials.get()}",
            "Content-Type": "application/json",
            "model": self.config.model,
        }
        return payload, headers

    def stream(
        self,
        text: str,
        speed: float = 1.0,
        audio_format: str = "wav",
        temperature: float = 0.7,
        latency: str | None = None,
    ):
        """Yield response bytes from Fish's documented HTTP streaming endpoint."""
        payload, headers = self._request(text, speed, audio_format, temperature, latency)
        _record_test_request("http")
        if self.http_client.is_closed:
            self.http_client = httpx.Client(
                timeout=httpx.Timeout(90.0, connect=10.0), transport=self.transport
            )
        client = self.http_client
        if client:
            try:
                with client.stream(
                    "POST", "https://api.fish.audio/v1/tts", json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        raw = b""
                        for part in response.iter_bytes():
                            raw += part[: max(0, 2048 - len(raw))]
                            if len(raw) >= 2048:
                                break
                        try:
                            detail = json.loads(raw.decode("utf-8", errors="replace"))
                            reason = str(detail.get("reason") or detail.get("message") or "")
                        except (ValueError, AttributeError):
                            reason = ""
                        reason = re.sub(
                            r"[\x00-\x1f\x7f]",
                            " ",
                            reason.replace(self.credentials.get(), "[redacted]"),
                        )[:160]
                        raise FishAudioError(
                            response.status_code, f"Fish TTS HTTP {response.status_code}", reason
                        )
                    for chunk in response.iter_bytes():
                        if chunk:
                            yield chunk
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise FishAudioError(0, "Fish TTS 网络连接失败或超时") from exc

    @_track_health
    def synthesize(self, text: str, control) -> dict:
        started = time.perf_counter()
        speed = self.config.speed * getattr(control, "speed_factor", 1.0)
        directed_text = self.directed_text(text, control)
        if self.config.transport_mode == "standard":
            content, rate = self._canonical_wav(b"".join(self.stream(directed_text, speed)))
            transport_used = "standard"
        else:
            try:
                content, rate = self._canonical_wav(
                    b"".join(self._stream_websocket(directed_text, speed))
                )
                transport_used = "stream"
            except FishAudioError as exc:
                if exc.status_code in {401, 402, 403, 404, 422, 429}:
                    raise
                # A transport failure may be retried through the proven HTTP path.
                content, rate = self._canonical_wav(b"".join(self.stream(directed_text, speed)))
                transport_used = "standard"
        self.authorized = True
        return {
            "audio_base64": base64.b64encode(content).decode("ascii"),
            "engine": self.name,
            "sample_rate": rate,
            "transport": transport_used,
            "generation_ms": round((time.perf_counter() - started) * 1000),
        }

    @_track_health
    def synthesize_stream(self, text: str, control, on_pcm) -> dict:
        started = time.perf_counter()
        speed = self.config.speed * getattr(control, "speed_factor", 1.0)
        directed = self.directed_text(text, control)
        emitted = False

        def collect(iterator):
            nonlocal emitted
            decoder = PcmStreamDecoder()
            parts = []
            for chunk in iterator:
                parts.append(chunk)
                pcm = decoder.feed(chunk)
                if pcm:
                    emitted = True
                    on_pcm(pcm, decoder.rate, decoder.channels)
            return self._canonical_wav(b"".join(parts))

        transport = "standard"
        if self.config.transport_mode != "standard":
            try:
                content, rate = collect(self._stream_websocket(directed, speed))
                transport = "stream"
            except FishAudioError as exc:
                # Never replay a sentence through a second transport after audio began.
                if emitted or exc.status_code in {401, 402, 403, 404, 422, 429}:
                    raise
                content, rate = collect(self.stream(directed, speed))
        else:
            content, rate = collect(self.stream(directed, speed))
        self.authorized = True
        return {
            "audio_base64": base64.b64encode(content).decode("ascii"),
            "engine": self.name,
            "sample_rate": rate,
            "transport": transport,
            "generation_ms": round((time.perf_counter() - started) * 1000),
        }

    @staticmethod
    def directed_text(text, control):
        return FishAudioProvider.new_direction_session().directed_text(text, control)

    @staticmethod
    def new_direction_session(expression="natural"):
        from .fish_style_builder import FishDirectionSession

        return FishDirectionSession(expression)

    @staticmethod
    def _canonical_wav(content: bytes) -> tuple[bytes, int]:
        try:
            with wave.open(BytesIO(content), "rb") as reader:
                if (
                    reader.getnframes() == 0
                    or reader.getnchannels() != 1
                    or reader.getsampwidth() != 2
                ):
                    raise ValueError("Fish WAV format is not PCM16 mono")
                rate = reader.getframerate()
                # Fish HTTP streaming can use 0xffffffff as a placeholder for
                # the RIFF/data length. Native playback needs the real length.
                pcm = reader.readframes(reader.getnframes())
        except (wave.Error, EOFError, ValueError) as exc:
            raise FishAudioError(0, "Fish 返回的音频格式无效") from exc
        if rate <= 0 or len(pcm) < rate // 20 * 2 or len(pcm) % 2:
            raise FishAudioError(0, "Fish 返回的音频为空或不完整")
        normalized = BytesIO()
        with wave.open(normalized, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(pcm)
        content = normalized.getvalue()
        return content, rate

    def _stream_websocket(self, text: str, speed: float) -> Iterable[bytes]:
        if self.websocket_stream is not None:
            yield from self.websocket_stream(text, speed)
            return
        try:
            from fishaudio import FishAudio
            from fishaudio.core import WebSocketOptions
            from fishaudio.types import Prosody, TTSConfig
            from fishaudio.exceptions import WebSocketError
            from httpx_ws import WebSocketUpgradeError, HTTPXWSException

            config = TTSConfig(
                format="wav",
                sample_rate=44100,
                latency=self.config.latency,
                prosody=Prosody(speed=max(0.5, min(2.0, speed)), volume=0),
                normalize=True,
                temperature=0.7,
                top_p=0.7,
                condition_on_previous_chunks=True,
            )
            _record_test_request("websocket")
            with FishAudio(api_key=self.credentials.get(), timeout=90.0) as client:
                # The complete phrase is one text event; the SDK handles MessagePack framing.
                yield from client.tts.stream_websocket(
                    iter((text,)),
                    reference_id=self.config.reference_id,
                    model=self.config.model,
                    config=config,
                    ws_options=WebSocketOptions(keepalive_ping_timeout_seconds=60.0),
                )
        except WebSocketUpgradeError as exc:
            raise FishAudioError(
                exc.response.status_code, f"Fish WebSocket HTTP {exc.response.status_code}"
            ) from exc
        except WebSocketError as exc:
            raise FishAudioError(0, "Fish WebSocket 流式传输中断") from exc
        except HTTPXWSException as exc:
            raise FishAudioError(0, "Fish WebSocket 连接中断") from exc
        except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
            raise FishAudioError(0, "Fish WebSocket 连接失败或超时") from exc
        except Exception as exc:
            # SDK protocol failures are technical. Do not put exception text in logs:
            # it could contain request details from third-party code.
            raise FishAudioError(0, "Fish WebSocket 流式传输失败") from exc

    @_track_health
    def generate_mp3(
        self, text: str, temperature: float = 0.7, latency: str | None = None
    ) -> tuple[bytes, int, int]:
        """One ordinary REST request for explicit user-triggered listening tests."""
        started = time.perf_counter()
        chunks = []
        first_ms = None
        for chunk in self.stream(text, self.config.speed, "mp3", temperature, latency):
            if first_ms is None:
                first_ms = round((time.perf_counter() - started) * 1000)
            chunks.append(chunk)
        content = b"".join(chunks)
        if len(content) < 512 or not (
            content.startswith(b"ID3")
            or (len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0)
        ):
            raise FishAudioError(0, "Fish 没有返回有效 MP3 音频")
        return content, first_ms or 0, round((time.perf_counter() - started) * 1000)

    def test_mp3(self) -> tuple[bytes, int, int]:
        return self.generate_mp3("你好，这是一次声音测试。")

    @_track_health
    def generate_wav(
        self, text: str, temperature: float = 0.7, latency: str | None = None
    ) -> tuple[bytes, int, int]:
        """Request native PCM WAV; no external audio decoder is needed."""
        started = time.perf_counter()
        chunks = []
        first_ms = None
        for chunk in self.stream(text, self.config.speed, "wav", temperature, latency):
            if first_ms is None:
                first_ms = round((time.perf_counter() - started) * 1000)
            chunks.append(chunk)
        content, _ = self._canonical_wav(b"".join(chunks))
        return content, first_ms or 0, round((time.perf_counter() - started) * 1000)

    def test_wav(self) -> tuple[bytes, int, int]:
        return self.generate_wav("你好，这是一次声音测试。")
