"""所有读写评测接口均要求主进程控制令牌，嵌入网页不能直接执行。"""

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.agent_traces import authorized
from app.schemas.evaluation import RunAction, RunRequest
from app.services.evaluation.reports import EvaluationReportService

router = APIRouter(prefix="/api/evaluation", dependencies=[Depends(authorized)])


def engine(request):
    value = getattr(request.app.state, "evaluation", None)
    if value is None:
        raise HTTPException(503, "评测服务不可用")
    return value


async def read(function, *args, **kwargs):
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, "评测记录不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, "当前状态不允许该操作") from exc
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(503, "评测存储暂不可用") from exc


@router.get("/datasets")
async def datasets(request: Request):
    return {"items": [engine(request).registry.public()]}


@router.get("/datasets/{dataset_id}")
async def dataset(dataset_id: Literal["studio-core"], request: Request):
    return engine(request).registry.public(True)


@router.get("/models")
async def models(request: Request):
    roles = request.app.state.model_roles
    rows = []
    for role in ("character_model", "planning_model", "tool_execution_model", "coding_model"):
        config = roles.service(role).config
        rows.append(
            {
                "role": role,
                "model_id": config.model if config else None,
                "configured": bool(
                    config
                    and config.model != "unconfigured"
                    and (config.api_key or config.base_url.startswith("http://127.0.0.1:"))
                ),
            }
        )
    return {"items": rows}


@router.post("/runs")
async def start(options: RunRequest, request: Request):
    try:
        return await engine(request).start(options, request.app)
    except ValueError as exc:
        raise HTTPException(409, "无法启动：请核对测试项、模型配置、预算和当前运行状态") from exc


@router.get("/runs")
async def runs(
    request: Request, cursor: int = Query(0, ge=0, le=100), limit: int = Query(20, ge=1, le=100)
):
    return await read(engine(request).repository.listing, cursor, limit)


@router.get("/compare")
async def compare(request: Request, left: UUID, right: UUID):
    return await read(engine(request).repository.comparison, str(left), str(right))


@router.get("/runs/{run_id}")
async def detail(run_id: UUID, request: Request):
    return await read(engine(request).repository.detail, str(run_id))


@router.get("/runs/{run_id}/results")
async def results(
    run_id: UUID,
    request: Request,
    cursor: int = Query(0, ge=0, le=100),
    limit: int = Query(20, ge=1, le=100),
):
    return await read(engine(request).repository.results, str(run_id), cursor, limit)


@router.get("/runs/{run_id}/metrics")
async def metrics(run_id: UUID, request: Request):
    return (await read(engine(request).repository.detail, str(run_id)))["metrics"]


@router.post("/runs/{run_id}/control")
async def control(run_id: UUID, action: RunAction, request: Request):
    try:
        return await engine(request).control(str(run_id), action.action)
    except LookupError as exc:
        raise HTTPException(404, "评测不存在") from exc
    except ValueError as exc:
        raise HTTPException(409, "评测已结束或不可用") from exc


@router.post("/runs/{run_id}/cancel")
async def cancel(run_id: UUID, request: Request):
    return await control(run_id, RunAction(action="cancel"), request)


@router.delete("/runs/{run_id}")
async def remove(run_id: UUID, request: Request):
    try:
        return await engine(request).remove(str(run_id))
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, "请先结束有效评测再删除") from exc


@router.get("/runs/{run_id}/export")
async def export(
    run_id: UUID, request: Request, format: Literal["json", "csv", "markdown"] = "json"
):
    return await read(
        EvaluationReportService(engine(request).repository).export, str(run_id), format
    )


@router.get("/runs/{run_id}/events")
async def events(run_id: UUID, request: Request):
    value = engine(request)
    await read(value.repository.detail, str(run_id))
    return StreamingResponse(
        value.stream(str(run_id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store"},
    )


def read_trace(value, run_id, attempt_id, operation, cursor, limit, trace_id=None):
    row = value.repository.attempt(run_id, attempt_id)
    if not row["trace_id"] or not row["conversation_id"]:
        raise LookupError("该评测无 Trace")
    with value.environment.trace_repository(run_id, attempt_id) as repo:
        scopes = row["result"].get("trace_scopes") or [row]
        if operation == "list":
            traces = [
                repo.detail(scope["trace_id"], scope["conversation_id"]) for scope in scopes[:2]
            ]
            return {
                "items": traces[cursor : cursor + limit],
                "next_cursor": None,
                "has_more": False,
                "limit": limit,
                "schema_version": 1,
            }
        selected = next(
            (scope for scope in scopes if scope["trace_id"] == (trace_id or row["trace_id"])), None
        )
        if not selected:
            raise LookupError("Trace 不属于当前评测尝试")
        scope = selected["conversation_id"]
        if operation == "detail":
            return repo.detail(selected["trace_id"], scope)
        if operation == "summary":
            return repo.summary(selected["trace_id"], scope)
        return repo.events(selected["trace_id"], scope, after=cursor, limit=limit)


@router.get("/runs/{run_id}/attempts/{attempt_id}/trace/{operation}")
async def trace(
    run_id: UUID,
    attempt_id: UUID,
    operation: Literal["list", "detail", "events", "summary"],
    request: Request,
    cursor: int = Query(0, ge=0, le=100000),
    limit: int = Query(250, ge=1, le=250),
    trace_id: UUID | None = None,
):
    return await read(
        read_trace,
        engine(request),
        str(run_id),
        str(attempt_id),
        operation,
        cursor,
        min(limit, 100) if operation == "list" else limit,
        str(trace_id) if trace_id else None,
    )
