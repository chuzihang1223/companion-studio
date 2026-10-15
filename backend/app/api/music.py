"""Local music catalog and range-capable media delivery."""

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.llm_settings import require_control
from app.services.music_library import MIME


router = APIRouter(prefix="/api/music", tags=["music"])


class MusicFolder(BaseModel):
    folder: str


@router.get("/library")
def library(request: Request):
    service = request.app.state.music_library
    return {"folder": str(service.folder) if service.folder else None, "tracks": service.scan()}


@router.post("/folder")
def select_folder(
    request: Request,
    payload: MusicFolder,
    x_companion_control_token: str | None = Header(default=None),
):
    require_control(x_companion_control_token)
    try:
        rows = request.app.state.music_library.choose(payload.folder)
        return {"folder": str(request.app.state.music_library.folder), "tracks": rows}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/audio/{identifier}")
def audio(request: Request, identifier: str):
    path = request.app.state.music_library.path(identifier)
    if path is None:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    return FileResponse(
        path, media_type=MIME[path.suffix.lower()], headers={"Cache-Control": "private, no-store"}
    )
