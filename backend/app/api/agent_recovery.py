"""Fixed local recovery routes; reads never execute a pending tool or model."""

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.agent_traces import authorized
from app.services.recovery.checkpoints import RecoveryError

router = APIRouter(
    prefix="/api/agent/recovery", tags=["agent-recovery"], dependencies=[Depends(authorized)]
)
Scope = Annotated[UUID, Query()]
OptionalScope = Annotated[UUID | None, Query()]


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID
    work_session_id: UUID
    renderer_id: str = Field(min_length=1, max_length=100)
    request_id: UUID
    revision: int = Field(ge=1, le=1_000_000)
    approved_ids: list[str] = Field(default_factory=list, max_length=5)
    confirm_model_requests: bool = False


async def invoke(request, method, *args, threaded=False, **kwargs):
    coordinator = getattr(request.app.state, "recovery", None)
    if not coordinator:
        raise HTTPException(503, detail={"code": "RECOVERY_UNAVAILABLE"})
    try:
        target = getattr(coordinator.manager if threaded else coordinator, method)
        return (
            await asyncio.to_thread(target, *args, **kwargs)
            if threaded
            else await target(*args, **kwargs)
        )
    except RecoveryError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(503, detail={"code": "CHECKPOINT_STORAGE_UNAVAILABLE"}) from exc
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise HTTPException(409, detail={"code": "CHECKPOINT_INVALID"}) from exc


@router.get("/tasks")
async def listing(
    request: Request,
    conversation_id: Scope,
    work_session_id: OptionalScope = None,
    limit: int = Query(default=30, ge=1, le=50),
):
    return {
        "items": await invoke(
            request,
            "listing",
            str(conversation_id),
            str(work_session_id) if work_session_id else None,
            limit,
            threaded=True,
        )
    }


@router.get("/tasks/{task_id}")
async def detail(
    request: Request, task_id: UUID, conversation_id: Scope, work_session_id: OptionalScope = None
):
    return await invoke(
        request,
        "detail",
        str(task_id),
        str(conversation_id),
        str(work_session_id) if work_session_id else None,
        threaded=True,
    )


@router.get("/tasks/{task_id}/checkpoints")
async def checkpoints(
    request: Request, task_id: UUID, conversation_id: Scope, work_session_id: OptionalScope = None
):
    value = await detail(request, task_id, conversation_id, work_session_id)
    return {"task_id": str(task_id), "items": value["checkpoints"]}


@router.get("/tasks/{task_id}/preview")
async def preview(
    request: Request, task_id: UUID, conversation_id: Scope, work_session_id: OptionalScope = None
):
    return await invoke(
        request,
        "preview",
        str(task_id),
        str(conversation_id),
        str(work_session_id) if work_session_id else None,
    )


@router.post("/tasks/{task_id}/{action}")
async def control(
    request: Request,
    task_id: UUID,
    action: Literal["resume", "cancel", "revalidate", "restart", "takeover", "pause", "continue"],
    payload: RecoveryRequest,
):
    args = (str(task_id), str(payload.conversation_id), str(payload.work_session_id))
    if action == "resume":
        return await invoke(
            request,
            "resume",
            *args,
            payload.renderer_id,
            str(payload.request_id),
            payload.revision,
            payload.approved_ids,
        )
    if action == "restart":
        return await invoke(
            request, "restart", *args, payload.renderer_id, payload.confirm_model_requests
        )
    if action in {"pause", "continue", "takeover"}:
        return await invoke(request, "control", *args, action)
    return await invoke(request, action, *args)
