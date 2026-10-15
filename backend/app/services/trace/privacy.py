"""Allowlisted summaries, applied before enqueue AND when reading/exporting.

Never attempt to redact an arbitrary prompt/result recursively and then store it.
Unknown keys and all original free text are discarded instead.
"""

import math
import re
from uuid import UUID

from app.services.trace.types import ERRORS

SENSITIVE = re.compile(
    r"(?i)(sk[-_]|gh[pousr]_|github_pat_|bearer|password|secret|token|api.?key|[a-z]:[\\/]|https?://|@|\\\\)"
)
NUMBERS = {
    "step_count",
    "attempt",
    "approved_count",
    "parameter_count",
    "character_count",
    "max_concurrency",
    "queue_depth",
    "observed_count",
    "duration_ms",
    "dropped_events",
}
BOOLEANS = {
    "requires_permission",
    "side_effect",
    "executed",
    "verified",
    "focused",
    "window_ready",
    "responding",
    "installed",
    "running",
    "evidence_available",
    "legacy",
    "incomplete",
    "streaming",
    "tools_requested",
    "successful",
    "queued",
}
ENUMS = {
    "strategy": {
        "retry_read",
        "stop",
        "cancel",
        "budget_exhausted",
        "observe_page",
        "takeover",
        "reconcile_file",
        "inspect_side_effect",
        "budget_or_permission",
        "refresh_known_search",
        "refocus_observed_window",
        "unchanged_observation",
        "safe_resume",
    },
    "step": {
        "启动应用",
        "检测应用",
        "观察应用",
        "聚焦应用",
        "搜索并播放歌曲",
        "搜索歌曲",
        "修改项目并验证测试",
        "读取系统状态",
        "网页搜索",
        "打开网页",
        "读取文件",
        "修改文件",
        "创建文件",
        "运行测试",
        "打开并读取网页",
        "运行项目脚本",
        "代码智能体执行与验证",
        "检测音乐客户端",
        "启动并等待窗口",
        "定位搜索框并搜索歌曲",
        "选择匹配歌曲",
        "验证歌名与播放状态",
        "媒体操作",
    },
    "operation": {"chat", "stream_chat", "plan_tools", "execute", "observe", "verify"},
    "decision": {"submitted", "allowed", "denied"},
    "verification_method": {"deterministic_result_rules", "result_flag"},
    "wait_kind": {"conversation_lock", "global_capacity", "permission", "user", "scheduler"},
    "goal_kind": {"application", "browser", "music", "coding", "file", "general"},
    "reason": {
        "process_restarted",
        "queue_full",
        "event_limit",
        "storage_failure",
        "history_unavailable",
    },
}
PARAMETERS = {
    "app",
    "provider",
    "path",
    "url",
    "query",
    "content",
    "command",
    "goal",
    "target",
    "code",
    "name",
    "old",
    "new",
    "text",
    "recursive",
    "limit",
}


def identifier(value: object, limit: int = 80) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,99}", value):
        return None
    if len(value) > limit or SENSITIVE.search(value):
        return None
    return value


def uuid_value(value: object) -> str | None:
    try:
        return str(UUID(str(value))) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def metadata(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if (
            key in NUMBERS
            and isinstance(item, (int, float))
            and not isinstance(item, bool)
            and math.isfinite(item)
        ):
            result[key] = max(0, min(item, 1_000_000_000))
        elif key in BOOLEANS and isinstance(item, bool):
            result[key] = item
        elif key in ENUMS and isinstance(item, str) and item in ENUMS[key]:
            result[key] = item
        elif key == "tool_call_id" and identifier(item, 100):
            result[key] = item
        elif key in {"step_id", "checkpoint_id", "recovery_id", "origin_task_id"} and uuid_value(
            item
        ):
            result[key] = uuid_value(item)
        elif key == "parameter_summary" and isinstance(item, dict):
            result[key] = {
                name: kind
                for name, kind in item.items()
                if name in PARAMETERS
                and isinstance(kind, str)
                and kind in {"text", "number", "boolean", "object", "array", "null"}
            }
    return result


def arguments(value: object) -> dict:
    if not isinstance(value, dict):
        return {}

    def kind(item):
        if item is None:
            return "null"
        if isinstance(item, bool):
            return "boolean"
        if isinstance(item, str):
            return "text"
        if isinstance(item, (int, float)):
            return "number"
        return "array" if isinstance(item, list) else "object"

    return {name: kind(item) for name, item in value.items() if name in PARAMETERS}


def evidence(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for row in value[:16]:
        if not isinstance(row, dict):
            continue
        name, item = row.get("name"), row.get("value")
        if not isinstance(name, str):
            continue
        if name in BOOLEANS and isinstance(item, bool):
            result.append({"kind": "boolean", "name": name, "value": item})
        elif name == "observed_count" and isinstance(item, int) and 0 <= item <= 1_000_000:
            result.append({"kind": "count", "name": name, "value": item})
    return result


def result_evidence(value: object) -> list[dict]:
    if not isinstance(value, dict):
        return []
    return evidence(
        [{"name": key, "value": item} for key, item in value.items() if key in BOOLEANS]
    )


def error_code(error: object = None, result: dict | None = None) -> str | None:
    code = (result or {}).get("code") or (result or {}).get("error_code")
    if isinstance(code, str) and code in ERRORS:
        return code
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if error is not None:
        return "TOOL_ERROR"
    return "VERIFICATION_FAILED" if (result or {}).get("verified") is False else None
