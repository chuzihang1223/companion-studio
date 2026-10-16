"""Size-limited SDK transport; only this child's Job/process is ever stopped."""

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys

import anyio
from mcp.shared.message import SessionMessage
from mcp_types import jsonrpc_message_adapter

from app.services.evaluation.environment import OwnedJob
from app.services.mcp_ext.contracts import MCPError

MAX_FRAME = 1048576


def executable(value):
    path = Path(value)
    if (
        not path.is_absolute()
        or not path.is_file()
        or path.is_symlink()
        or path.suffix.lower() != ".exe"
        or path.name.lower()
        in {"cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe"}
    ):
        raise MCPError("MCP_COMMAND_DENIED")
    return str(path.resolve())


class OwnedStdio:
    def __init__(self, config, environment, workdir):
        self.config, self.environment, self.workdir = config, environment, workdir
        self.dead = asyncio.Event()
        self.process = None
        self.stderr_bytes = 0

    @asynccontextmanager
    async def open(self):
        env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ}
        env.update(self.environment)
        env.update(
            TEMP=str(self.workdir),
            TMP=str(self.workdir),
            PYTHONIOENCODING="utf-8",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONNOUSERSITE="1",
        )
        self.workdir.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            executable(self.config.endpoint_or_command),
            *self.config.args,
            cwd=self.workdir,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.process = process
        job, tasks = None, []
        tx, rx = anyio.create_memory_object_stream(0)
        writer, outgoing = anyio.create_memory_object_stream(0)

        async def read():
            try:
                async with tx:
                    while line := await process.stdout.readline():
                        if len(line) > MAX_FRAME:
                            raise MCPError()
                        try:
                            message = SessionMessage(jsonrpc_message_adapter.validate_json(line))
                        except ValueError:
                            raise MCPError("MCP_PROTOCOL_MISMATCH") from None
                        await tx.send(message)
            except (ValueError, OSError, anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass
            finally:
                self.dead.set()

        async def write():
            try:
                async with outgoing:
                    async for message in outgoing:
                        data = (
                            message.message.model_dump_json(
                                by_alias=True, exclude_unset=True
                            ).encode()
                            + b"\n"
                        )
                        if len(data) > MAX_FRAME:
                            raise MCPError()
                        process.stdin.write(data)
                        await process.stdin.drain()
            except (ValueError, OSError, anyio.BrokenResourceError):
                self.dead.set()

        async def stderr():
            # Drain without persisting arbitrary logs, credentials or private paths.
            while data := await process.stderr.read(4096):
                self.stderr_bytes = min(1_000_000_000, self.stderr_bytes + len(data))

        try:
            job = OwnedJob(process.pid)
            tasks = [asyncio.create_task(fn()) for fn in (read, write, stderr)]
            yield rx, writer
        finally:
            writer.close()
            if process.stdin:
                process.stdin.close()
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), 1)
                except TimeoutError:
                    if job:
                        job.close()
                    else:
                        process.kill()
                    await asyncio.wait_for(process.wait(), 3)
            if job:
                job.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            rx.close()
            outgoing.close()
            self.dead.set()
