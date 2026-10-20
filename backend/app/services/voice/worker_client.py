"""Start, reuse, and stop the isolated local TTS subprocess safely."""

from dataclasses import asdict
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
import time
from threading import Event, Lock, Thread
from uuid import uuid4

from app.core.config import settings

from .prosody_planner import EngineControl


logger = logging.getLogger(__name__)


class VoiceWorkerClient:
    def __init__(
        self,
        model_dir: Path | None = None,
        references_dir: Path | None = None,
        engine: str = "onnx",
    ):
        self.engine = engine
        self.name = "indextts-2.5-expressive" if engine == "index" else "onnx-tts"
        default_model = settings.data_root / "voice_models" / engine
        default_refs = settings.data_root / "voice_references"
        model_env = "VOICE_INDEX_MODEL_DIR" if engine == "index" else "VOICE_MODEL_DIR"
        self.model_dir = Path(model_dir or os.getenv(model_env) or default_model)
        self.references_dir = Path(
            references_dir or os.getenv("VOICE_REFERENCES_DIR") or default_refs
        )
        self.python = (
            (self.model_dir.parent if engine == "index" else self.model_dir)
            / ".venv"
            / "Scripts"
            / "python.exe"
        )
        self.process: subprocess.Popen | None = None
        self._ready = Event()
        self._responses: Queue[dict] = Queue()
        self._request_lock = Lock()
        self._lifecycle_lock = Lock()
        self.error = ""
        self.ready_info: dict = {}
        self.status = "starting"
        self._stopping = False
        self._alias: str | None = None
        self._alias_root: Path | None = None
        if os.getenv("COMPANION_CLEAN_DISTRIBUTION") == "1" and not self.available:
            self.status = "unconfigured"

    def _ascii_paths(self) -> tuple[Path, Path, Path, Path]:
        """Give Windows native tokenizers an ASCII drive alias; the files stay on their original volume."""
        model, refs, python = self.model_dir, self.references_dir, self.python
        source = self.model_dir.parent if self.engine == "index" else self.model_dir
        if (
            self.engine != "index"
            or sys.platform != "win32"
            or all(ord(ch) < 128 for ch in str(model))
        ):
            return model, refs, python, source
        root = Path(
            os.path.commonpath(
                (str(model), str(refs), str(python), str(source), str(settings.data_root))
            )
        )
        self._alias_root = root
        for letter in "VWXYZ":
            drive = Path(f"{letter}:\\")
            if drive.exists():
                try:
                    if not drive.samefile(root):
                        continue
                except OSError:
                    continue
            else:
                subprocess.run(["subst", f"{letter}:", str(root)], check=True, capture_output=True)
                self._alias = f"{letter}:"

            def remap(path: Path) -> Path:
                return drive / path.relative_to(root)

            return remap(model), remap(refs), remap(python), remap(source)
        raise RuntimeError("No free drive letter for local expressive voice model")

    @property
    def available(self) -> bool:
        required = self.model_dir / ("gpt.pth" if self.engine == "index" else "infer.py")
        reference_file = self.references_dir / "neutral.wav"
        profile_file = self.references_dir / "profile.json"
        if self.engine == "index" and profile_file.is_file():
            try:
                selected = json.loads(profile_file.read_text(encoding="utf-8"))[
                    "selected_reference"
                ]
                reference_file = (self.references_dir / selected).resolve()
                if not reference_file.is_relative_to(self.references_dir.resolve()):
                    return False
            except (OSError, ValueError, KeyError):
                return False
        return self.python.is_file() and required.is_file() and reference_file.is_file()

    @property
    def ready(self) -> bool:
        return (
            self._ready.is_set()
            and self.process is not None
            and self.process.poll() is None
            and not self.error
        )

    def start_background(self) -> None:
        with self._lifecycle_lock:
            if not self.available or (self.process is not None and self.process.poll() is None):
                return
            # A crashed or previously stopped process must never reuse its old
            # ready event or an unanswered response from the earlier process.
            self.process = None
            self._ready.clear()
            self._responses = Queue()
            self.ready_info = {}
            self.error = ""
            self.status = "starting"
            self._stopping = False
            self._spawn()

    def _spawn(self) -> None:
        try:
            model_dir, references_dir, python, source = self._ascii_paths()
        except Exception as exc:
            self.error = str(exc)
            self._ready.set()
            return
        cache = settings.data_root / "voice_cache"
        cache.mkdir(parents=True, exist_ok=True)
        if self.engine == "index" and self._alias:
            cache = Path(self._alias + "\\") / cache.relative_to(self._alias_root)
        env = os.environ.copy()
        for secret in ("OPENAI_API_KEY",):
            env.pop(secret, None)
        env.update(
            {
                "TEMP": str(cache),
                "TMP": str(cache),
                "PYTHONPYCACHEPREFIX": str(cache / "pycache"),
                "NUMBA_CACHE_DIR": str(cache / "numba"),
                "MPLCONFIGDIR": str(cache / "matplotlib"),
                "HF_HOME": str(cache / "hf"),
                "NLTK_DATA": str(cache / "nltk"),
                "HF_HUB_OFFLINE": "1",
                "VOICE_BACKEND_PID": str(os.getpid()),
                "VOICE_ALIAS_DRIVE": self._alias or "",
                "PYTHONPATH": os.pathsep.join(
                    filter(
                        None,
                        (
                            os.getenv(
                                "VOICE_WORKER_SOURCE_DIR", str(settings.project_root / "backend")
                            ),
                            str(source) if self.engine == "index" else "",
                        ),
                    )
                ),
            }
        )
        if self.engine == "index":
            # The selected model snapshot and its auxiliary weights are local.
            env.update(
                {
                    "USE_MODELSCOPE": "true",
                    "XDG_CACHE_HOME": str(cache),
                    "TORCH_HOME": str(cache),
                    "PYTHONIOENCODING": "utf-8",
                }
            )
        if getattr(sys, "frozen", False):
            # PyInstaller may add its Python DLL directory to a child's Windows
            # search path. The voice venv uses a different Python version.
            env["PATH"] = os.pathsep.join(
                entry
                for entry in env.get("PATH", "").split(os.pathsep)
                if entry and not Path(entry).resolve().is_relative_to(settings.project_root)
            )
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        frozen_windows = sys.platform == "win32" and getattr(sys, "frozen", False)
        if frozen_windows:
            import ctypes

            ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            self.process = subprocess.Popen(
                [
                    str(python),
                    "-u",
                    "-m",
                    "app.services.voice.worker_process",
                    str(model_dir),
                    str(references_dir),
                    self.engine,
                ],
                cwd=source,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except Exception:
            if self._alias:
                subprocess.run(["subst", self._alias, "/D"], capture_output=True)
                self._alias = None
            raise
        finally:
            if frozen_windows:
                ctypes.windll.kernel32.SetDllDirectoryW(str(settings.project_root))
        process = self.process
        Thread(target=self._read_stdout, args=(process,), daemon=True).start()
        Thread(target=self._read_stderr, args=(process,), daemon=True).start()

    def _read_stdout(self, process: subprocess.Popen) -> None:
        assert process.stdout
        for line in process.stdout:
            if self.process is not process:
                break
            try:
                payload = json.loads(line)
            except ValueError:
                logger.warning("Voice worker sent malformed output")
                continue
            if payload.get("type") == "ready":
                self.ready_info = payload
                self.status = "ready"
                self._ready.set()
            elif payload.get("type") == "startup_error":
                self.error = payload.get("error", "Voice startup failed")
                self.status = "error"
                self._ready.set()
            elif payload.get("type") == "status":
                self.status = payload.get("status", self.status)
            else:
                self._responses.put(payload)
        if not self._stopping and self.process is process:
            self.error = self.error or "Voice worker exited"
            self.status = "error"
            self._ready.set()
            self._responses.put({"type": "error", "error": self.error})

    def _read_stderr(self, process: subprocess.Popen) -> None:
        assert process.stderr
        for line in process.stderr:
            logger.info("Voice worker: %s", line.rstrip())

    @contextmanager
    def _request_scope(self, cancel_event, timeout):
        deadline = time.monotonic() + timeout
        while not self._request_lock.acquire(timeout=0.1):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Voice generation cancelled")
            if time.monotonic() >= deadline:
                raise RuntimeError("Voice generation timed out")
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Voice generation cancelled")
            yield
        finally:
            self._request_lock.release()

    def synthesize(
        self,
        text: str,
        control: EngineControl,
        timeout: float | None = None,
        cancel_event: Event | None = None,
    ) -> dict:
        timeout = timeout or min(180, max(45, 30 + len(text) * 2))
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Voice generation cancelled")
        self.start_background()
        ready_deadline = time.monotonic() + timeout
        while not self._ready.wait(timeout=0.1):
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Voice generation cancelled")
            if time.monotonic() >= ready_deadline:
                break
        if not self.ready:
            raise RuntimeError(self.error or "Voice model is still unavailable")
        with self._request_scope(cancel_event, timeout):
            if not self.ready or self.process is None or self.process.stdin is None:
                raise RuntimeError("Voice worker unavailable")
            request_id = str(uuid4())
            request = {
                "command": "synthesize",
                "id": request_id,
                "text": text,
                "control": asdict(control),
            }
            self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    # Only this request's own worker is stopped while holding its
                    # request lock. No process-name kill or other edition access.
                    process = self.process
                    self._stopping = True
                    if process is not None and process.poll() is None:
                        process.terminate()
                        process.wait(timeout=3)
                    self._ready.clear()
                    raise RuntimeError("Voice generation cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Voice generation timed out")
                try:
                    response = self._responses.get(timeout=min(remaining, 0.1))
                    break
                except Empty:
                    continue
            if response.get("type") != "audio" or response.get("id") != request_id:
                raise RuntimeError(response.get("error", "Voice worker protocol error"))
            return response

    def stop(self) -> None:
        self._stopping = True
        try:
            if self.process is not None and self.process.poll() is None and self.process.stdin:
                self.process.stdin.write('{"command":"shutdown"}\n')
                self.process.stdin.flush()
                self.process.wait(timeout=3)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        finally:
            self.process = None
            self._ready.clear()
            self.ready_info = {}
            self.status = "starting"
            if self._alias:
                subprocess.run(["subst", self._alias, "/D"], capture_output=True)
                self._alias = None


class IndexVoiceClient(VoiceWorkerClient):
    """The production voice pipeline uses IndexTTS only."""

    def __init__(self):
        super().__init__(engine="index")
        self.selected_engine = "index"

    def restart(self) -> None:
        self.stop()
        self.start_background()
