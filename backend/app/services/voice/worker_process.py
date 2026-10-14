"""Persistent local voice worker, run with its engine's isolated Python."""

import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave
import traceback
from threading import Thread


def startup_stage(stage: str) -> None:
    if os.environ.get("VOICE_DEBUG_STARTUP") == "1":
        print(f"VOICE_STARTUP_STAGE {stage} {time.monotonic():.3f}", file=sys.stderr, flush=True)


startup_stage("before_numpy")
import numpy as np

startup_stage("after_numpy")

from app.services.voice.prosody_planner import EngineControl

startup_stage("after_project_imports")


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(samples, -0.98, 0.98) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def stop_when_backend_exits() -> None:
    """End the worker even if its owner is killed during a long synthesis."""
    if sys.platform != "win32":
        return
    parent_pid = int(os.environ.get("VOICE_BACKEND_PID", "0"))
    if parent_pid <= 0:
        return

    def watch() -> None:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000, False, parent_pid)
        if not handle:
            return
        try:
            if kernel32.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
                alias = os.environ.get("VOICE_ALIAS_DRIVE", "")
                if alias:
                    # The frozen owner can be terminated before its finally
                    # block runs. Release the virtual path before we exit.
                    try:
                        os.chdir(Path(__file__).anchor)
                        subprocess.run(["subst", alias, "/D"], capture_output=True, timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                os._exit(0)
        finally:
            kernel32.CloseHandle(handle)

    Thread(target=watch, daemon=True).start()


def main() -> None:
    startup_stage("main_begin")
    stop_when_backend_exits()
    model_dir, references_dir = sys.argv[1:3]
    engine_name = sys.argv[3] if len(sys.argv) > 3 else "onnx"
    if engine_name == "index":
        startup_stage("before_index_provider_import")
        from app.services.voice.index_provider import IndexTTSVoiceProvider

        startup_stage("after_index_provider_import")

        provider = IndexTTSVoiceProvider(model_dir, references_dir)
    else:
        from app.services.voice.onnx_provider import ONNXVoiceProvider

        provider = ONNXVoiceProvider(model_dir, references_dir)
    try:
        send({"type": "status", "status": "loading_model"})
        startup_stage("before_model_warm")
        with redirect_stdout(sys.stderr):
            provider.warm()
        startup_stage("after_model_warm")
        warmup_ms = 0
        if engine_name == "index":
            send({"type": "status", "status": "loading_voice_profile"})
            started = time.perf_counter()
            control = EngineControl(1.0, 0, 0, "neutral", "自然", "neutral", 0.3, 1.0, 1.0)
            send({"type": "status", "status": "warming_up"})
            with redirect_stdout(sys.stderr):
                provider.synthesize("嗯。", control)
            startup_stage("after_speech_warm")
            warmup_ms = round((time.perf_counter() - started) * 1000)
        cache = Path(os.environ.get("TEMP", "."))
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / f"voice-health-{os.getpid()}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
        send(
            {
                "type": "ready",
                "engine": provider.name,
                "model_loaded": True,
                "speaker_loaded": True,
                "device": "cuda" if engine_name == "index" else "cpu",
                "warmup_ms": warmup_ms,
            }
        )
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        send({"type": "startup_error", "error": str(exc)})
        raise
    running_rms = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                break
            send({"type": "status", "status": "busy"})
            control = EngineControl(**request["control"])
            started = time.perf_counter()
            with redirect_stdout(sys.stderr):
                audio = provider.synthesize(request["text"], control)
            samples = np.asarray(audio.samples, dtype=np.float32).copy()
            current_rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
            if running_rms and current_rms > 0.005:
                samples *= float(np.clip(running_rms / current_rms, 0.84, 1.19))
            if current_rms > 0.005:
                running_rms = (
                    current_rms if running_rms is None else 0.8 * running_rms + 0.2 * current_rms
                )
            payload = {
                "type": "audio",
                "id": request["id"],
                "engine": provider.name,
                "sample_rate": audio.sample_rate,
                "generation_ms": round((time.perf_counter() - started) * 1000),
                "audio_base64": base64.b64encode(wav_bytes(samples, audio.sample_rate)).decode(
                    "ascii"
                ),
            }
            send(payload)
            send({"type": "status", "status": "ready"})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send(
                {
                    "type": "error",
                    "id": request.get("id") if "request" in locals() else None,
                    "error": str(exc),
                }
            )
            send({"type": "status", "status": "ready"})


if __name__ == "__main__":
    main()
