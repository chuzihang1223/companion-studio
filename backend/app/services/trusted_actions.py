"""User-owned grants. Models cannot write this store or choose action risk."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock

from app.core.config import settings
from app.core.file_logging import file_logger
from app.services.permission_policy import PermissionPolicyEngine
from app.services.application_catalog import application_ids

KNOWN_APPS = {
    "netease",
    "qqmusic",
    "vscode",
    "notepad",
    "browser",
    "qq",
    "explorer",
    "calculator",
    "settings",
}
DEFAULTS = {
    **{f"app.launch:{app}": "always" for app in KNOWN_APPS},
    "browser.open:any": "always",
    "system.get_status": "always",
}


def action_for(call: dict, workspace: Path | None = None) -> str:
    name, args = call["name"], call.get("arguments", {})
    if call.get("coding_mode") in {"virtual_only", "preview_patch"} and name in {
        "coding.task",
        "code.create_file",
        "code.edit_file",
        "filesystem.write",
        "workspace.create",
        "workspace.write",
    }:
        return f"coding.draft:{workspace.resolve()}" if workspace else "coding.draft"
    if call.get("bulk_overwrite") and workspace:
        return f"filesystem.bulk_overwrite:{workspace.resolve()}"
    if name in {"app.launch", "app.focus", "app.detect", "app.status", "app.close"}:
        return f"{name}:{args.get('app', '')}"
    if name in {"music.search", "music.play"} and args.get("provider") in KNOWN_APPS:
        return f"app.control:{args['provider']}"
    if name in {"media.current", "media.sessions"}:
        return name + ":" + args.get("app", "netease")
    if name.startswith("media."):
        return f"app.control:{args.get('app', 'netease')}"
    if name == "coding.task":
        return f"coding.execute:{workspace.resolve()}"
    if name.startswith("browser."):
        return "browser.open:any"
    if name.startswith(("code.", "filesystem.", "workspace.")) and workspace:
        return f"{name}:{workspace.resolve()}"
    return name


def risk_for(action: str) -> str:
    if action.startswith(("app.launch:", "app.focus:", "app.detect:", "app.status:", "app.close:")):
        app = action.split(":", 1)[1]
        if app not in KNOWN_APPS and app not in application_ids():
            return "critical"
    if action.startswith(
        (
            "filesystem.delete:",
            "workspace.delete:",
            "filesystem.bulk_overwrite:",
            "code.run:",
            "coding.execute:",
            "app.close",
            "system.install",
            "system.modify",
        )
    ):
        return "high"
    if action.startswith(("system.admin", "security.", "filesystem.all")):
        return "critical"
    if action.startswith(("app.launch:", "app.focus:", "app.detect:", "app.status:")):
        app = action.split(":", 1)[1]
        return "low" if app in KNOWN_APPS or app in application_ids() else "critical"
    if action in {"browser.open:any", "system.get_status", "music.pause"}:
        return "low"
    if action.startswith(("media.current:", "media.sessions:")):
        return "low" if action.split(":", 1)[1] in {"netease", "qqmusic"} else "critical"
    if action.startswith("app.control:"):
        return "low" if action.split(":", 1)[1] in {"netease", "qqmusic"} else "high"
    if action.startswith(
        (
            "code.read_file:",
            "filesystem.read:",
            "filesystem.list:",
            "workspace.read:",
            "workspace.list:",
            "workspace.search:",
        )
    ):
        return "low"
    return "medium"


class TrustedActionStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.data_root / "trusted-actions.json"
        self.lock = RLock()
        self.tasks: dict[str, set[str]] = {}
        self.logger = file_logger("app.permissions", "permissions.log")

    def _read(self) -> dict:
        if not self.path.exists():
            return {} if os.getenv("COMPANION_CLEAN_DISTRIBUTION") == "1" else dict(DEFAULTS)
        # Corruption must not silently re-grant revoked permissions.
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))["actions"]
        except (ValueError, OSError, KeyError):
            self.logger.error("grant_store_unreadable")
            return {}

    def list(self) -> list[dict]:
        with self.lock:
            return [{"action": k, "scope": v, "risk": risk_for(k)} for k, v in self._read().items()]

    def archive_legacy(self) -> None:
        """Keep an exact copy of previous per-app preferences for recovery."""
        with self.lock:
            if not self.path.exists():
                return
            archive = self.path.with_name(self.path.stem + ".legacy-v8.json")
            try:
                with archive.open("xb") as handle:
                    handle.write(self.path.read_bytes())
            except FileExistsError:
                pass

    def decision(self, action: str, task_id: str | None = None) -> str:
        with self.lock:
            categories = self.tasks.get(task_id or "", set())
            return PermissionPolicyEngine().decide(
                action,
                risk_for(action),
                task_allowed=action in categories or action.split(":", 1)[0] in categories,
            )

    def allowed(self, action: str, task_id: str | None = None) -> bool:
        return self.decision(action, task_id) == "allow"

    def permitted(
        self, action: str, call_id: str, approved_ids: set[str], task_id: str | None = None
    ) -> bool:
        decision = self.decision(action, task_id)
        return decision == "allow" or (decision == "ask" and call_id in approved_ids)

    def finish_task(self, task_id: str | None) -> None:
        with self.lock:
            self.tasks.pop(task_id or "", None)

    def set(self, action: str, scope: str, task_id: str | None = None) -> None:
        if len(action) > 600 or scope not in {"always", "task", "deny", "revoke"}:
            raise ValueError("无效的授权设置")
        if risk_for(action) in {"high", "critical"} and scope in {"always", "task"}:
            raise ValueError("高风险操作必须每次单独确认")
        with self.lock:
            if scope == "task":
                if not task_id:
                    raise ValueError("缺少当前任务")
                self.tasks.setdefault(task_id, set()).add(action.split(":", 1)[0])
            else:
                values = self._read()
                if scope == "revoke":
                    values.pop(action, None)
                    for actions in self.tasks.values():
                        actions.discard(action)
                else:
                    values[action] = scope
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(
                        {"actions": values, "updated_at": datetime.now(timezone.utc).isoformat()},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                os.replace(tmp, self.path)
            self.logger.info("action=%s scope=%s task=%s", action, scope, task_id or "-")


trusted_actions = TrustedActionStore()
