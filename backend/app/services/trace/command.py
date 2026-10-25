"""Versioned JSON only: journal records contain the already filtered projection."""

from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID


COMMANDS = {"create", "bind", "state", "segment", "finish", "span_start", "span_finish", "event"}
MAX_PAYLOAD = 32_768


def encode(command, data):
    if command not in COMMANDS:
        raise ValueError("TRACE_COMMAND_INVALID")
    clean = {key: value for key, value in data.items() if not key.startswith("_projection_")}
    if isinstance(clean.get("created_at"), datetime):
        clean["created_at"] = clean["created_at"].isoformat()
    payload = json.dumps(
        {"version": 1, "command": command, "data": clean},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(payload.encode("utf-8")) > MAX_PAYLOAD:
        raise ValueError("TRACE_COMMAND_TOO_LARGE")
    return payload, sha256(payload.encode()).hexdigest()


def decode(payload, digest):
    if not isinstance(payload, str) or not isinstance(digest, str):
        raise ValueError("TRACE_JOURNAL_SCHEMA")
    if len(payload.encode("utf-8")) > MAX_PAYLOAD or sha256(payload.encode()).hexdigest() != digest:
        raise ValueError("TRACE_JOURNAL_CHECKSUM")
    row = json.loads(payload)
    if (
        not isinstance(row, dict)
        or row.get("version") != 1
        or row.get("command") not in COMMANDS
        or not isinstance(row.get("data"), dict)
    ):
        raise ValueError("TRACE_JOURNAL_SCHEMA")
    data = row["data"]
    if not isinstance(data.get("trace_id"), str) or not isinstance(data.get("created_at"), str):
        raise ValueError("TRACE_JOURNAL_SCHEMA")
    for field in (
        "trace_id",
        "task_id",
        "conversation_id",
        "work_session_id",
        "span_id",
        "parent_span_id",
    ):
        if data.get(field) is not None:
            if not isinstance(data[field], str):
                raise ValueError("TRACE_JOURNAL_SCHEMA")
            UUID(data[field])
    if any(key.startswith("_projection_") for key in data):
        raise ValueError("TRACE_JOURNAL_SCHEMA")
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    return row["command"], data
