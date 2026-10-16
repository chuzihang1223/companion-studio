"""Exact endpoint, public-address DNS pinning and bounded HTTP responses.

Resolve before each connection, then connect to that literal address with the
original TLS SNI/Host. A second DNS lookup cannot redirect traffic to a private
service. Redirects, proxies and compressed responses are intentionally excluded.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx2

from app.core.config import settings
from app.services.mcp_ext.contracts import MCPError


def endpoint(value, allow_loopback=False):
    try:
        url = urlsplit(value)
        port = url.port or (443 if url.scheme == "https" else 80)
    except ValueError as exc:
        raise MCPError("MCP_ENDPOINT_DENIED") from exc
    loop = url.hostname in {"127.0.0.1", "::1"}
    # localhost names, private ranges and the app control port cannot be opted in.
    if (
        not url.hostname
        or url.username
        or url.password
        or url.fragment
        or url.query
        or len(value) > 2048
        or "%" in (url.hostname or "")
        or port == settings.backend_port
        or (url.scheme != "https" and not (allow_loopback and loop and url.scheme == "http"))
    ):
        raise MCPError("MCP_ENDPOINT_DENIED")
    if loop and not allow_loopback:
        raise MCPError("MCP_ENDPOINT_DENIED")
    try:
        address = ipaddress.ip_address(url.hostname)
    except ValueError:
        address = None
    if address and not address.is_global and not (loop and allow_loopback):
        raise MCPError("MCP_ENDPOINT_DENIED")
    return url, port, loop


async def resolve(value, allow_loopback=False):
    url, port, loop = endpoint(value, allow_loopback)
    rows = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(url.hostname, port, type=socket.SOCK_STREAM), 4
    )
    addresses = {row[4][0] for row in rows}
    if not addresses or any(
        not ipaddress.ip_address(ip).is_global
        and not (loop and allow_loopback and ipaddress.ip_address(ip).is_loopback)
        for ip in addresses
    ):
        raise MCPError("MCP_ENDPOINT_DENIED")
    return sorted(addresses)[0]


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, source):
        self.source = source

    async def __aiter__(self):
        total = 0
        async for data in self.source:
            total += len(data)
            # A single HTTP exchange is bounded even for an endless SSE stream.
            if total > 1048576:
                raise MCPError("MCP_RESULT_INVALID")
            yield data

    async def aclose(self):
        await self.source.aclose()


class EndpointTransport(httpx2.AsyncBaseTransport):
    def __init__(self, value, allow_loopback=False):
        endpoint(value, allow_loopback)
        self.value, self.allow_loopback = value, allow_loopback
        self.inner = httpx2.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        if str(request.url) != self.value or request.method not in {"GET", "POST", "DELETE"}:
            raise MCPError("MCP_ENDPOINT_DENIED")
        ip = await resolve(self.value, self.allow_loopback)
        original = request.url
        headers = request.headers.copy()
        headers["Host"] = original.netloc.decode()
        headers["Accept-Encoding"] = "identity"
        extensions = {**request.extensions, "sni_hostname": original.host}
        pinned = httpx2.Request(
            request.method,
            original.copy_with(host=ip),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        response = await self.inner.handle_async_request(pinned)
        if response.status_code in {401, 403}:
            await response.aclose()
            raise MCPError("MCP_AUTH_REQUIRED")
        if (
            300 <= response.status_code < 400
            or response.headers.get("content-encoding", "identity") != "identity"
        ):
            await response.aclose()
            raise MCPError("MCP_ENDPOINT_DENIED")
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=BoundedStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.inner.aclose()
