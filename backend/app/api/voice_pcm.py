import base64
import sys
from fastapi import APIRouter,Header,HTTPException,Request
from pydantic import BaseModel,Field
from app.api.llm_settings import require_control
router=APIRouter(prefix='/api/voice/native/stream',tags=['voice'])


class StreamCancel(BaseModel):
    session_id:str=Field(min_length=1,max_length=100)


@router.post('/cancel')
async def cancel(payload:StreamCancel,request:Request,x_companion_control_token:str|None=Header(default=None)):
    require_control(x_companion_control_token)
    operation=getattr(request.app.state,'live_voice_sessions',{}).pop(payload.session_id,None)
    if operation:
        import asyncio
        tasks=getattr(request.app.state,'live_voice_tasks',None)
        if tasks is None:tasks=request.app.state.live_voice_tasks=set()
        task=asyncio.create_task(operation())
        tasks.add(task);task.add_done_callback(tasks.discard)
    return {'cancelled':bool(operation)}

class StreamStart(BaseModel):
    message_id:str=Field(min_length=1,max_length=100)
    sample_rate:int=Field(ge=8000,le=96000)
    channels:int=Field(ge=1,le=2)
    volume:float=Field(default=1,ge=0,le=1)
    session_id:str|None=Field(default=None,max_length=100)
    initial_buffer_ms:int=Field(default=300,ge=200,le=1200)

class StreamFeed(BaseModel):
    pcm_base64:str=Field(default='',max_length=1400000)
    segment_id:str=Field(min_length=1,max_length=120)
    end_segment:bool=False
    session_id:str|None=Field(default=None,max_length=100)
    sequence:int|None=Field(default=None,ge=0)

@router.post('/start')
def start(payload:StreamStart,request:Request,x_companion_control_token:str|None=Header(default=None)):
    require_control(x_companion_control_token)
    if sys.platform!='win32':raise HTTPException(503,'Windows PCM output is unavailable')
    player=request.app.state.native_player
    player.stop()
    player.stream_owned=True
    try:
        result=player.pcm.start(payload.message_id,payload.sample_rate,payload.channels,payload.volume,
                                initial_buffer_ms=payload.initial_buffer_ms)
        player.pcm.session_id=payload.session_id or payload.message_id
        player.pcm.sequence=-1
        return result
    except (OSError,ValueError) as exc:player.stream_owned=False;raise HTTPException(503,str(exc)) from exc

@router.post('/feed')
def feed(payload:StreamFeed,request:Request,x_companion_control_token:str|None=Header(default=None)):
    require_control(x_companion_control_token)
    player=request.app.state.native_player.pcm
    if payload.session_id is not None and payload.session_id!=getattr(player,'session_id',None):
        return {'status':'ignored','reason':'stale_session'}
    if payload.sequence is not None:
        previous=getattr(player,'sequence',-1)
        if payload.sequence<=previous:return {'status':'ignored','reason':'duplicate_sequence'}
        if payload.sequence!=previous+1:raise HTTPException(409,'PCM chunk sequence gap')
    try:
        result=player.feed(base64.b64decode(payload.pcm_base64,validate=True),payload.segment_id,payload.end_segment)
        if payload.sequence is not None:player.sequence=payload.sequence
        return result
    except (OSError,ValueError) as exc:raise HTTPException(422,str(exc)) from exc

@router.post('/finish')
def finish(request:Request,x_companion_control_token:str|None=Header(default=None)):
    require_control(x_companion_control_token)
    return request.app.state.native_player.pcm.finish()
