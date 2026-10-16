"""Normalize known boundary errors without persisting arbitrary exception text."""

import asyncio
from uuid import UUID

from app.services.recovery.types import Failure, FailureCode, MESSAGES
from app.services.trace.privacy import identifier

ALIASES = {
    "APP_EXITED_BEFORE_READY": "APP_EXITED",
    "target_exited": "APP_EXITED",
    "LOCATOR_MISSING": "ELEMENT_NOT_FOUND",
    "TIMEOUT": "TOOL_TIMEOUT",
    "MODEL_ERROR": "MODEL_UNAVAILABLE",
    "user_interruption": "USER_INTERRUPTION",
    "automation_paused": "USER_INTERRUPTION",
    "desktop_locked": "DESKTOP_LOCKED",
    "task_cancelled": "TASK_CANCELLED",
}
TRANSIENT = {
    FailureCode.MCP_SERVER_UNAVAILABLE,
    FailureCode.MCP_CONNECTION_TIMEOUT,
    FailureCode.MCP_TOOL_TIMEOUT,
    FailureCode.NETWORK_TIMEOUT,
    FailureCode.TOOL_TIMEOUT,
    FailureCode.ELEMENT_NOT_FOUND,
    FailureCode.DOM_CHANGED,
    FailureCode.PAGE_CHANGED,
    FailureCode.RESOURCE_CONFLICT,
}


class FailureClassifier:
    def classify(self, error=None, *, result=None, tool_name, task_id, step_id, trace_id=None):
        result = result or {}
        raw = result.get("error_code") or result.get("code")
        candidates = {**{c.value: c.value for c in FailureCode}, **ALIASES}
        code = candidates.get(raw)
        if code is None:
            text = str(error or result.get("error") or "")
            # Recognition only. Never return or store the original free text.
            code = next((v for k, v in candidates.items() if k in text), None)
        if code is None:
            code = (
                "TASK_CANCELLED"
                if isinstance(error, asyncio.CancelledError)
                or type(error).__name__ == "TaskCancelled"
                else "TOOL_TIMEOUT"
                if isinstance(error, TimeoutError)
                else "TOOL_ARGUMENT_INVALID"
                if isinstance(error, (ValueError, FileNotFoundError))
                else "VERIFICATION_FAILED"
                if result.get("verified") is False
                else "UNKNOWN_ERROR"
            )
        code = FailureCode(code)
        transient = code in TRANSIENT
        return Failure(
            error_code=code,
            category="transient"
            if transient
            else "permission"
            if code == "PERMISSION_DENIED"
            else "cancelled"
            if code == "TASK_CANCELLED"
            else "state",
            message=MESSAGES[code],
            source="model" if code in {"MODEL_TIMEOUT", "MODEL_UNAVAILABLE"} else "tool",
            tool_name=identifier(tool_name) or "unknown",
            task_id=UUID(str(task_id)),
            step_id=UUID(str(step_id)),
            trace_id=UUID(str(trace_id)) if trace_id else None,
            retryable=transient,
            requires_user_approval=not transient,
            requires_reobservation=code not in {"PERMISSION_DENIED", "TASK_CANCELLED"},
        )
