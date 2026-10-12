"""Agent planning and execution endpoints."""

from typing import Literal
import asyncio
from types import SimpleNamespace
from fastapi.responses import StreamingResponse
from app.database.session import SessionLocal
from app.services.voice.continuous_pipeline import RealtimeVoicePipeline
from app.api.chat_stream_v4 import sse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.schemas.chat import AttachmentInput

from app.services.agent_router import ConversationRouter
from app.services.desktop.lifecycle import TaskCancelled


router = APIRouter(prefix="/api/agent", tags=["agent"])


class RouteRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    mode: Literal["auto", "chat", "agent"] = "auto"


class PlanRequest(BaseModel):
    renderer_id: str | None = Field(default=None,max_length=100)
    character_id: str = Field(min_length=1, max_length=100)
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=4)
    workspace_folder: str | None = Field(default=None, max_length=500)


class ExecuteRequest(BaseModel):
    plan_id: str
    approved_ids: list[str] = Field(default_factory=list, max_length=5)
    voice_enabled: bool = True
    expression: Literal["restrained", "natural", "rich"] = "natural"
    speed_factor: float = Field(default=1.0, ge=0.9, le=1.1)


class SelectApplicationRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=100)
    app_id: str = Field(min_length=1, max_length=100)


@router.post("/route")
def route(payload: RouteRequest):
    return {"mode": ConversationRouter().route(payload.message, payload.mode)}


@router.post("/plan")
async def plan(request: Request, payload: PlanRequest):
    try:
        return await request.app.state.agent_service.plan(request.app, payload.character_id,
                                                          payload.conversation_id, payload.message.strip(), payload.attachments,
                                                          payload.workspace_folder,payload.renderer_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/execute")
async def execute(request: Request, payload: ExecuteRequest):
    try:
        return await request.app.state.agent_service.execute(
            request.app, payload.plan_id, set(payload.approved_ids), payload.expression,
            payload.voice_enabled, payload.speed_factor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskCancelled as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@router.post('/select-application')
async def select_application(request: Request, payload: SelectApplicationRequest):
    try:
        return await request.app.state.agent_service.select_application(
            request.app, payload.plan_id, payload.app_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/execute/stream')
async def execute_stream(request:Request,payload:ExecuteRequest):
    async def events():
        stored=request.app.state.work_state.restore_plan(payload.plan_id)
        pending=request.app.state.agent_service.pending.get(payload.plan_id)
        route={'task_id':payload.plan_id,'conversation_id':pending.conversation_id if pending else (stored or {}).get('conversation_id'),
               'work_session_id':pending.work_session_id if pending else (stored or {}).get('work_session_id'),
               'renderer_id':pending.renderer_id if pending else (stored or {}).get('renderer_id')}
        text=asyncio.Queue(maxsize=32)
        ready=asyncio.get_running_loop().create_future()
        async def started(info):
            if not ready.done():ready.set_result(info)
        async def run():
            try:
                return await request.app.state.agent_service.execute(request.app,payload.plan_id,
                    set(payload.approved_ids),payload.expression,payload.voice_enabled,payload.speed_factor,
                    on_text=text.put,on_started=started)
            finally:
                if not asyncio.current_task().cancelling():
                    await text.put(None)
        task=asyncio.create_task(run())
        try:
            await asyncio.wait({task,ready},return_when=asyncio.FIRST_COMPLETED)
            if not ready.done():
                await task
                raise ValueError('任务没有生成回复')
            info=ready.result()
            envelope={key:info.get(key) for key in ('conversation_id','work_session_id','task_id','renderer_id')}
            async def deltas():
                while (delta:=await text.get()) is not None:yield delta
                await task
            async def complete(canonical):
                result=await task
                if result['content']!=canonical:
                    raise ValueError('Canonical reply mismatch')
                return result
            speech_payload=SimpleNamespace(voice_enabled=payload.voice_enabled,expression=payload.expression,
                speed_factor=payload.speed_factor,message=info['user_text'],character_id=info['character_id'])
            pipeline=RealtimeVoicePipeline(request.app,speech_payload,info['message_id'],SessionLocal)
            yield sse('user_saved',envelope)
            async for event,data in pipeline.events(deltas(),info['messages'],complete):
                yield sse(event,{**envelope,**data,'event_type':event})
        except asyncio.CancelledError:
            raise
        except TaskCancelled as exc:
            yield sse('error',{**route,'detail':str(exc)[:240],'event_type':'error'})
        except Exception as exc:
            yield sse('error',{**route,'detail':str(exc)[:240] or '工作任务未完成','event_type':'error'})
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    return StreamingResponse(events(),media_type='text/event-stream',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
