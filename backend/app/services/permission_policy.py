"""由用户偏好、动作类别和本地风险决定权限；模型不能选择模式。"""

from app.services.desktop.preferences import read_preferences


class PermissionPolicyEngine:
    """输出 allow / ask / deny，不执行操作，也不持久化确认结果。"""

    def decide(self, action: str, risk: str, *, task_allowed: bool = False) -> str:
        """关键风险拒绝，高风险始终询问，其余结合只读及任务级授权。

        task_allowed 只覆盖本任务的低/中风险操作。智能和自动模式都不
        绕过高风险确认；本方法不使用模型声称的权限或旧版持久授权。
        """
        mode = read_preferences()["permission_mode"]
        category = action.split(":", 1)[0]
        if category == "mcp.execute":
            # External tools never inherit built-in readonly or automatic trust.
            return "ask"
        # Critical includes unknown executables and attempts at security/admin access.
        if risk not in {"low", "medium", "high"}:
            return "deny"
        if risk == "high":
            return "ask"
        readonly = category in {
            "workflow.list",
            "scheduler.list",
            "scheduler.status",
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
            "code.read_file",
            "filesystem.read",
            "filesystem.list",
            "workspace.read",
            "workspace.list",
            "workspace.search",
            "browser.observe",
        }
        if readonly:
            return "allow"
        if task_allowed and risk not in {"high", "critical"}:
            return "allow"
        if mode == "ask":
            return "ask"
        if mode == "smart":
            return "ask" if risk == "high" else "allow"
        # High-risk execution already returned ask above in every mode.
        # No UAC interaction or system-permission bypass is implemented.
        if category in {
            "filesystem.delete",
            "workspace.delete",
            "app.close",
            "system.install",
            "system.modify",
        }:
            return "ask"
        return "allow"
