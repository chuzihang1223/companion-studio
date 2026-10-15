"""Bounded project browsing. The UI and Agent use the same path guard."""

import os
from collections import deque
from pathlib import Path

from app.core.config import settings
from app.services.workspace_paths import scoped_path

IGNORED = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "cache",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".next",
    "coverage",
}
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".json",
    ".html",
    ".css",
    ".toml",
    ".yaml",
    ".yml",
    ".csv",
    ".xml",
    ".jsx",
}


def validate_root(value: str) -> str:
    if not value.strip() or not Path(value).is_absolute():
        raise ValueError("请选择具体的项目文件夹")
    root = Path(value).resolve()
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError("请选择具体的项目文件夹")
    data = settings.data_root.resolve()
    if data.is_relative_to(root) or (
        root.is_relative_to(data) and not root.is_relative_to(data / "agent_workspace")
    ):
        raise ValueError("项目不能包含或位于应用聊天、配置与密钥目录")
    scoped_path(root, ".")
    return str(root)


def tree(root: str, relative: str = ".", show_ignored: bool = False) -> dict:
    workspace = Path(validate_root(root))
    folder = scoped_path(workspace, relative)
    if not folder.is_dir():
        raise ValueError("目标不是文件夹")
    items, inspected, truncated = [], 0, False
    with os.scandir(folder) as entries:
        for entry in entries:
            inspected += 1
            if inspected > 2000 or len(items) >= 200:
                truncated = True
                break
            path = Path(entry.path)
            relative_path = str(path.relative_to(workspace))
            try:
                scoped_path(workspace, relative_path)
                ignored = entry.name.lower() in IGNORED
                if ignored and not show_ignored:
                    continue
                items.append(
                    {
                        "name": entry.name,
                        "path": relative_path,
                        "directory": entry.is_dir(),
                        "ignored": ignored,
                        "size": entry.stat().st_size if entry.is_file() else None,
                    }
                )
            except (ValueError, OSError):
                continue
    items.sort(key=lambda item: (not item["directory"], item["name"].lower()))
    return {
        "root": str(workspace),
        "path": relative,
        "items": items,
        "limit": 200,
        "inspected": inspected,
        "truncated": truncated,
    }


def search(root: str, query: str) -> dict:
    workspace = Path(validate_root(root))
    if not query.strip() or len(query) > 200:
        raise ValueError("请输入不超过 200 字的搜索词")
    matches, queue, visited, inspected = [], deque([workspace]), {workspace}, 0
    truncated = False
    while queue and inspected < 2000 and len(matches) < 100:
        folder = queue.popleft()
        try:
            entries = os.scandir(folder)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if inspected >= 2000 or len(matches) >= 100:
                    truncated = True
                    break
                inspected += 1
                relative = str(Path(entry.path).relative_to(workspace))
                try:
                    path = scoped_path(workspace, relative)
                    if entry.is_dir():
                        if entry.name.lower() not in IGNORED and path not in visited:
                            visited.add(path)
                            queue.append(path)
                        continue
                    if query.lower() in relative.lower():
                        matches.append({"path": relative, "match": "文件名"})
                    elif path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 16_384:
                        content = path.read_text(encoding="utf-8", errors="replace")
                        for number, line in enumerate(content.splitlines(), 1):
                            if query.lower() in line.lower():
                                matches.append(
                                    {"path": relative, "line": number, "text": line[:200]}
                                )
                                break
                except (ValueError, OSError):
                    continue
    return {"matches": matches, "inspected": inspected, "truncated": truncated or bool(queue)}
