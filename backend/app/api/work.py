from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.api.llm_settings import require_control
from app.services.workspace_service import validate_root, tree, search
from app.services.agent_tools import ToolExecutor
from pathlib import Path

router = APIRouter(prefix="/api/work", tags=["work"])


class WorkspaceChange(BaseModel):
    conversation_id: str = Field(max_length=100)
    root: str | None = Field(default=None, max_length=500)


@router.post("/workspace")
def set_workspace(
    request: Request,
    payload: WorkspaceChange,
    x_companion_control_token: str | None = Header(default=None),
):
    require_control(x_companion_control_token)
    try:
        root = validate_root(payload.root) if payload.root else None
        session = request.app.state.work_state.ensure(payload.conversation_id)
        request.app.state.work_state.set_workspace(session, root)
        return request.app.state.work_state.snapshot(session)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{session_id}/files")
def workspace_files(
    request: Request,
    session_id: str,
    path: str = ".",
    query: str | None = None,
    read: bool = False,
    show_ignored: bool = False,
    x_companion_control_token: str | None = Header(default=None),
):
    require_control(x_companion_control_token)
    try:
        root = request.app.state.work_state.snapshot(session_id)["workspace_root"]
        if not root:
            raise ValueError("尚未选择工作文件夹")
        if query is not None:
            return search(root, query)
        if read:
            return ToolExecutor(Path(root)).execute(
                {"name": "workspace.read", "arguments": {"path": path}}
            )
        return tree(root, path, show_ignored)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


class TabCreate(BaseModel):
    kind: str
    title: str = Field(min_length=1, max_length=100)
    data: dict = Field(default_factory=dict)


class TabUpdate(BaseModel):
    data: dict = Field(default_factory=dict)


@router.patch("/{session_id}/tabs/{tab_id}")
def update_tab(request: Request, session_id: str, tab_id: str, payload: TabUpdate):
    try:
        request.app.state.work_state.update_tab(session_id, tab_id, payload.data)
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {"ok": True}


@router.delete("/{session_id}/tabs/{tab_id}")
def close_tab(request: Request, session_id: str, tab_id: str):
    try:
        request.app.state.work_state.update_tab(session_id, tab_id, None)
    except LookupError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/by-conversation/{conversation_id}")
def by_conversation(request: Request, conversation_id: str):
    try:
        return request.app.state.work_state.for_conversation(conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}")
def snapshot(request: Request, session_id: str):
    try:
        return request.app.state.work_state.snapshot(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/events")
async def events(
    request: Request,
    session_id: str,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
):
    try:
        request.app.state.work_state.snapshot(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    resume = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    return StreamingResponse(
        request.app.state.work_state.stream(session_id, max(after, resume)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/{session_id}/stop")
def stop(request: Request, session_id: str):
    if getattr(request.app.state, "task_runtime", None):
        request.app.state.task_runtime.control(session_id, "cancel")
    request.app.state.work_state.stop(session_id)
    return {"status": "stopped"}


class TaskControl(BaseModel):
    action: str = Field(pattern="^(pause|resume|takeover|cancel)$")


@router.post("/{session_id}/control")
def control_task(request: Request, session_id: str, payload: TaskControl):
    request.app.state.task_runtime.control(session_id, payload.action)
    return {"status": request.app.state.work_state.status(session_id)}


@router.post("/{session_id}/tabs")
def add_tab(request: Request, session_id: str, payload: TabCreate):
    try:
        tab_id = request.app.state.work_state.add_tab(
            session_id, payload.kind, payload.title, payload.data
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": tab_id}
