"""Read-only execution history; desktop token and explicit owner scope required."""

import asyncio
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from app.api.llm_settings import require_control
from app.schemas.agent_trace import AgentTrace, TraceEventPage, TracePage, TraceSummary
from app.services.trace.privacy import identifier

router = APIRouter(prefix="/api/agent/traces", tags=["agent-traces"])


def authorized(x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)


router.dependencies.append(Depends(authorized))
Scope = Annotated[UUID, Query()]
OptionalScope = Annotated[UUID | None, Query()]


async def query(request: Request, method: str, *args, **kwargs):
    repository = getattr(request.app.state, "trace_repository", None)
    if repository is None:
        raise HTTPException(503, "执行追踪暂不可用；原任务仍可正常运行")
    try:
        return await asyncio.to_thread(getattr(repository, method), *args, **kwargs)
    except LookupError as error:
        raise HTTPException(404, "当前会话中没有这条执行轨迹") from error
    except ValueError as error:
        raise HTTPException(409, "轨迹版本不受支持，或任务尚未结束") from error
    except SQLAlchemyError as error:
        raise HTTPException(503, "轨迹存储暂不可用") from error


@router.get("", response_model=TracePage)
async def listing(
    request: Request,
    conversation_id: Scope,
    work_session_id: OptionalScope = None,
    task_id: OptionalScope = None,
    status: Literal[
        "planning",
        "ready",
        "queued",
        "running",
        "paused",
        "waiting_permission",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    ]
    | None = None,
    tool_name: str | None = Query(default=None, max_length=80),
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=30, ge=1, le=100),
):
    if tool_name and identifier(tool_name) != tool_name:
        raise HTTPException(422, "工具名称无效")
    if since:
        since = (
            since.replace(tzinfo=timezone.utc)
            if since.tzinfo is None
            else since.astimezone(timezone.utc)
        )
    if until:
        until = (
            until.replace(tzinfo=timezone.utc)
            if until.tzinfo is None
            else until.astimezone(timezone.utc)
        )
    if since and until and since > until:
        raise HTTPException(422, "开始时间必须早于结束时间")
    return await query(
        request,
        "listing",
        str(conversation_id),
        work_session_id=str(work_session_id) if work_session_id else None,
        task_id=str(task_id) if task_id else None,
        status=status,
        tool_name=tool_name,
        since=since,
        until=until,
        cursor=cursor,
        limit=limit,
    )


@router.get("/{trace_id}", response_model=AgentTrace)
async def detail(
    request: Request, trace_id: UUID, conversation_id: Scope, work_session_id: OptionalScope = None
):
    return await query(
        request,
        "detail",
        str(trace_id),
        str(conversation_id),
        str(work_session_id) if work_session_id else None,
    )


@router.get("/{trace_id}/events", response_model=TraceEventPage)
async def events(
    request: Request,
    trace_id: UUID,
    conversation_id: Scope,
    work_session_id: OptionalScope = None,
    after: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=200, ge=1, le=250),
):
    return await query(
        request,
        "events",
        str(trace_id),
        str(conversation_id),
        work_session_id=str(work_session_id) if work_session_id else None,
        after=after,
        limit=limit,
    )


@router.get("/{trace_id}/summary", response_model=TraceSummary)
async def summary(
    request: Request, trace_id: UUID, conversation_id: Scope, work_session_id: OptionalScope = None
):
    return await query(
        request,
        "summary",
        str(trace_id),
        str(conversation_id),
        str(work_session_id) if work_session_id else None,
    )


@router.delete("/{trace_id}")
async def remove(
    request: Request, trace_id: UUID, conversation_id: Scope, work_session_id: OptionalScope = None
):
    return await query(
        request,
        "remove",
        str(trace_id),
        str(conversation_id),
        str(work_session_id) if work_session_id else None,
    )
