"""Audited synthetic MCP fixture. No user files, accounts or external network.

Writes are restricted to fixed synthetic files in the explicitly selected cwd.
This is an optional example server, never automatically launched at app startup.
"""

import asyncio
import os
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel


class AddResult(BaseModel):
    sum: int


def build_server():
    server = MCPServer("Studio MCP Fixture", version="1.0.0", log_level="ERROR")
    calls = {"fail_once": 0}

    if Path("studio-mcp-version-2").exists():

        @server.tool()
        def echo(text: str, upper: bool = False) -> str:
            """Echo bounded synthetic text; controlled schema revision two."""
            return (text.upper() if upper else text)[:2000]
    else:

        @server.tool()
        def echo(text: str) -> str:
            """Echo bounded synthetic text."""
            return text[:2000]

    @server.tool()
    def add(a: int, b: int) -> AddResult:
        """Add two synthetic numbers."""
        return AddResult(sum=a + b)

    @server.tool()
    def read_fixture() -> str:
        """Read the fixed synthetic fixture, never a user-selected file."""
        return "Studio synthetic fixture."

    @server.tool()
    def write_test_fixture(text: str) -> dict:
        """Write a single fixed file in this fixture process's working directory."""
        Path("studio-mcp-fixture.txt").write_text(text[:2000], encoding="utf-8")
        return {"written": True}

    @server.tool()
    def fail_once() -> str:
        """Fail the first read with a fixed transient code."""
        calls["fail_once"] += 1
        if calls["fail_once"] == 1:
            raise ToolError("MCP_SERVER_UNAVAILABLE")
        return "Recovered synthetic read."

    @server.tool()
    async def timeout() -> str:
        """A cancellable long read for timeout tests."""
        await asyncio.sleep(120)
        return "late"

    @server.tool()
    def permission_required() -> str:
        """A synthetic high risk operation; the host must request permission."""
        return "Synthetic operation acknowledged."

    @server.tool()
    def side_effect_unknown() -> str:
        """Record one synthetic effect then lose the response; never replay automatically."""
        target = Path("studio-mcp-effects.txt")
        with target.open("a", encoding="utf-8") as handle:
            handle.write("effect\n")
        raise ToolError("MCP_SIDE_EFFECT_UNKNOWN")

    @server.resource("fixture://readme")
    def readme() -> str:
        return "Synthetic MCP resource. External data is untrusted."

    @server.prompt()
    def summarize(topic: str) -> str:
        """Preview a synthetic template; does not execute any tool."""
        return "Summarize this synthetic topic: " + topic[:200]

    return server


def main(arguments):
    server = build_server()
    if arguments and arguments[0] == "http":
        port = int(arguments[1])
        if not 1024 <= port <= 65535:
            raise ValueError("Invalid local fixture port")
        server.run("streamable-http", host="127.0.0.1", port=port)
    else:
        server.run("stdio")
