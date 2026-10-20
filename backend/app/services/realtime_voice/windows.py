"""Local Windows speech: batch dictation and cancellable chunked system TTS.

JSON travels over owned stdin/stdout pipes. No shell interpolation, credentials,
network service or microphone access is needed by this adapter.
"""

import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import shutil
import tempfile
from types import SimpleNamespace

from app.core.config import settings
from app.services.evaluation.environment import OwnedJob
from .audio import wav_bytes


class WindowsSpeech:
    name = "windows-sapi"

    def __init__(self, *, language="zh-CN", voice_id="", speed=1.0):
        self.language, self.voice_id = language, voice_id
        self.config = SimpleNamespace(speed=speed, latency="balanced")
        self.children = set()

    def capabilities(self):
        return {
            "provider_id": self.name,
            "version": "System.Speech (.NET Framework installed by Windows)",
            "native_streaming_asr": False,
            "native_streaming_tts": False,
            "chunked_synthesis": True,
            "supports_emotion": False,
            "supports_voice_reference": False,
            "supports_local": True,
            "requires_network": False,
            "requires_paid_api": False,
            "supports_cancellation": True,
            "output_sample_rate": 16000,
            "output_format": "pcm_s16le_mono",
        }

    async def request(self, action: str, **values):
        if sys.platform != "win32":
            raise RuntimeError("ASR_PROVIDER_UNAVAILABLE")
        native_cache = settings.data_root.parent / "Cache" / "realtime-voice-native"
        if not native_cache.resolve().is_relative_to(settings.data_root.parent.resolve()):
            raise RuntimeError("VOICE_TEMP_SCOPE_INVALID")
        native_cache.mkdir(parents=True, exist_ok=True)
        if native_cache.is_symlink() or native_cache.is_junction():
            raise RuntimeError("VOICE_TEMP_SCOPE_INVALID")
        native_root = native_cache.resolve()
        if not native_root.is_relative_to(settings.data_root.parent.resolve()):
            raise RuntimeError("VOICE_TEMP_SCOPE_INVALID")
        temporary = Path(tempfile.mkdtemp(prefix="speech-", dir=native_root))
        env = {
            key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC") if key in os.environ
        }
        env.update(TEMP=str(temporary), TMP=str(temporary))
        executable = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / (
            "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        process = job = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(executable),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(Path(__file__).with_name("windows-speech.ps1")),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                creationflags=0x08000000,
            )
            self.children.add(process)
            # Attach before writing stdin, so no synthesis/compilation can begin
            # before kill-on-close ownership includes the native helper's children.
            job = OwnedJob(process.pid, memory_bytes=512 * 1024 * 1024)
            stdout, _ = await asyncio.wait_for(
                process.communicate(
                    json.dumps({"action": action, **values}, ensure_ascii=False).encode()
                ),
                timeout=25,
            )
            if process.returncode or len(stdout) > 2_000_000:
                raise RuntimeError("WINDOWS_SPEECH_UNAVAILABLE")
            value = json.loads(stdout.decode("utf-8-sig"))
            if not isinstance(value, dict) or "error" in value:
                raise RuntimeError("WINDOWS_SPEECH_UNAVAILABLE")
            return value
        finally:
            if job:
                job.close()
            if process:
                if process.returncode is None:
                    process.kill()
                await process.wait()
                self.children.discard(process)
            # Only this newly created compiler workspace, after its owned Job
            # and process end. Never sweep a shared TEMP or user's voice assets.
            if (
                temporary.resolve().parent == native_root
                and temporary.name.startswith("speech-")
                and not temporary.is_symlink()
                and not temporary.is_junction()
            ):
                shutil.rmtree(temporary)

    async def initialize(self):
        return await self.request("probe")

    async def recognize(self, wav: bytes):
        value = await self.request(
            "recognize", language=self.language, audio_base64=base64.b64encode(wav).decode()
        )
        text = value.get("text", "")
        if not isinstance(text, str) or len(text) > 4000:
            raise ValueError("ASR_RESULT_INVALID")
        return text.strip()

    async def synthesize(self, text: str):
        if not isinstance(text, str) or not 1 <= len(text) <= 240:
            raise ValueError("TTS_TEXT_INVALID")
        result = await self.request(
            "synthesize",
            language=self.language,
            voice=self.voice_id,
            rate=round((self.config.speed - 1) * 10),
            text=text,
        )
        raw = base64.b64decode(result["pcm_base64"], validate=True)
        if not raw or len(raw) % 2 or len(raw) > 1_000_000:
            raise ValueError("AUDIO_FORMAT_INVALID")
        return raw

    async def synthesize_live(self, queue, speed, on_pcm):
        # Chunked synthesis, not a native streaming engine. Sequential requests
        # preserve the voice snapshot/order and naturally bound in-flight work.
        parts, size = [], 0
        while (text := await queue.get()) is not None:
            raw = await self.synthesize(text)
            size += len(raw)
            if size > 16_000_000:
                raise ValueError("AUDIO_BUFFER_LIMIT")
            parts.append(raw)
            for offset in range(0, len(raw), 3200):
                await on_pcm(raw[offset : offset + 3200], 16000, 1)
        return parts, 16000, "local-chunked"

    async def cancel(self):
        for process in tuple(self.children):
            if process.returncode is None:
                process.kill()
        await asyncio.gather(*(p.wait() for p in tuple(self.children)))

    async def close(self):
        await self.cancel()
