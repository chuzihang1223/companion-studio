from fastapi import APIRouter, Header, Request, HTTPException
from pydantic import BaseModel, Field
from app.api.llm_settings import require_control
from app.services.desktop.preferences import read_preferences, write_preferences

router = APIRouter(prefix='/api/desktop', tags=['desktop-internal'])


class DesktopReply(BaseModel):
    id: str = Field(max_length=100)
    result: dict = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=1000)

class DesktopObservation(BaseModel):
    session_id: str=Field(max_length=100)
    kind: str=Field(pattern='^(app|app_launch_stage|lifecycle|heartbeat)$')
    title: str=Field(max_length=100)
    data: dict


@router.get('/commands')
async def commands(request: Request, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    return await request.app.state.desktop_bridge.poll()


@router.post('/result')
async def result(request: Request, payload: DesktopReply, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    request.app.state.desktop_bridge.resolve(payload.id, payload.result, payload.error)
    return {'ok': True}


@router.get('/preferences')
def preferences():
    return read_preferences()

@router.post('/observation')
def observation(request: Request,payload:DesktopObservation,x_companion_control_token:str|None=Header(default=None)):
    require_control(x_companion_control_token)
    work=request.app.state.work_state
    if payload.kind=='lifecycle' and payload.data.get('target_exited'):
        handle=request.app.state.task_runtime.handles.get(payload.session_id)
        if handle:handle.target_exited(str(payload.data.get('code') or 'TARGET_APP_EXITED'))
        if work.status(payload.session_id) not in {'completed','done','stopped','cancelled','failed','interrupted'}:
            work.emit(payload.session_id,'target_exited',{'summary':'目标应用已关闭，本次操作已结束'},status='interrupted')
        return {'ok':True}
    if work.status(payload.session_id) not in {'completed','done','stopped','cancelled','failed'}:
        if payload.kind=='app':work.module(payload.session_id,payload.kind,payload.title,payload.data)
        else:work.emit(payload.session_id,payload.kind,{'summary':payload.title,**payload.data})
    return {'ok':True}


@router.patch('/preferences')
def preferences_update(payload: dict, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    try: return write_preferences(payload)
    except ValueError as exc: raise HTTPException(422,str(exc))


@router.get('/state/{session_id}')
def task_state(request: Request, session_id: str, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    return {'status':request.app.state.work_state.status(session_id), 'automation_enabled':read_preferences()['automation_enabled']}
