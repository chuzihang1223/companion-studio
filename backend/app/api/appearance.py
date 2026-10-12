"""Serve only the two user-selected appearance files stored under UserData."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings


router = APIRouter(prefix="/api/appearance", tags=["appearance"])
THEME_FILE = settings.data_root / "themes" / "theme.json"


def _assets() -> dict:
    try:
        data = json.loads(THEME_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@router.get("")
def appearance():
    data = _assets()
    return {kind: f"/api/appearance/{kind}?v={Path(data[kind]).stem}" if kind in data else None
            for kind in ("background", "work_background", "avatar")}


@router.get("/{kind}")
def asset(kind: str):
    if kind not in {"background", "work_background", "avatar"}:
        raise HTTPException(status_code=404, detail="图片不存在")
    item = _assets().get(kind)
    root = (settings.data_root / "themes" / ("backgrounds" if kind.endswith('background') else "avatars")).resolve()
    path = (root / item).resolve() if isinstance(item, str) else root
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})
