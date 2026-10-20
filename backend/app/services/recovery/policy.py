"""Permission-independent recovery eligibility; execution still checks current grants."""

from app.services.recovery.types import RecoveryDecision

READ_ONLY = {
    "workflow.list",
    "scheduler.list",
    "scheduler.status",
    "computer.observe",
    "computer.find_element",
    "computer.wait_for_state",
    "knowledge.collections",
    "knowledge.search",
    "knowledge.get_document",
    "knowledge.get_chunk",
    "skill.read_reference",
    "app.detect",
    "app.status",
    "system.get_status",
    "media.current",
    "media.sessions",
    "workspace.list",
    "workspace.search",
    "workspace.read",
    "filesystem.read",
    "filesystem.list",
    "code.read_file",
    "browser.observe",
}
FILE_WRITES = {
    "code.create_file",
    "code.edit_file",
    "filesystem.write",
    "workspace.write",
    "workspace.create",
}
FILE_READS = {"workspace.read", "filesystem.read", "code.read_file"}


def side_effect_class(name, call=None):
    if name.startswith("mcp."):
        return (
            "READ_ONLY"
            if call and call.get("side_effect_class") == "READ_ONLY"
            else "NON_IDEMPOTENT"
        )
    return (
        "READ_ONLY"
        if name in READ_ONLY
        else "RECONCILABLE"
        if name in FILE_WRITES
        else "NON_IDEMPOTENT"
    )


class RecoveryPolicyEngine:
    def decide(self, failure, tool_name, *, retry_count=0, recovery_count=0, budget, call=None):
        code = failure.error_code
        if code == "TASK_CANCELLED":
            return RecoveryDecision(level=3, strategy="cancel", stop=True)
        if code in {
            "PERMISSION_DENIED",
            "TOOL_ARGUMENT_INVALID",
            "MODEL_UNAVAILABLE",
            "MODEL_TIMEOUT",
        }:
            return RecoveryDecision(level=3, strategy="stop", stop=True, approval=True)
        if retry_count >= budget.max_retry_per_step or recovery_count >= budget.max_recovery_cycles:
            return RecoveryDecision(level=3, strategy="budget_exhausted", stop=True)
        if (
            tool_name in READ_ONLY
            or tool_name.startswith("mcp.")
            and side_effect_class(tool_name, call) == "READ_ONLY"
        ) and failure.retryable:
            return RecoveryDecision(level=1, strategy="retry_read", retry=True, observe=True)
        if code in {"DOM_CHANGED", "ELEMENT_NOT_FOUND", "PAGE_CHANGED"} and tool_name.startswith(
            "browser."
        ):
            return RecoveryDecision(level=2, strategy="observe_page", observe=True, approval=True)
        if code in {
            "USER_INTERRUPTION",
            "DESKTOP_LOCKED",
            "APP_EXITED",
            "TARGET_WINDOW_CLOSED",
            "WINDOW_NOT_FOUND",
        }:
            return RecoveryDecision(
                level=3, strategy="takeover", observe=True, approval=True, stop=True
            )
        if tool_name in FILE_WRITES:
            return RecoveryDecision(level=2, strategy="reconcile_file", observe=True, approval=True)
        return RecoveryDecision(
            level=3, strategy="inspect_side_effect", observe=True, approval=True, stop=True
        )
