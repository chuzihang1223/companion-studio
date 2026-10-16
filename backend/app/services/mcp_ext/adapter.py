"""One MCP call, no tool replay. External acknowledgement is not goal proof."""

import asyncio
import re
import time

from app.services.mcp_ext.contracts import MCPError, bounded_json
from app.services.mcp_ext.events import emit
from app.services.trace.scope import current_trace

SECRET = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:sk-|ghp_|github_pat_)\S+|(?:api[_-]?key|password|token)\s*[:=]\s*\S+)"
)


def clean(value, secrets=()):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        return SECRET.sub("[redacted]", value)
    if isinstance(value, list):
        return [clean(item, secrets) for item in value]
    if isinstance(value, dict):
        return {
            k: "[redacted]"
            if re.search(r"(?i)token|password|secret|api.?key", k)
            else clean(v, secrets)
            for k, v in value.items()
        }
    return value


def normalize(result, *, secrets=()):
    raw = bounded_json(result.model_dump(mode="json", by_alias=True, exclude_none=True))
    texts, skipped = [], 0
    for block in raw.get("content", [])[:8]:
        if block.get("type") == "text":
            texts.append(block.get("text", "")[:4000])
        elif block.get("type") == "resource" and isinstance(
            block.get("resource", {}).get("text"), str
        ):
            texts.append(block["resource"]["text"][:4000])
        else:
            # Binary, images and links are not fetched or passed as implicit attachments.
            skipped += 1
    summary = "\n".join(texts)[:8000]
    return {
        "content_summary": clean(summary, secrets),
        "structured_data": clean(raw.get("structuredContent"), secrets),
        "incomplete": skipped > 0
        or len(raw.get("content", [])) > 8
        or len("\n".join(texts)) > 8000,
        "is_error": raw.get("isError", False),
        "trust": "untrusted_external_data",
    }


class MCPToolAdapter:
    def __init__(self, connections, registry):
        self.connections, self.registry = connections, registry

    async def execute(self, call):
        started = time.perf_counter()
        trace = current_trace.get()
        result = {
            "tool_call_id": call["id"],
            "server_id": call.get("server_id"),
            "tool_name": call.get("original_tool_name"),
            "status": "failed",
            "verified": False,
            "content_summary": "",
            "structured_data": None,
            "error_code": None,
            "error_message": None,
            "side_effect_class": call.get("side_effect_class"),
            "evidence_refs": [],
            "trace_id": trace.trace_id if trace else None,
            "goal_verified": False,
            "trust": "untrusted_external_data",
            "request_status": "not_sent",
        }
        emit(
            "MCP_TOOL_STARTED",
            server_id=call.get("server_id"),
            tool_id=call["name"],
            tool_call_id=call["id"],
        )
        try:
            row = self.registry.validate_call(call)
            # Refresh before execution to detect changed schemas even without notifications.
            await self.registry.discover(row["server_id"])
            row = self.registry.validate_call(call)
            result["request_status"] = "dispatched"
            response = await self.connections.invoke(
                row["server_id"], "call_tool", row["name"], call["arguments"]
            )
            result["request_status"] = "response_received"
            creds = self.connections.credentials.get(row["server_id"], {})
            secrets = [creds.get("token", ""), *creds.get("environment", {}).values()]
            result.update(normalize(response, secrets=secrets))
            if result.pop("is_error"):
                code = (
                    "MCP_SIDE_EFFECT_UNKNOWN"
                    if call["side_effect_class"] != "READ_ONLY"
                    else "MCP_RESULT_INVALID"
                )
                # Only our local fixed transient code is recognized; never server risk claims.
                if (
                    call["side_effect_class"] == "READ_ONLY"
                    and result["content_summary"] == "MCP_SERVER_UNAVAILABLE"
                ):
                    code = "MCP_SERVER_UNAVAILABLE"
                raise MCPError(code)
            if row.get("output_schema") and result["structured_data"] is not None:
                from jsonschema import Draft202012Validator, ValidationError

                try:
                    Draft202012Validator(row["output_schema"]).validate(result["structured_data"])
                except ValidationError:
                    raise MCPError() from None
            result["protocol_success"] = True
            if call["side_effect_class"] != "READ_ONLY":
                raise MCPError("MCP_SIDE_EFFECT_UNKNOWN")
            result.update(
                status="completed", verified=not result["incomplete"], protocol_success=True
            )
        except asyncio.CancelledError:
            emit(
                "MCP_TOOL_FAILED",
                server_id=call.get("server_id"),
                tool_id=call["name"],
                code="TASK_CANCELLED",
            )
            raise
        except MCPError as exc:
            code = exc.code
            if call.get("side_effect_class") != "READ_ONLY" and code in {
                "MCP_TOOL_TIMEOUT",
                "MCP_SERVER_UNAVAILABLE",
                "MCP_CONNECTION_TIMEOUT",
            }:
                code = "MCP_SIDE_EFFECT_UNKNOWN"
            result.update(error_code=code, error_message=code, error=code, code=code)
        result["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        c = self.connections.connections.get(call.get("server_id"))
        if c:
            c.history.append(
                {
                    "tool_id": call["name"],
                    "status": result["status"],
                    "error_code": result["error_code"],
                    "duration_ms": result["duration_ms"],
                }
            )
        emit(
            "MCP_TOOL_COMPLETED" if result["status"] == "completed" else "MCP_TOOL_FAILED",
            server_id=call.get("server_id"),
            tool_id=call["name"],
            tool_call_id=call["id"],
            code=result["error_code"],
            duration_ms=result["duration_ms"],
            verified=result["verified"],
        )
        return result
