"""Stable external identities; server annotations never define local permission."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from jsonschema import Draft202012Validator, ValidationError, SchemaError

from app.services.mcp_ext.contracts import MCPError, RISKS, bounded_json, digest
from app.services.mcp_ext.events import emit


def validate_schema(schema):
    bounded_json(schema, limit=24000)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise MCPError("MCP_SCHEMA_UNSUPPORTED")

    def walk(item):
        if isinstance(item, dict):
            # No remote reference resolution or unbounded regular-expression work.
            if any(
                key in item
                for key in ("$ref", "$dynamicRef", "pattern", "patternProperties", "format")
            ):
                raise MCPError("MCP_SCHEMA_UNSUPPORTED")
            for value in item.values():
                walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise MCPError("MCP_SCHEMA_UNSUPPORTED") from None


def identity(server_id, name):
    return "mcp." + digest([server_id, name])[:40]


class ExternalToolRegistry:
    def __init__(self, store, connections):
        self.store, self.connections = store, connections
        self.tools = {}
        connections.on_discovery = self.discover

    async def discover(self, server_id, client=None):
        if client is None:
            page = await self.connections.invoke(server_id, "list_tools")
        elif client.server_capabilities.tools is None:
            return
        else:
            page = await client.list_tools()
        if len(page.tools) > 100 or page.next_cursor:
            raise MCPError("MCP_TOOL_LIMIT")
        previous = {
            row["name"]: row for row in self.store.listing("tool") if row["server_id"] == server_id
        }
        updated = {}
        for tool in page.tools:
            if (
                not isinstance(tool.name, str)
                or not 1 <= len(tool.name) <= 100
                or "\x00" in tool.name
            ):
                raise MCPError("MCP_RESULT_INVALID")
            schema = tool.input_schema
            validate_schema(schema)
            if tool.output_schema:
                validate_schema(tool.output_schema)
            key = identity(server_id, tool.name)
            fingerprint = digest([schema, tool.output_schema])
            old = previous.get(tool.name, {})
            row = {
                "server_id": server_id,
                "name": tool.name,
                "description": (tool.description or "")[:600],
                "input_schema": schema,
                "output_schema": tool.output_schema,
                "annotations": tool.annotations.model_dump(exclude_none=True)
                if tool.annotations
                else {},
                "schema_fingerprint": fingerprint,
                "enabled": old.get("enabled", False)
                and old.get("schema_fingerprint") == fingerprint,
                "risk_level": old.get("risk_level", "HIGH_RISK_ACTION"),
                "last_discovered_at": datetime.now(timezone.utc).isoformat(),
            }
            updated[key] = self.store.save("tool", row, key)
        # Publish the full new generation at once; a changed schema disables approval.
        self.tools = {k: v for k, v in self.tools.items() if v["server_id"] != server_id} | updated
        for name, row in previous.items():
            if identity(server_id, name) not in updated:
                self.store.delete("tool", row["id"])
        emit("MCP_TOOLS_DISCOVERED", server_id=server_id, observed_count=len(updated))

    def listing(self, server_id=None):
        return [
            deepcopy(row)
            for row in self.store.listing("tool")
            if server_id is None or row["server_id"] == server_id
        ]

    def configure(self, key, enabled, risk_level):
        if risk_level not in RISKS:
            raise MCPError("MCP_RISK_INVALID")
        row = self.store.get("tool", key)
        row.update(enabled=enabled, risk_level=risk_level)
        self.store.save("tool", row, key)
        if key in self.tools:
            self.tools[key] = row
        return row

    def get(self, key, fingerprint=None):
        row = self.tools.get(key)
        if not row:
            raise MCPError("MCP_TOOL_NOT_FOUND")
        # Read current preferences again so disable/revoke takes effect immediately.
        saved = self.store.get("tool", key)
        if not saved["enabled"]:
            raise MCPError("MCP_PERMISSION_DENIED", 403)
        if fingerprint and row["schema_fingerprint"] != fingerprint:
            raise MCPError("MCP_SCHEMA_CHANGED")
        c = self.connections.connections.get(row["server_id"])
        if not c or c.status not in {"CONNECTED", "DEGRADED"}:
            raise MCPError("MCP_SERVER_UNAVAILABLE")
        return saved

    def parse(self, api_name, call_id, arguments):
        key = api_name.replace("mcp_", "mcp.", 1)
        row = self.get(key)
        bounded_json(arguments, limit=16000)
        try:
            Draft202012Validator(row["input_schema"]).validate(arguments)
        except ValidationError:
            raise MCPError("MCP_ARGUMENT_INVALID") from None
        return {
            "id": call_id,
            "name": key,
            "arguments": arguments,
            "server_id": row["server_id"],
            "original_tool_name": row["name"],
            "schema_fingerprint": row["schema_fingerprint"],
            "side_effect_class": "READ_ONLY"
            if row["risk_level"].startswith("READ_ONLY_")
            else "NON_IDEMPOTENT",
            "risk_level": "high",
            "requires_confirmation": True,
        }

    def validate_call(self, call):
        row = self.get(call["name"], call.get("schema_fingerprint"))
        parsed = self.parse(call["name"].replace(".", "_"), call["id"], call["arguments"])
        if (
            parsed["side_effect_class"] != call.get("side_effect_class")
            or row["server_id"] != call.get("server_id")
            or row["name"] != call.get("original_tool_name")
        ):
            raise MCPError("MCP_SCHEMA_CHANGED")
        return row

    def candidates(self, text, *, limit=8, byte_budget=24000):
        words = set(re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]", text.lower()))
        ranked = []
        for key in self.tools:
            try:
                row = self.get(key)
            except MCPError:
                continue
            haystack = (row["name"] + " " + row["description"]).lower()
            score = sum(1 for word in words if word in haystack)
            if score or "mcp" in text.lower():
                ranked.append((-score, key, row))
        result, used = [], 0
        for _, key, row in sorted(ranked):
            item = {
                "type": "function",
                "function": {
                    "name": key.replace(".", "_"),
                    "description": "External untrusted MCP tool; permission required. "
                    + row["description"][:400],
                    "parameters": row["input_schema"],
                },
            }
            size = len(json.dumps(item).encode())
            if used + size <= byte_budget and len(result) < limit:
                result.append(item)
                used += size
        return result

    def available_names(self):
        names = set()
        for key in self.tools:
            try:
                row = self.get(key)
                names.update((key, row["name"]))
            except MCPError:
                pass
        return names
