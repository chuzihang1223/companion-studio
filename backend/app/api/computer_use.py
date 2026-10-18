"""Private UI adapter into the existing single-tool Runtime bridge; no new executor."""

import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from app.api.agent_traces import authorized
from app.database.session import SessionLocal
from app.models import Conversation
from app.services.computer_use.contracts import Owner, ExplicitAction, ComputerUseError
from pydantic import Field
from app.services.workflows.contracts import Node, WorkflowError


class PrivateComputerRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default validation response echoes rejected input.
                # This route may reject a secret; never reflect that body.
                raise HTTPException(422, detail={"code": "COMPUTER_ARGUMENT_INVALID"}) from None

        return bounded


router = APIRouter(
    prefix="/api/computer",
    tags=["computer-use"],
    dependencies=[Depends(authorized)],
    route_class=PrivateComputerRoute,
)


def validate_owner(value):
    with SessionLocal() as db:
        row = db.get(Conversation, str(value.conversation_id))
        if (
            not row
            or row.is_deleted
            or row.character_id != value.character_id
            or row.conversation_type not in {"work", "project"}
        ):
            raise HTTPException(404, detail={"code": "COMPUTER_OWNER_INVALID"})
    return {"valid": True}


@router.post("/owner")
async def owner(value: Owner):
    try:
        return await asyncio.to_thread(validate_owner, value)
    except SQLAlchemyError:
        raise HTTPException(503, detail={"code": "COMPUTER_STORAGE_UNAVAILABLE"}) from None


@router.post("/vision/target")
async def vision_target(value: Owner, request: Request):
    from app.services.computer_use.vision import target

    await owner(value)
    try:
        return target(request.app)[1]
    except ComputerUseError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None


class VisionInput(Owner):
    task_id: UUID
    window_id: UUID
    observation_id: UUID
    target_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    image: str = Field(max_length=2800000)


class VisionCancel(Owner):
    task_id: UUID


@router.post("/vision/cancel")
async def vision_cancel(value: VisionCancel, request: Request):
    from app.services.computer_use.vision_lifecycle import lifecycle

    await owner(value)
    try:
        lifecycle(request.app).cancel(str(value.task_id), str(value.conversation_id))
        return {"cancelled": True}
    except ComputerUseError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None


class QARequest(Owner):
    consent_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    fixture_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    operation: str = Field(pattern=r"^(open|close|inspect)$")


@router.post("/qa/fixture")
async def qa_fixture(value: QARequest, request: Request):
    from app.services.computer_use.qa_profile import enabled

    if not enabled():
        raise HTTPException(409, detail={"code": "VISUAL_QA_ISOLATED_PROFILE_REQUIRED"})
    await owner(value)
    return await request.app.state.desktop_bridge.request(
        "visual_qa." + value.operation,
        {
            "owner": {
                "conversation_id": str(value.conversation_id),
                "character_id": value.character_id,
            },
            "consent_id": value.consent_id,
            "fixture_id": value.fixture_id,
        },
        "",
        timeout=30,
    )


@router.post("/vision/analyze")
async def vision_analyze(value: VisionInput, request: Request):
    from app.services.computer_use.vision_lifecycle import lifecycle

    await owner(value)
    try:
        return await lifecycle(request.app).run(
            str(value.task_id),
            str(value.conversation_id),
            value.target_fingerprint,
            value.image,
            {
                "window_id": str(value.window_id),
                "observation_id": str(value.observation_id),
                "action_id": str(value.task_id),
            },
        )
    except asyncio.CancelledError:
        raise HTTPException(409, detail={"code": "COMPUTER_PAUSED"}) from None
    except TimeoutError:
        raise HTTPException(504, detail={"code": "VISION_ANALYSIS_TIMEOUT"}) from None
    except ComputerUseError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None


@router.post("/execute")
async def execute(value: ExplicitAction, request: Request):
    await owner(value)
    service = getattr(request.app.state, "workflows", None)
    if service is None:
        raise HTTPException(503, detail={"code": "COMPUTER_RUNTIME_UNAVAILABLE"})
    args = value.arguments.model_dump(mode="json", exclude_none=True)
    node = Node(
        node_id="observer_action",
        type="tool",
        title="屏幕观察操作",
        config={"name": "computer." + value.operation, "arguments": args},
    )
    context = None
    try:
        context, _ = await asyncio.to_thread(
            service.bridge.prepare,
            {"conversation_id": str(value.conversation_id), "renderer_id": "visual-observer"},
            node,
            args,
            None,
        )
        recorder = getattr(request.app.state, "trace_recorder", None)
        from contextlib import nullcontext

        trace = (
            await recorder.execution_scope(
                context.task_id, context.conversation_id, context.work_session_id
            )
            if recorder
            else nullcontext()
        )
        with trace:
            result, verified = await service.bridge.execute(context, {context.calls[0]["id"]})
        return {
            **result,
            "goal_verified": verified,
            "task_id": context.task_id,
            "work_session_id": context.work_session_id,
        }
    except WorkflowError as error:
        raise HTTPException(error.status, detail={"code": error.code}) from None
    except (ValueError, SQLAlchemyError):
        raise HTTPException(409, detail={"code": "COMPUTER_RUNTIME_FAILED"}) from None
