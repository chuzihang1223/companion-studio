"""User-editable capabilities; models cannot change these preferences."""

import json
import os
from app.core.config import settings

DEFAULTS = {
    "automation_enabled": True,
    "auto_work_mode": True,
    "music_player": "netease",
    "coding_provider": "internal",
    "permission_mode": "smart",
    "browser_media_enabled": False,
}


def read_preferences() -> dict:
    path = settings.data_root / "agent-preferences.json"
    if not path.exists():
        return dict(DEFAULTS)
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("Invalid preferences")
        result = {**DEFAULTS, **{key: values[key] for key in DEFAULTS if key in values}}
        if result["permission_mode"] not in {"automatic", "smart", "ask"}:
            result["permission_mode"] = "ask"
        for key in ("automation_enabled", "auto_work_mode", "browser_media_enabled"):
            if not isinstance(result[key], bool):
                result[key] = False
        return result
    except (ValueError, OSError, TypeError):
        return {**DEFAULTS, "automation_enabled": False}


def write_preferences(values: dict) -> dict:
    if set(values) - set(DEFAULTS):
        raise ValueError("未知工作设置")
    for key in ("automation_enabled", "auto_work_mode", "browser_media_enabled"):
        if key in values and not isinstance(values[key], bool):
            raise ValueError("无效开关")
    if values.get("music_player", "netease") not in {"netease", "qqmusic"}:
        raise ValueError("无效播放器")
    if values.get("coding_provider", "internal") not in {"internal", "codex"}:
        raise ValueError("无效代码智能体")
    if values.get("permission_mode", "smart") not in {"automatic", "smart", "ask"}:
        raise ValueError("无效权限模式")
    result = {**read_preferences(), **values}
    path = settings.data_root / "agent-preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)
    return result
