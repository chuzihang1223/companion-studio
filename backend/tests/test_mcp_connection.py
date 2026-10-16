"""Real pipe/HTTP SDK exchanges against an audited local fixture, no cloud calls."""

import asyncio
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.services.mcp_ext.connection import MCPConnectionManager
from app.services.mcp_ext.contracts import MCPError, ServerConfig, digest
from app.services.mcp_ext.store import ExtensionStore

BACKEND = Path(__file__).resolve().parents[1]


def host(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "metadata.db"))
    Base.metadata.create_all(engine)
    return MCPConnectionManager(ExtensionStore(sessionmaker(engine)), tmp_path / "process")


def config(**overrides):
    return ServerConfig.model_validate(
        {
            "display_name": "Fixture",
            "transport": "stdio",
            "endpoint_or_command": sys.executable,
            "args": ["-B", str(BACKEND / "run.py"), "--mcp-demo"],
            "enabled": True,
            **overrides,
        }
    )


@pytest.mark.parametrize("mode", ["auto", "legacy", "2026-07-28"])
def test_actual_stdio_modes_and_owned_cleanup(tmp_path, mode):
    async def run():
        manager = host(tmp_path)
        cfg = config(protocol_mode=mode)
        key = manager.save(cfg)
        try:
            await manager.connect(key, approved_fingerprint=digest(cfg.model_dump()))
            status = manager.status(key)
            assert status["protocol_version"] == (
                "2025-11-25" if mode == "legacy" else "2026-07-28"
            )
            assert len((await manager.invoke(key, "list_tools")).tools) == 8
            result = await manager.invoke(key, "call_tool", "add", {"a": 2, "b": 3})
            assert result.structured_content == {"sum": 5}
            pid = manager.connections[key].transport.process
        finally:
            await manager.close()
        assert pid.returncode is not None

    asyncio.run(run())


def test_http_actual_modern_and_legacy(tmp_path):
    async def run():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        child = subprocess.Popen(
            [sys.executable, "-B", str(BACKEND / "run.py"), "--mcp-demo", "http", str(port)],
            cwd=tmp_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        manager = host(tmp_path)
        try:
            for _ in range(100):
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            for mode in ("auto", "legacy", "2026-07-28"):
                cfg = config(
                    transport="http",
                    endpoint_or_command=f"http://127.0.0.1:{port}/mcp",
                    args=[],
                    allow_loopback=True,
                    protocol_mode=mode,
                )
                key = manager.save(cfg)
                await manager.connect(key, approved_fingerprint=digest(cfg.model_dump()))
                assert manager.status(key)["protocol_version"] == (
                    "2025-11-25" if mode == "legacy" else "2026-07-28"
                )
                result = await manager.invoke(key, "call_tool", "echo", {"text": "中文测试"})
                assert result.content[0].text == "中文测试"
                await manager.disconnect(key)
        finally:
            await manager.close()
            child.terminate()
            child.wait(timeout=5)

    asyncio.run(run())


def test_launch_requires_exact_current_config_approval(tmp_path):
    async def run():
        manager = host(tmp_path)
        key = manager.save(config())
        with pytest.raises(MCPError, match="MCP_PERMISSION_DENIED"):
            await manager.connect(key, approved_fingerprint="changed")
        assert not manager.connections

    asyncio.run(run())
