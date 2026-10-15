"""Reads may use a project; implicit coding writes never use that project."""

from pathlib import Path
from app.core.config import settings
from app.services.agent_tools import ToolExecutor
from app.services.coding.documents import VirtualDocumentStore, content_hash

WRITES = {
    "code.create_file",
    "code.edit_file",
    "filesystem.write",
    "workspace.write",
    "workspace.create",
}
READS = {"code.read_file", "filesystem.read", "workspace.read"}


class CodingWorkspaceExecutor(ToolExecutor):
    def __init__(self, project: Path | None, mode: str, task_id: str):
        self.project, self.coding_mode, self.task_id = project, mode, task_id
        self.sandbox = settings.data_root / "agent_workspace" / "sandbox" / task_id
        super().__init__(project or self.sandbox)
        self.session_id: str | None = None
        self.documents = VirtualDocumentStore()

    def bind(self, session_id: str):
        self.session_id = session_id
        return self

    def _draft(self, relative: str):
        return self.documents.latest(self.session_id, relative) if self.session_id else None

    def execute(self, call: dict) -> dict:
        name, args = call["name"], call["arguments"]
        relative = str(args.get("path", ".")).replace("\\", "/")
        if name in READS:
            draft = self._draft(relative)
            if draft:
                return {
                    "path": relative,
                    "content": draft["content"],
                    "document_id": draft["id"],
                    "virtual": True,
                    "dirty": True,
                }
        if name in WRITES and self.coding_mode == "write_workspace" and self.project:
            draft = self._draft(relative)
            if draft and draft["content"] == args["content"]:
                return self.documents.apply(
                    self.session_id, draft["id"], self.project, relative, overwrite=True
                )
        if name in WRITES and self.coding_mode != "write_workspace":
            target = self._path(relative)
            if not self.session_id:
                raise ValueError("尚未建立草稿会话")
            existing = self._draft(relative)
            previous = existing["content"] if existing else ""
            original = ""
            if self.project and target.is_file():
                if target.stat().st_size > 100_000:
                    raise ValueError("源文件超过 100 KB")
                original = target.read_text(encoding="utf-8")
                previous = previous or original
            return self.documents.put(
                self.session_id,
                relative,
                args["content"],
                source="agent_modified"
                if existing
                else "derived_from_file"
                if original
                else "generated",
                related_file=relative if self.project and target.is_file() else None,
                previous=previous,
                base_hash=content_hash(original) if self.project and target.is_file() else None,
            )
        if (
            name in {"filesystem.delete", "workspace.delete", "workspace.rename"}
            and self.coding_mode != "write_workspace"
        ):
            raise ValueError("当前任务仅生成草稿，不会删除或重命名本地文件")
        if name == "code.run" and self.coding_mode != "write_workspace":
            return self.run_script(relative)
        return super().execute(call)

    def prepare_sandbox(self) -> Path:
        """Bounded text copy. No symlinks, credentials, dependencies or project writes."""
        from app.services.coding.codex import snapshot

        self.sandbox.mkdir(parents=True, exist_ok=True)
        runner = ToolExecutor(self.sandbox)
        if self.project:
            for relative, content in snapshot(self.project).items():
                target = runner._path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text(content, encoding="utf-8")
        if self.session_id:
            for document in self.documents.active(self.session_id):
                target = runner._path(document["title"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(document["content"], encoding="utf-8")
        return self.sandbox

    def run_script(self, relative: str, on_output=None, cancelled=None, paused=None):
        if self.coding_mode == "write_workspace":
            return self._run_python(self._path(relative), on_output, cancelled, paused)
        runner = ToolExecutor(self.prepare_sandbox())
        return {
            **runner._run_python(runner._path(relative), on_output, cancelled, paused),
            "workspace": str(self.sandbox),
            "sandbox": True,
        }
