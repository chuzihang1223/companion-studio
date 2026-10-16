"""One lifecycle owner per Server; no reconnect loop can replay a tool call."""

import asyncio
from collections import deque
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import random
import logging

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError as SDKError
from mcp_types import REQUEST_TIMEOUT, METHOD_NOT_FOUND, UNSUPPORTED_PROTOCOL_VERSION

from app.core.config import settings
from app.services.mcp_ext.contracts import ConnectionStatus as S, MCPError, ServerConfig, digest
from app.services.mcp_ext.events import emit
from app.services.mcp_ext.network import EndpointTransport, endpoint
from app.services.mcp_ext.stdio import OwnedStdio, executable

# Vendor diagnostics may contain raw protocol bodies/URLs. Our fixed status and
# Trace codes are the public diagnostic boundary; never forward vendor text.
for _name in ("mcp", "httpx2", "httpcore2"):
    _logger = logging.getLogger(_name)
    _logger.addHandler(logging.NullHandler())
    _logger.propagate = False


def error_code(error):
    if isinstance(error, MCPError):
        return error.code
    if isinstance(error, (TimeoutError, httpx2.TimeoutException)):
        return "MCP_CONNECTION_TIMEOUT"
    if isinstance(error, SDKError):
        return (
            "MCP_CONNECTION_TIMEOUT"
            if error.code == REQUEST_TIMEOUT
            else "MCP_TOOL_NOT_FOUND"
            if error.code == METHOD_NOT_FOUND
            else "MCP_PROTOCOL_MISMATCH"
            if error.code == UNSUPPORTED_PROTOCOL_VERSION
            else "MCP_SERVER_UNAVAILABLE"
        )
    if isinstance(error, BaseExceptionGroup):
        return error_code(error.exceptions[0])
    return "MCP_SERVER_UNAVAILABLE"


class Connection:
    def __init__(self, config):
        self.config = config
        self.status, self.last_error = S.DISCONNECTED, None
        self.last_connected_at = None
        self.protocol_version = None
        self.capabilities = {}
        self.client = None
        self.owner = None
        self.stop = asyncio.Event()
        self.ready = asyncio.Event()
        self.calls = set()
        self.semaphore = asyncio.Semaphore(2)
        self.transport = None
        self.reconnections = 0
        self.history = deque(maxlen=30)


class MCPConnectionManager:
    def __init__(self, store, root=None):
        self.store = store
        self.root = root or settings.data_root / "extensions" / "process-cache"
        self.connections = {}
        self.credentials = {}  # Values are delivered by main; never serialized.
        self.locks = {}
        self.on_discovery = None
        self.closing = False

    def config(self, server_id):
        return ServerConfig.model_validate(self.store.get("server", server_id)["config"])

    def save(self, config, server_id=None):
        if config.transport == "http":
            endpoint(config.endpoint_or_command, config.allow_loopback)
        else:
            executable(config.endpoint_or_command)
        if server_id and server_id in self.connections and self.connections[server_id].owner:
            raise MCPError("MCP_DISCONNECT_BEFORE_EDIT")
        row = self.store.save("server", {"config": config.model_dump()}, server_id)
        return row["id"]

    def status(self, server_id):
        config = self.config(server_id)
        c = self.connections.get(server_id)
        return {
            "server_id": server_id,
            "config": config.model_dump(),
            "config_fingerprint": digest(config.model_dump()),
            "connection_status": c.status if c else S.DISCONNECTED,
            "protocol_version": c.protocol_version if c else None,
            "capabilities": c.capabilities if c else {},
            "last_error": c.last_error if c else None,
            "last_connected_at": c.last_connected_at if c else None,
            "reconnection_count": c.reconnections if c else 0,
            "recent_calls": list(c.history) if c else [],
        }

    async def connect(self, server_id, *, approved_fingerprint):
        async with self.locks.setdefault(server_id, asyncio.Lock()):
            if self.closing:
                raise MCPError("MCP_SERVER_UNAVAILABLE")
            config = self.config(server_id)
            if not config.enabled or approved_fingerprint != digest(config.model_dump()):
                raise MCPError("MCP_PERMISSION_DENIED", 403)
            creds = self.credentials.get(server_id, {})
            if creds.get("config_fingerprint") != digest(config.model_dump()):
                creds = {}
            if (
                config.transport == "http"
                and config.credential_reference
                and not creds.get("token")
            ) or any(not creds.get("environment", {}).get(k) for k in config.environment_names):
                raise MCPError("MCP_AUTH_REQUIRED")
            await self.disconnect(server_id)
            c = Connection(config)
            self.connections[server_id] = c
            emit("MCP_CONNECTION_STARTED", server_id=server_id)
            c.owner = asyncio.create_task(self._own(server_id, c))
            try:
                await c.ready.wait()
            except asyncio.CancelledError:
                await self.disconnect(server_id)
                raise
            if c.status != S.CONNECTED:
                raise MCPError(c.last_error or "MCP_SERVER_UNAVAILABLE")
            return self.status(server_id)

    async def _own(self, server_id, c):
        try:
            # Only establishing a connection is retried. Authentication is not.
            for attempt in range(3):
                if c.stop.is_set():
                    break
                c.status = S.CONNECTING if not attempt else S.RECONNECTING
                c.reconnections = attempt
                try:
                    async with AsyncExitStack() as stack:
                        config = c.config
                        creds = self.credentials.get(server_id, {})
                        if creds.get("config_fingerprint") != digest(config.model_dump()):
                            creds = {}
                        if config.transport == "stdio":
                            env = {
                                k: creds.get("environment", {}).get(k, "")
                                for k in config.environment_names
                            }
                            c.transport = OwnedStdio(config, env, self.root / server_id)
                            transport = c.transport.open()
                        else:
                            headers = (
                                {"Authorization": "Bearer " + creds["token"]}
                                if config.credential_reference and creds.get("token")
                                else {}
                            )
                            http = await stack.enter_async_context(
                                httpx2.AsyncClient(
                                    transport=EndpointTransport(
                                        config.endpoint_or_command, config.allow_loopback
                                    ),
                                    headers=headers,
                                    timeout=config.call_timeout,
                                    trust_env=False,
                                    follow_redirects=False,
                                )
                            )
                            transport = streamable_http_client(
                                config.endpoint_or_command,
                                http_client=http,
                                max_sse_event_size=1048576,
                            )
                        client = Client(
                            transport,
                            mode=config.protocol_mode,
                            read_timeout_seconds=config.call_timeout,
                            cache=None,
                        )
                        async with asyncio.timeout(config.connect_timeout):
                            c.client = await stack.enter_async_context(client)
                            c.protocol_version = client.protocol_version
                            c.capabilities = client.server_capabilities.model_dump(
                                exclude_none=True
                            )
                            if self.on_discovery:
                                await self.on_discovery(server_id, client)
                        c.last_error = None
                        c.last_connected_at = datetime.now(timezone.utc).isoformat()
                        c.status = S.CONNECTED
                        emit("MCP_CONNECTION_ESTABLISHED", server_id=server_id)
                        c.ready.set()
                        while not c.stop.is_set():
                            if c.transport and c.transport.dead.is_set():
                                raise MCPError("MCP_SERVER_UNAVAILABLE")
                            try:
                                await asyncio.wait_for(c.stop.wait(), 0.2)
                            except TimeoutError:
                                pass
                    break
                except Exception as exc:
                    c.client = None
                    c.last_error = error_code(exc)
                    if (
                        c.ready.is_set()
                        or c.last_error == "MCP_AUTH_REQUIRED"
                        or c.stop.is_set()
                        or attempt == 2
                    ):
                        break
                    emit("MCP_RECONNECT_ATTEMPTED", server_id=server_id, attempt=attempt + 1)
                    try:
                        await asyncio.wait_for(
                            c.stop.wait(), 0.2 * 2**attempt + random.random() * 0.1
                        )
                    except TimeoutError:
                        pass
        finally:
            c.client = None
            c.status = (
                S.STOPPED
                if c.stop.is_set()
                else S.AUTH_REQUIRED
                if c.last_error == "MCP_AUTH_REQUIRED"
                else S.FAILED
            )
            c.ready.set()
            emit(
                "MCP_SERVER_DISCONNECTED" if c.stop.is_set() else "MCP_CONNECTION_FAILED",
                server_id=server_id,
                code=c.last_error,
            )

    async def invoke(self, server_id, method, *args, **kwargs):
        c = self.connections.get(server_id)
        if (
            not c
            or c.status not in {S.CONNECTED, S.DEGRADED}
            or not c.client
            or not self.config(server_id).enabled
        ):
            raise MCPError("MCP_SERVER_UNAVAILABLE")
        task = asyncio.current_task()
        c.calls.add(task)
        try:
            async with c.semaphore:
                async with asyncio.timeout(c.config.call_timeout):
                    return await getattr(c.client, method)(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = error_code(exc)
            if code == "MCP_CONNECTION_TIMEOUT":
                code = "MCP_TOOL_TIMEOUT"
            c.last_error = code
            c.status = S.AUTH_REQUIRED if code == "MCP_AUTH_REQUIRED" else S.DEGRADED
            raise MCPError(code) from None
        finally:
            c.calls.discard(task)

    async def disconnect(self, server_id):
        c = self.connections.get(server_id)
        if c:
            c.stop.set()
            for task in list(c.calls):
                if task is not asyncio.current_task():
                    task.cancel()
            if c.owner:
                await asyncio.wait_for(asyncio.shield(c.owner), 10)
                c.owner = None

    async def remove(self, server_id):
        await self.disconnect(server_id)
        self.credentials.pop(server_id, None)
        self.store.delete("server", server_id)
        for row in self.store.listing("tool"):
            if row["server_id"] == server_id:
                self.store.delete("tool", row["id"])

    async def close(self):
        self.closing = True
        await asyncio.gather(
            *(self.disconnect(key) for key in list(self.connections)), return_exceptions=True
        )
        self.credentials.clear()
