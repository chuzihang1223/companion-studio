"""One boundary for project reads, writes and directory browsing."""

from pathlib import Path, PureWindowsPath

from app.core.config import settings

PRIVATE_PARTS = {
    ".codex",
    ".aws",
    ".ssh",
    ".azure",
    ".config",
    "auth.json",
    "credentials",
    "credentials.json",
    "secrets.json",
    "api-presets.json",
    "api-keys.json",
    "fish-key.json",
    "fish-settings.json",
    "id_rsa",
    "id_ed25519",
}


def private_path(path: Path) -> bool:
    return any(
        part.lower().startswith(".env") or part.lower() in PRIVATE_PARTS for part in path.parts
    )


def scoped_path(workspace: Path, value: str) -> Path:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError("无效的工作区路径")
    source = Path(value)
    windows = PureWindowsPath(value)
    if (
        source.is_absolute()
        or windows.drive
        or windows.root
        or ".." in source.parts
        or ".." in windows.parts
    ):
        raise ValueError("仅允许工作文件夹内的相对路径")
    if "\x00" in value or ":" in value:
        raise ValueError("无效的工作区路径")
    root = workspace.resolve()
    result = (root / source).resolve()
    if not result.is_relative_to(root):
        raise ValueError("路径超出工作文件夹")
    if private_path(source) or private_path(result):
        raise ValueError("密钥和账号配置不能通过文件工具访问")
    data = settings.data_root.resolve()
    if result.is_relative_to(data) and not result.is_relative_to(data / "agent_workspace"):
        raise ValueError("应用配置、密钥、权限记录和聊天数据不能通过文件工具访问")
    return result
