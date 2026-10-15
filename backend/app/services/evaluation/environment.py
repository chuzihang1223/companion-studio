"""评测子进程/目录的所有权边界；终止时仅清理本管理器创建的资源。"""

import asyncio
from contextlib import closing, contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import UUID, uuid4


class OwnedJob:
    """Windows Job 的 kill-on-close 覆盖 worker 的后代，父进程崩溃也不遗留它们。"""

    def __init__(self, pid):
        self.handle = None
        if sys.platform != "win32":
            return
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD),
                ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t),
                ("active_limit", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.OpenProcess.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel = kernel
        job = kernel.CreateJobObjectW(None, None)
        config = Extended()
        config.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            if (
                not job
                or not process
                or not kernel.SetInformationJobObject(
                    job, 9, ctypes.byref(config), ctypes.sizeof(config)
                )
                or not kernel.AssignProcessToJobObject(job, process)
            ):
                raise OSError("evaluation process ownership unavailable")
            self.handle = job
        except BaseException:
            if job:
                kernel.CloseHandle(job)
            raise
        finally:
            if process:
                kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class EnvironmentManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.active = {}

    def path(self, run_id, attempt_id=None):
        target = self.root / str(UUID(run_id))
        if attempt_id:
            target /= str(UUID(attempt_id))
        if not target.resolve().is_relative_to(self.root) or any(
            parent.is_symlink()
            for parent in [target, *target.parents]
            if parent.is_relative_to(self.root)
        ):
            raise ValueError("评测目录所有权无效")
        return target

    async def run(self, run_id, attempt_id, payload, timeout):
        archive = self.path(run_id, attempt_id)
        if archive.exists():
            raise ValueError("评测档案已存在，拒绝重用")
        archive.mkdir(parents=True)
        # Run/Attempt UUID nesting plus the runtime's own sandbox UUID can exceed
        # Windows MAX_PATH. Use a shorter, exclusive disposable execution name,
        # then archive only SQLite evidence at the stable Run/Attempt address.
        home = self.root / "_w" / uuid4().hex[:16]
        home.mkdir(parents=True, exist_ok=False)
        data = home / "data"
        config = home / "config"
        data.mkdir()
        config.mkdir()
        (data / ".env").write_text("", encoding="utf-8")
        # No credentials, HOME, proxy or user config inherited. Python installation
        # and DLL discovery retain OS variables only; all writable caches are owned.
        env = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "PATH", "PATHEXT", "COMSPEC")
            if key in os.environ
        }
        env.update(
            COMPANION_EVALUATION_WORKER="1",
            COMPANION_DATA_DIR=str(data),
            COMPANION_HOME=str(home),
            TEMP=str(home),
            TMP=str(home),
            HOME=str(config),
            USERPROFILE=str(config),
            APPDATA=str(config),
            LOCALAPPDATA=str(config),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONIOENCODING="utf-8",
            PYTHONUTF8="1",
            OPENAI_API_KEY="",
            OPENAI_MODEL="",
            OPENAI_BASE_URL="http://127.0.0.1:9/v1",
            FISH_API_KEY="",
            FISH_REFERENCE_ID="",
            COMPANION_CLEAN_DISTRIBUTION="1",
            PYINSTALLER_RESET_ENVIRONMENT="1",
        )
        command = (
            [sys.executable, "--evaluation-worker"]
            if getattr(sys, "frozen", False)
            else [
                sys.executable,
                "-B",
                str(Path(__file__).resolve().parents[3] / "run.py"),
                "--evaluation-worker",
            ]
        )
        process, job = None, None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=home,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32",
                limit=65536,
            )
            self.active[attempt_id] = process
            job = OwnedJob(process.pid)
            stdout, _ = await asyncio.wait_for(
                process.communicate(json.dumps(payload, ensure_ascii=True).encode()), timeout
            )
            if len(stdout) > 32000 or process.returncode != 0:
                raise RuntimeError("worker output unavailable")
            result = json.loads(stdout)
            if "error" in result:
                raise RuntimeError("worker execution error")
            return result
        finally:
            if job:
                job.close()
            if process and process.returncode is None:
                if sys.platform != "win32":
                    import signal

                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                await process.wait()
            self.active.pop(attempt_id, None)
            try:
                # Closed worker DBs may retain a WAL after abrupt termination.
                # SQLite backup reads a consistent snapshot and folds that WAL
                # into the archived DB, rather than copying an incomplete file.
                source = data / "chat.db"
                if source.is_file():
                    await asyncio.to_thread(self.archive_database, source, archive)
            finally:
                if home.resolve().parent == (self.root / "_w").resolve() and not home.is_symlink():
                    await asyncio.to_thread(shutil.rmtree, home)

    @staticmethod
    def archive_database(source, archive):
        import sqlite3

        (archive / "data").mkdir(exist_ok=True)
        with (
            closing(sqlite3.connect(source)) as src,
            closing(sqlite3.connect(archive / "data" / "chat.db")) as dest,
        ):
            src.backup(dest)

    @contextmanager
    def trace_repository(self, run_id, attempt_id):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.services.trace.repository import TraceRepository
        from app.services.trace.store import TraceStore

        path = self.path(run_id, attempt_id) / "data" / "chat.db"
        if not path.is_file() or path.is_symlink():
            raise LookupError("该评测没有可用 Trace")
        # Immutable read-only connection: opening a history panel cannot migrate,
        # create a SQLite file, modify a trace or resume an Agent task.
        engine = create_engine(
            f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true",
            connect_args={"check_same_thread": False},
        )
        try:
            yield TraceRepository(TraceStore(sessionmaker(engine, expire_on_commit=False)))
        finally:
            engine.dispose()

    def remove(self, run_id):
        target = self.path(run_id)
        if target.is_dir():
            shutil.rmtree(target)
