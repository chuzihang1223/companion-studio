"""评测子进程/目录的所有权边界；终止时仅清理本管理器创建的资源。"""

import asyncio
from contextlib import closing, contextmanager
import ctypes
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import sys
from uuid import UUID, uuid4

from app.core.filesystem import disk_path


class OwnedJob:
    """Windows Job 的 kill-on-close 覆盖 worker 的后代，父进程崩溃也不遗留它们。"""

    def __init__(self, pid, *, memory_bytes=None):
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
        if memory_bytes is not None:
            config.basic.flags |= 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
            config.process_memory = memory_bytes
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

    def check_capacity(self):
        total = 0
        # Include the new bounded durable sources, including abandoned owned
        # staging. Retaining them on archive failure must not evade the old cap.
        for pattern in (
            "*/*/data/chat.db",
            "*/*/data/chat.db.trace-pending/*",
            "_w/*/data/chat.db",
            "_w/*/data/chat.db.trace-pending/*",
        ):
            for path in self.root.glob(pattern):
                try:
                    if path.is_file() and not any(
                        p.is_symlink() or p.is_junction()
                        for p in (path, *path.parents)
                        if p.is_relative_to(self.root)
                    ):
                        total += path.stat().st_size
                except FileNotFoundError:
                    continue  # A completed run can be removed during this scan.
        if total >= 256 * 1024 * 1024:
            raise ValueError("评测证据已达 256 MiB，请删除旧评测历史")

    def path(self, run_id, attempt_id=None):
        target = self.root / str(UUID(run_id))
        if attempt_id:
            target /= str(UUID(attempt_id))
        if not target.resolve().is_relative_to(self.root) or any(
            parent.is_symlink() or parent.is_junction()
            for parent in [target, *target.parents]
            if parent.is_relative_to(self.root)
        ):
            raise ValueError("评测目录所有权无效")
        return target

    async def run(self, run_id, attempt_id, payload, timeout):
        self.check_capacity()
        archive = self.path(run_id, attempt_id)
        if archive.exists():
            raise ValueError("评测档案已存在，拒绝重用")
        archive.mkdir(parents=True)
        # Run/Attempt UUID nesting plus the runtime's own sandbox UUID can exceed
        # Windows MAX_PATH. Use a shorter, exclusive disposable execution name,
        # then archive only SQLite evidence at the stable Run/Attempt address.
        home = self.root / "_w" / uuid4().hex[:16]
        if not home.resolve().is_relative_to(self.root):
            raise ValueError("临时评测目录超出范围")
        home.mkdir(parents=True, exist_ok=False)
        (home / "owner.json").write_text(
            json.dumps({"run_id": run_id, "attempt_id": attempt_id}), encoding="utf-8"
        )
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
        if os.environ.get("AGENT_PYTHON"):
            # The main application already selects its coding interpreter. Only
            # that executable identity is retained, no virtualenv/profile config.
            env["AGENT_PYTHON"] = os.environ["AGENT_PYTHON"]
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
        try:
            if payload.get("dataset_id") == "recovery-core" and payload["case_id"] == "REC-012":
                started = asyncio.get_running_loop().time()
                first = await self.launch_worker(
                    command,
                    dict(env),
                    home,
                    attempt_id,
                    {**payload, "restart_stage": "prepare"},
                    timeout,
                )
                if first.get("worker_exited") != 86:
                    raise WorkerFailure("RESTART_PREPARATION_FAILED")
                remaining = max(1, timeout - (asyncio.get_running_loop().time() - started))
                result = await self.launch_worker(
                    command,
                    dict(env),
                    home,
                    attempt_id,
                    {**payload, "restart_stage": "reopen", "parent_observed_restart": True},
                    remaining,
                )
            else:
                result = await self.launch_worker(
                    command, dict(env), home, attempt_id, payload, timeout
                )
            if "worker_exited" in result:
                raise WorkerFailure("UNEXPECTED_WORKER_EXIT")
            return result
        finally:
            archived = False
            try:
                # Closed worker DBs may retain a WAL after abrupt termination.
                # SQLite backup reads a consistent snapshot and folds that WAL
                # into the archived DB, rather than copying an incomplete file.
                source = data / "chat.db"
                if source.is_file():
                    await asyncio.to_thread(self.archive_database, source, archive)
                archived = True
            finally:
                if (
                    archived
                    and home.resolve().parent == (self.root / "_w").resolve()
                    and not home.is_symlink()
                    and not home.is_junction()
                ):
                    # Skills may contain immutable revisions below MAX_PATH.
                    # Ownership is checked above, before applying the IO prefix.
                    await asyncio.to_thread(shutil.rmtree, disk_path(home))

    async def launch_worker(self, command, env, home, attempt_id, payload, timeout):
        """One authenticated process/Job. No profiles or payload files cross IPC."""
        secret = secrets.token_urlsafe(32)
        outcome = asyncio.get_running_loop().create_future()
        ready = asyncio.Event()
        writers = set()
        receivers = set()

        async def receive(reader, writer):
            receiver = asyncio.current_task()
            if len(receivers) >= 8:
                writer.close()
                return
            receivers.add(receiver)
            writers.add(writer)
            authenticated = False
            try:
                hello = (await asyncio.wait_for(reader.readline(), 3)).decode().strip()
                if not hmac.compare_digest(hello, secret):
                    return
                authenticated = True
                await ready.wait()
                # Framed local IPC also works in PyInstaller --noconsole, whose
                # sys.stdin/stdout are None. Credentials stay in this private pipe
                # equivalent, never command-line arguments or config files.
                writer.write(json.dumps(payload, ensure_ascii=True).encode() + b"\n")
                await writer.drain()
                raw = await reader.readline()
                if len(raw) > 32000:
                    raise ValueError("worker output exceeds limit")
                value = json.loads(raw) if raw else {"worker_exited": await process.wait()}
                if not outcome.done():
                    outcome.set_result(value)
            except (ConnectionError, OSError):
                # Windows reports a reset, rather than EOF, when the controlled
                # crash test hard-exits. Only the authenticated worker's actual
                # process exit code can authorize the second recovery segment.
                try:
                    code = (
                        await asyncio.wait_for(process.wait(), 3)
                        if authenticated and process
                        else None
                    )
                    if code is not None and not outcome.done():
                        outcome.set_result({"worker_exited": code})
                    elif not outcome.done():
                        outcome.set_exception(RuntimeError("worker IPC unavailable"))
                except Exception:
                    if not outcome.done():
                        outcome.set_exception(RuntimeError("worker IPC unavailable"))
            except Exception:
                if not outcome.done():
                    outcome.set_exception(RuntimeError("worker IPC unavailable"))
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
                finally:
                    writers.discard(writer)
                    receivers.discard(receiver)

        server = await asyncio.start_server(receive, "127.0.0.1", 0, limit=32768)
        env["COMPANION_EVAL_PORT"] = str(server.sockets[0].getsockname()[1])
        env["COMPANION_EVAL_NONCE"] = secret
        process, job = None, None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=home,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                start_new_session=sys.platform != "win32",
                limit=65536,
            )
            self.active[attempt_id] = process
            job = OwnedJob(process.pid)
            ready.set()
            result = await asyncio.wait_for(outcome, timeout)
            await asyncio.wait_for(process.wait(), 3)
            if "error" in result:
                raise WorkerFailure(result.get("error_type"))
            return result
        finally:
            server.close()
            await server.wait_closed()
            for writer in list(writers):
                writer.close()
            ready.set()
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
            pending = list(receivers)
            if pending:
                try:
                    await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 4)
                except TimeoutError:
                    for receiver in pending:
                        receiver.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def archive_database(source, archive):
        import sqlite3

        (archive / "data").mkdir(exist_ok=True)
        with (
            closing(sqlite3.connect(source)) as src,
            closing(sqlite3.connect(archive / "data" / "chat.db")) as dest,
        ):
            src.backup(dest)
        from app.services.trace.journal import TraceJournal

        TraceJournal.archive(source, archive / "data" / "chat.db")

    def recover_links(self, run_id, attempt_id):
        """仅在 worker 结束后恢复自己的 Trace，强制终止只能标记中断。"""
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker
        from app.models.agent_trace import AgentTrace
        from app.services.trace.store import TraceStore

        path = self.path(run_id, attempt_id) / "data" / "chat.db"
        if not path.is_file():
            return {}
        engine = create_engine(f"sqlite:///{path.as_posix()}")
        try:
            factory = sessionmaker(engine, expire_on_commit=False)
            store = TraceStore(factory)
            try:
                store.recover()
            finally:
                store.close_idle()
            with factory() as db:
                row = db.scalar(select(AgentTrace))
                if row:
                    return {
                        "trace_id": row.id,
                        "task_id": row.task_id,
                        "conversation_id": row.conversation_id,
                        "work_session_id": row.work_session_id,
                    }
                return {}
        finally:
            engine.dispose()

    def cleanup_abandoned(self, repository):
        folder = self.root / "_w"
        if (
            not folder.exists()
            or folder.is_symlink()
            or folder.is_junction()
            or not folder.resolve().is_relative_to(self.root)
        ):
            return
        for child in folder.iterdir():
            if (
                re.fullmatch(r"[0-9a-f]{16}", child.name)
                and child.is_dir()
                and not child.is_symlink()
                and not child.is_junction()
                and child.resolve().is_relative_to(self.root)
            ):
                archived = False
                try:
                    owner = json.loads((child / "owner.json").read_text(encoding="utf-8"))
                    run_id, attempt_id = str(UUID(owner["run_id"])), str(UUID(owner["attempt_id"]))
                    repository.attempt(run_id, attempt_id)
                    archive = self.path(run_id, attempt_id)
                    source = child / "data" / "chat.db"
                    if source.is_file() and source.resolve().is_relative_to(child.resolve()):
                        # Retry both backups after an interrupted copy. The
                        # exited worker's owned staging remains authoritative
                        # until the main DB AND its pending sources are sealed.
                        archive.mkdir(parents=True, exist_ok=True)
                        self.archive_database(source, archive)
                    links = self.recover_links(run_id, attempt_id)
                    repository.attach_recovered_trace(run_id, attempt_id, links)
                    archived = True
                except (OSError, ValueError, KeyError, LookupError):
                    pass  # Unknown/corrupt staging is not evidence of task success.
                finally:
                    if archived:
                        shutil.rmtree(disk_path(child))

    @contextmanager
    def trace_repository(self, run_id, attempt_id):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.services.trace.repository import TraceRepository

        path = self.path(run_id, attempt_id) / "data" / "chat.db"
        if not path.is_file() or path.is_symlink():
            raise LookupError("该评测没有可用 Trace")
        wal = path.with_name(path.name + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise LookupError("评测 Trace 归档尚未封存")
        # Immutable read-only connection: opening a history panel cannot migrate,
        # create a SQLite file, modify a trace or resume an Agent task.
        engine = create_engine(
            f"sqlite:///file:{path.as_posix()}?mode=ro&immutable=1&uri=true",
            connect_args={"check_same_thread": False},
        )
        try:
            yield TraceRepository.read_only(sessionmaker(engine, expire_on_commit=False))
        finally:
            engine.dispose()

    def remove(self, run_id):
        target = self.path(run_id)
        if target.is_dir():
            # path() has already checked the UUID and owned-root boundary.
            # Journal files may exceed MAX_PATH even when this run path does not.
            shutil.rmtree(disk_path(target))


class WorkerFailure(RuntimeError):
    def __init__(self, error_type):
        # Only Python class categories, never provider messages, URLs or paths.
        known = {
            "ValueError",
            "KeyError",
            "FileNotFoundError",
            "PermissionError",
            "OperationalError",
            "RuntimeError",
            "OSError",
        }
        self.code = "WORKER_" + error_type.upper() if error_type in known else "WORKER_ERROR"
        if error_type in {
            "LLMConfigurationError",
            "LLMResponseError",
            "LLMHTTPError",
            "LLMTimeoutError",
            "LLMAuthenticationError",
        }:
            self.code = "MODEL_ERROR"
        if error_type == "EvaluationBudgetExceeded":
            self.code = "MODEL_BUDGET_EXHAUSTED"
        super().__init__(self.code)
