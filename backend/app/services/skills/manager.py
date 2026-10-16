"""Bounded snapshots and progressive disclosure; import never executes scripts."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
from uuid import uuid4

from app.core.config import settings
from app.services.mcp_ext.contracts import MCPError, digest
from app.services.mcp_ext.events import emit
from app.services.skills.parser import parse_skill, skill_name

CAPABILITIES = {
    "read_project_files": {"workspace.read", "filesystem.read", "code.read_file"},
    "search_code": {"workspace.search"},
    "write_report": {"workspace.create", "workspace.write"},
    "run_tests": {"code.run"},
}


def disk_path(value):
    """Use Win32 extended paths without changing system long-path policy.

    Isolated profiles and immutable Skill revisions can exceed MAX_PATH. Keep
    this prefix internal; persisted paths remain relative to the owned root.
    """
    path = Path(value)
    if os.name != "nt" or not path.is_absolute():
        return path
    raw = os.path.abspath(path)
    if raw.startswith("\\\\?\\"):
        return Path(raw)
    return Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)


def safe_file(root, relative):
    parts = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or parts.is_absolute()
        or any(p in {"", ".", ".."} for p in parts.parts)
    ):
        raise MCPError("SKILL_PATH_DENIED")
    target = root.joinpath(*parts.parts)
    for ancestor in [target, *target.parents]:
        if ancestor == root.parent:
            break
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise MCPError("SKILL_PATH_DENIED")
    if not target.resolve().is_relative_to(root.resolve()):
        raise MCPError("SKILL_PATH_DENIED")
    return target


def snapshot(folder):
    root = disk_path(folder)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise MCPError("SKILL_PATH_DENIED")
    files, size = {}, 0
    for parent, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            relative = (Path(parent) / name).relative_to(root).as_posix()
            path = safe_file(root, relative)
            if path.is_dir():
                continue
            if len(files) >= 128 or not path.is_file() or path.stat().st_size > 1_000_000:
                raise MCPError("SKILL_SIZE_LIMIT")
            # Credential/config directories are never imported as a Skill asset.
            if any(
                p.lower() in {".git", ".env", ".ssh", ".aws", "userdata", "node_modules", ".venv"}
                for p in Path(relative).parts
            ):
                raise MCPError("SKILL_PRIVATE_FILE_DENIED")
            data = path.read_bytes()
            size += len(data)
            if size > 4_000_000:
                raise MCPError("SKILL_SIZE_LIMIT")
            files[relative] = data
    try:
        meta = parse_skill(files["SKILL.md"].decode("utf-8"), root.name)
    except (KeyError, UnicodeError):
        raise MCPError("SKILL_INVALID") from None
    manifest = [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    return files, meta, manifest, digest(manifest)


class AgentSkillManager:
    def __init__(self, store, root=None):
        self.store = store
        self.root = disk_path(root or settings.data_root / "extensions" / "skills")
        self.root.mkdir(parents=True, exist_ok=True)

    def listing(self):
        return self.store.listing("skill")

    def preview(self, folder):
        _, meta, manifest, content_hash = snapshot(folder)
        return {
            **meta,
            "files": manifest,
            "content_hash": content_hash,
            "trusted_state": "untrusted",
            "scripts_executable": False,
        }

    def import_folder(self, folder, expected_hash, *, skill_id=None, own=False):
        files, meta, manifest, content_hash = snapshot(folder)
        if content_hash != expected_hash:
            raise MCPError("SKILL_CHANGED_SINCE_PREVIEW")
        if any(row["name"] == meta["name"] and row["id"] != skill_id for row in self.listing()):
            raise MCPError("SKILL_NAME_CONFLICT")
        if skill_id:
            old = self.store.get("skill", skill_id)
            if old["name"] != meta["name"] or own and old["source"] != "self-authored":
                raise MCPError("SKILL_PERMISSION_DENIED")
        key = skill_id or str(uuid4())
        # Each update is immutable; DB points to the new snapshot only after all writes.
        relative = f"{key}/{uuid4().hex}/{meta['name']}"
        destination = self.root / relative
        destination.mkdir(parents=True, exist_ok=False)
        try:
            for name, data in files.items():
                target = destination.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            row = {k: v for k, v in meta.items() if k != "instructions"}
            row.update(
                version=meta["metadata"].get("version", "unversioned"),
                source="self-authored" if own else "local-import",
                enabled=False,
                trusted_state="untrusted",
                files=manifest,
                content_hash=content_hash,
                installation_path=relative,
                last_updated=datetime.now(timezone.utc).isoformat(),
            )
            return self.store.save("skill", row, key)
        except BaseException:
            # Only our just-created immutable snapshot is removed on failure.
            shutil.rmtree(destination)
            raise

    def detail(self, key):
        row = self.store.get("skill", key)
        root = self.root / row["installation_path"]
        raw = self.read_file(key, "SKILL.md")
        return {
            **row,
            "instructions": parse_skill(raw, row["name"])["instructions"],
            "scripts_executable": False,
        }

    def read_file(self, key, name):
        row = self.store.get("skill", key)
        manifest = next((f for f in row["files"] if f["path"] == name), None)
        if not manifest:
            raise MCPError("SKILL_PATH_DENIED")
        root = self.root / row["installation_path"]
        path = safe_file(root, name)
        data = path.read_bytes()
        if len(data) > 32000 or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
            raise MCPError("SKILL_CONTENT_CHANGED")
        try:
            return data.decode("utf-8")
        except UnicodeError:
            raise MCPError("SKILL_BINARY_PREVIEW_UNSUPPORTED") from None

    def enable(self, key, enabled):
        row = self.store.get("skill", key)
        if enabled:
            self.detail(key)  # Verify bytes before allowing future matches.
        row["enabled"] = enabled
        return self.store.save("skill", row, key)

    def delete(self, key):
        # Delete the registration. Immutable files stay local for manual recovery;
        # no user-selected source folder is ever deleted or overwritten.
        self.store.delete("skill", key)

    def dependencies(self, row, names):
        missing = sorted(set(row.get("required_tools", [])) - names)
        for capability in row.get("required_capabilities", []):
            if not CAPABILITIES.get(capability, set()) & names:
                missing.append(capability)
        return missing

    def validate_selection(self, selected):
        for item in selected or []:
            row = self.store.get("skill", item["skill_id"])
            if not row["enabled"] or row["content_hash"] != item.get("content_hash"):
                raise MCPError("SKILL_PERMISSION_DENIED")
            self.detail(row["id"])

    def select(self, text, names):
        started = time.perf_counter()
        words = set(re.findall(r"[a-z0-9_-]+|[\u4e00-\u9fff]", text.lower()))
        ranked = []
        for row in self.listing():
            if not row["enabled"]:
                continue
            explicit = "$" + row["name"] in text or "skill:" + row["name"] in text
            score = sum(word in (row["description"] + " " + row["name"]).lower() for word in words)
            if explicit or score >= 4:
                ranked.append((not explicit, -score, row["id"], row))
        if not ranked:
            return {"skills": [], "context": "", "missing": [], "load_ms": 0}
        # One deterministic winner; do not silently combine conflicting instructions.
        row = sorted(ranked, key=lambda r: r[:3])[0][3]
        emit("SKILL_SELECTED", skill_id=row["id"])
        missing = self.dependencies(row, names)
        if missing:
            emit("SKILL_EXECUTION_BLOCKED", skill_id=row["id"], code="SKILL_DEPENDENCY_MISSING")
            return {
                "skills": [{"skill_id": row["id"], "name": row["name"]}],
                "context": "",
                "missing": missing,
                "load_ms": (time.perf_counter() - started) * 1000,
            }
        instructions = self.detail(row["id"])["instructions"][:16000]
        emit("SKILL_LOADED", skill_id=row["id"])
        return {
            "skills": [
                {"skill_id": row["id"], "name": row["name"], "content_hash": row["content_hash"]}
            ],
            "context": instructions,
            "missing": [],
            "load_ms": (time.perf_counter() - started) * 1000,
        }

    def create(self, name, text, *, skill_id=None):
        if not skill_name(name):
            raise MCPError("SKILL_INVALID")
        parse_skill(text, name)
        stage = self.root / "_drafts" / uuid4().hex / name
        stage.mkdir(parents=True, exist_ok=False)
        try:
            (stage / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
            preview = self.preview(stage)
            return self.import_folder(stage, preview["content_hash"], skill_id=skill_id, own=True)
        finally:
            shutil.rmtree(stage.parent)
