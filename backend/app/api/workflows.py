"""Workflow administration/execution requires the main-process control token and owner scope."""

import asyncio
import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.agent_traces import authorized
from app.services.workflows.contracts import Definition, Strict, WorkflowError
from app.services.workflows.templates import TEMPLATES, make_template


class ProtectedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                size = request.headers.get("content-length", "0")
                if size.isdecimal() and int(size) > 600000:
                    return JSONResponse(
                        status_code=413, content={"detail": {"code": "WORKFLOW_SIZE_LIMIT"}}
                    )
                return await handler(request)
            except WorkflowError as error:
                return JSONResponse(
                    status_code=error.status,
                    content={"detail": {"code": error.code, "node_id": error.node_id}},
                )
            except SQLAlchemyError:
                return JSONResponse(
                    status_code=503, content={"detail": {"code": "WORKFLOW_STORAGE_UNAVAILABLE"}}
                )
            except ValueError as error:
                code = getattr(error, "code", "WORKFLOW_INVALID")
                return JSONResponse(status_code=409, content={"detail": {"code": code}})

        return handle


router = APIRouter(
    tags=["workflows"], route_class=ProtectedRoute, dependencies=[Depends(authorized)]
)


def service(request):
    result = getattr(request.app.state, "workflows", None)
    if not result:
        raise HTTPException(503, detail={"code": "WORKFLOW_STORAGE_UNAVAILABLE"})
    return result


class Save(Strict):
    definition: Definition
    revision: int = Field(default=0, ge=0)


class Revision(Strict):
    revision: int = Field(ge=1)


class Run(Strict):
    version_id: UUID = Field(strict=False)
    conversation_id: UUID = Field(strict=False)
    inputs: dict = Field(default_factory=dict, max_length=30)
    renderer_id: str = Field(default="workflow", max_length=100)


class Approval(Strict):
    conversation_id: UUID = Field(strict=False)
    approval_id: UUID = Field(strict=False)
    decision: bool


class Control(Strict):
    conversation_id: UUID = Field(strict=False)
    action: Literal["pause", "continue", "cancel"]


class Resume(Strict):
    conversation_id: UUID = Field(strict=False)
    revision: int = Field(ge=1)


@router.get("/api/workflows")
async def listing(request: Request):
    return {"items": service(request).repository.listing()}


@router.get("/api/workflows/catalog")
async def catalog(request: Request):
    return service(request).catalog.public()


@router.get("/api/workflows/templates")
async def templates(request: Request):
    service(request)
    return {
        "items": [
            {"template_id": key, "name": value[0], "description": value[1], "license": "MIT"}
            for key, value in TEMPLATES.items()
        ]
    }


@router.get("/api/workflows/templates/{template_id}")
async def template(template_id: str, request: Request):
    return make_template(template_id, service(request).catalog.public()).model_dump(mode="json")


@router.post("/api/workflows")
async def create(options: Save, request: Request):
    if options.revision != 0:
        raise WorkflowError("WORKFLOW_CREATE_REVISION_INVALID")
    return service(request).repository.save(options.definition)


@router.get("/api/workflows/{workflow_id}")
async def detail(workflow_id: UUID, request: Request):
    return service(request).repository.get(str(workflow_id))


@router.patch("/api/workflows/{workflow_id}")
async def save(workflow_id: UUID, options: Save, request: Request):
    if str(workflow_id) != options.definition.workflow_id or options.revision < 1:
        raise WorkflowError("WORKFLOW_ID_MISMATCH")
    return service(request).repository.save(options.definition, options.revision)


@router.delete("/api/workflows/{workflow_id}")
async def remove(workflow_id: UUID, revision: int, request: Request):
    service(request).repository.delete(str(workflow_id), revision)
    return {"deleted": True, "history_retained": True}


@router.post("/api/workflows/{workflow_id}/validate")
async def validate(workflow_id: UUID, options: Save, request: Request):
    if options.definition.workflow_id != str(workflow_id):
        raise WorkflowError("WORKFLOW_ID_MISMATCH")
    compiled = service(request).validator.compile(options.definition)
    return {
        "valid": True,
        "order": compiled.order,
        "validation_ms": compiled.validation_ms,
        "compile_ms": compiled.compile_ms,
    }


@router.post("/api/workflows/{workflow_id}/archive")
async def archive(workflow_id: UUID, options: Revision, request: Request):
    return service(request).repository.archive(str(workflow_id), options.revision)


@router.post("/api/workflows/{workflow_id}/publish")
async def publish(workflow_id: UUID, options: Save, request: Request):
    if options.definition.workflow_id != str(workflow_id):
        raise WorkflowError("WORKFLOW_ID_MISMATCH")
    coordinator = service(request)
    coordinator.validator.compile(options.definition)
    # Pin to current registered schemas, without silently modifying the user's saved draft.
    pinned = coordinator.catalog.pin(options.definition)
    if pinned != options.definition:
        saved = coordinator.repository.save(pinned, options.revision)
        options.revision = saved["revision"]
    return coordinator.repository.publish(pinned, options.revision)


@router.get("/api/workflows/{workflow_id}/versions")
async def versions(workflow_id: UUID, request: Request):
    return {"items": service(request).repository.versions(str(workflow_id))}


@router.get("/api/workflow-versions/{version_id}")
async def version(version_id: UUID, request: Request):
    return service(request).repository.version(str(version_id)).model_dump(mode="json")


@router.get("/api/workflow-versions/{version_id}/overview")
async def overview(version_id: UUID, request: Request, conversation_id: UUID):
    coordinator = service(request)
    workspace = coordinator.scope(str(conversation_id))
    definition = coordinator.repository.version(str(version_id))
    coordinator.validator.compile(definition)
    return {
        "workflow_id": definition.workflow_id,
        "version": definition.version,
        "name": definition.name,
        "workspace": workspace,
        "budget": definition.budget.model_dump(),
        "nodes": [
            {"node_id": n.node_id, "type": n.type, "title": n.title, "config": n.config}
            for n in definition.nodes
        ],
        "grants_permissions": False,
    }


@router.post("/api/workflows/{workflow_id}/runs")
async def start(workflow_id: UUID, options: Run, request: Request):
    return service(request).start(
        str(workflow_id),
        str(options.version_id),
        str(options.conversation_id),
        options.inputs,
        options.renderer_id,
    )


@router.get("/api/workflow-runs")
async def runs(request: Request, conversation_id: UUID, workflow_id: UUID | None = None):
    coordinator = service(request)
    coordinator.scope(str(conversation_id))
    return {
        "items": coordinator.repository.runs(
            str(conversation_id), str(workflow_id) if workflow_id else None
        )
    }


@router.get("/api/workflow-runs/{run_id}")
async def run(run_id: UUID, request: Request, conversation_id: UUID):
    return service(request).repository.run(str(run_id), str(conversation_id))


@router.get("/api/workflow-runs/{run_id}/nodes")
async def nodes(run_id: UUID, request: Request, conversation_id: UUID):
    return {"items": service(request).repository.run(str(run_id), str(conversation_id))["nodes"]}


@router.get("/api/workflow-runs/{run_id}/events")
async def events(
    run_id: UUID, request: Request, conversation_id: UUID, after: int = Query(0, ge=0)
):
    repo = service(request).repository
    return {"items": repo.events(str(run_id), str(conversation_id), after)}


@router.get("/api/workflow-runs/{run_id}/stream")
async def stream(
    run_id: UUID, request: Request, conversation_id: UUID, after: int = Query(0, ge=0)
):
    coordinator = service(request)
    coordinator.repository.run(str(run_id), str(conversation_id))

    async def generate():
        cursor = after
        while not await request.is_disconnected():
            rows = coordinator.repository.events(str(run_id), str(conversation_id), cursor)
            for row in rows:
                cursor = row["cursor"]
                yield f"id: {cursor}\nevent: workflow\ndata: {json.dumps(row)}\n\n"
            state = coordinator.repository.run(str(run_id), str(conversation_id))["state"]
            if (
                state in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "NEEDS_REVIEW"}
                and not rows
            ):
                return
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/api/workflow-runs/{run_id}/approve")
async def approve(run_id: UUID, options: Approval, request: Request):
    service(request).approve(
        str(run_id), str(options.conversation_id), str(options.approval_id), options.decision
    )
    return {"accepted": True}


@router.post("/api/workflow-runs/{run_id}/control")
async def control(run_id: UUID, options: Control, request: Request):
    await service(request).control(str(run_id), str(options.conversation_id), options.action)
    return {"accepted": True}


@router.get("/api/workflow-runs/{run_id}/resume-preview")
async def preview(run_id: UUID, request: Request, conversation_id: UUID):
    return await service(request).recovery.preview(str(run_id), str(conversation_id))


@router.post("/api/workflow-runs/{run_id}/resume")
async def resume(run_id: UUID, options: Resume, request: Request):
    return await service(request).resume(
        str(run_id), str(options.conversation_id), options.revision
    )
