"""Windows audio fallback when Chromium cannot open an output stream."""

from array import array
from io import BytesIO
from pathlib import Path
import os
import sys
import threading
import time
from uuid import uuid4
import wave

from .mp3_decoder import decode_mp3_to_wav


class NativeAudioPlayer:
    def __init__(self, data_root: Path):
        from .native_pcm import NativePcmOutput
        self.pcm = NativePcmOutput()
        self.stream_owned = False
        self.cache = data_root / "audio" / "native-playback"
        self.lock = threading.RLock()
        self.source: Path | None = None
        self.temporary: Path | None = None
        self.decoded: Path | None = None
        self.started = 0.0
        self.position = 0.0
        self.duration = 0.0
        self.volume = 1.0
        self.state = "stopped"

    def _stop_output(self) -> None:
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(None, 0)
        if self.temporary:
            self.temporary.unlink(missing_ok=True)
            self.temporary = None

    def _render(self, position: float) -> Path:
        assert self.source is not None
        with wave.open(str(self.source), "rb") as reader:
            if reader.getnchannels() not in (1, 2) or reader.getsampwidth() != 2:
                raise ValueError("Native player requires 16-bit PCM WAV")
            rate = reader.getframerate()
            frames = reader.getnframes()
            params = reader.getparams()
            reader.setpos(min(frames - 1, int(position * rate)))
            data = reader.readframes(frames)
        if self.volume >= 0.999 and position == 0:
            return self.source
        samples = array("h")
        samples.frombytes(data)
        if self.volume < 0.999:
            samples = array("h", (round(sample * self.volume) for sample in samples))
        self.cache.mkdir(parents=True, exist_ok=True)
        target = self.cache / f"{uuid4()}.wav"
        with wave.open(str(target), "wb") as writer:
            writer.setparams(params)
            writer.writeframes(samples.tobytes())
        self.temporary = target
        return target

    def play(self, source: Path, volume: float = 1.0) -> dict:
        if sys.platform != "win32":
            raise OSError("Windows native playback is unavailable")
        import ctypes
        import winsound
        if ctypes.windll.winmm.waveOutGetNumDevs() < 1:
            raise OSError("No Windows audio output device")
        with self.lock:
            self.pcm.stop(); self.stream_owned = False
            self._stop_output()
            if self.decoded:
                self.decoded.unlink(missing_ok=True)
                self.decoded = None
            if source.suffix.lower() == ".mp3":
                self.cache.mkdir(parents=True, exist_ok=True)
                decoded = self.cache / f"{uuid4()}.wav"
                decode_mp3_to_wav(source, decoded)
                self.decoded = decoded
                source = decoded
            with wave.open(str(source), "rb") as reader:
                if reader.getnchannels() not in (1, 2) or reader.getsampwidth() != 2 or reader.getframerate() <= 0:
                    raise ValueError("Unsupported WAV format")
                duration = reader.getnframes() / reader.getframerate()
            if duration < 0.05:
                raise ValueError("WAV is empty")
            self.source = source
            self.duration = duration
            self.position = 0
            self.volume = max(0, min(1, volume))
            output = self._render(0)
            winsound.PlaySound(str(output), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.started = time.monotonic()
            self.state = "playing"
            return self.status()

    def pause(self) -> dict:
        if self.stream_owned: return self.pcm.pause()
        with self.lock:
            if self.state == "playing":
                self.position = min(self.duration, self.position + time.monotonic() - self.started)
                self._stop_output()
                self.state = "paused"
            return self.status()

    def resume(self) -> dict:
        if self.stream_owned: return self.pcm.resume()
        if sys.platform != "win32":
            raise OSError("Windows native playback is unavailable")
        import winsound
        with self.lock:
            if self.state != "paused" or not self.source:
                return self.status()
            if self.position >= self.duration:
                self.position = 0
            output = self._render(self.position)
            winsound.PlaySound(str(output), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.started = time.monotonic()
            self.state = "playing"
            return self.status()

    def stop(self) -> dict:
        self.pcm.stop(); self.stream_owned = False
        with self.lock:
            self._stop_output()
            if self.decoded:
                self.decoded.unlink(missing_ok=True)
                self.decoded = None
            self.source = None
            self.position = 0
            self.duration = 0
            self.state = "stopped"
            return self.status()

    def status(self) -> dict:
        if self.stream_owned: return self.pcm.status()
        with self.lock:
            current = self.position + (time.monotonic() - self.started if self.state == "playing" else 0)
            if self.state == "playing" and current >= self.duration:
                self._stop_output()
                if self.decoded:
                    self.decoded.unlink(missing_ok=True)
                    self.decoded = None
                self.state = "stopped"
                current = self.duration
            return {"status": self.state, "position": round(min(current, self.duration), 3),
                    "duration": round(self.duration, 3), "output": "Windows audio"}
