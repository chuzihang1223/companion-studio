"""Main-process control token, strict UUID owner scopes and fixed scheduler operations."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.agent_traces import authorized
from app.services.scheduling.contracts import ScheduleDefinition, ScheduleError
from app.services.scheduling.notifications import SchedulerNotifications
from app.services.workflows.contracts import Strict


class ProtectedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                length = request.headers.get("content-length", "0")
                if not length.isdecimal() or int(length) > 100000:
                    return JSONResponse(
                        status_code=413, content={"detail": {"code": "SCHEDULE_SIZE_LIMIT"}}
                    )
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422, content={"detail": {"code": "SCHEDULE_INPUT_INVALID"}}
                )
            except SQLAlchemyError:
                return JSONResponse(
                    status_code=503, content={"detail": {"code": "SCHEDULE_STORAGE_UNAVAILABLE"}}
                )
            except ValueError as error:
                code = getattr(error, "code", "SCHEDULE_INVALID")
                return JSONResponse(
                    status_code=getattr(error, "status", 409), content={"detail": {"code": code}}
                )

        return handle


router = APIRouter(
    prefix="/api/scheduler",
    tags=["scheduler"],
    route_class=ProtectedRoute,
    dependencies=[Depends(authorized)],
)


def service(request):
    result = getattr(request.app.state, "scheduler", None)
    if not result or result.closing:
        raise HTTPException(503, detail={"code": "SCHEDULE_RUNTIME_UNAVAILABLE"})
    return result


class Save(Strict):
    definition: ScheduleDefinition
    revision: int = Field(default=0, ge=0)


class Owner(Strict):
    conversation_id: UUID = Field(strict=False)


class Control(Owner):
    revision: int = Field(ge=1)
    state: Literal["ENABLED", "PAUSED", "DISABLED", "ARCHIVED"]


class Source(Owner):
    kind: Literal["FILE_CHANGE", "WORKFLOW_STATE"]
    workflow_run_id: UUID | None = Field(default=None, strict=False)


class Pause(Strict):
    paused: bool


class Delivery(Strict):
    result: Literal["SHOWN", "FAILED", "UNAVAILABLE"]


class Consent(Owner):
    binding: str = Field(pattern=r"^[a-f0-9]{64}$")


class Resume(Consent):
    revision: int = Field(ge=1)


@router.get("/status")
async def status(request: Request):
    return service(request).status()


@router.post("/pause")
async def pause(options: Pause, request: Request):
    return service(request).store.settings({"paused": options.paused})


@router.post("/shutdown")
async def shutdown(request: Request):
    await service(request).close()
    return {"stopped": True}


@router.post("/preview")
async def preview(options: Save, request: Request):
    return service(request).validate(options.definition)


@router.get("/schedules")
async def listing(request: Request, conversation_id: UUID):
    scheduler = service(request)
    scheduler.app.state.workflows.scope(str(conversation_id))
    return {"items": scheduler.store.listing(str(conversation_id))}


@router.post("/schedules")
async def save(options: Save, request: Request):
    return service(request).save(options.definition, options.revision)


@router.get("/owners")
async def owners(request: Request):
    from sqlalchemy import select
    from app.models import Conversation

    with service(request).store.factory() as db:
        return {
            "items": [
                {"conversation_id": row.id, "title": row.title}
                for row in db.scalars(
                    select(Conversation)
                    .where(
                        Conversation.is_deleted.is_(False),
                        Conversation.conversation_type.in_({"work", "project"}),
                    )
                    .limit(200)
                )
            ]
        }


@router.get("/recent")
async def recent(request: Request):
    return {
        "items": [
            {
                "conversation_id": r["owner_id"],
                "schedule_id": r["schedule_id"],
                "occurrence_id": r["occurrence_id"],
                "kind": r["status"],
                "created_at": r["created_at"],
            }
            for r in service(request).store.occurrences(limit=20)
        ]
    }


@router.get("/schedules/{key}/history")
async def history(key: UUID, request: Request, conversation_id: UUID):
    scheduler = service(request)
    scheduler.store.get(str(key), str(conversation_id))
    rows = scheduler.store.occurrences(str(conversation_id), str(key))
    return {
        "items": [
            scheduler.store.occurrence(row["occurrence_id"], str(conversation_id)) for row in rows
        ],
        "events": scheduler.store.journal(str(key), str(conversation_id)),
    }


@router.post("/schedules/{key}/state")
async def state(key: UUID, options: Control, request: Request):
    return service(request).set_state(
        str(key), str(options.conversation_id), options.revision, options.state
    )


@router.post("/schedules/{key}/run-now")
async def run_now(key: UUID, options: Owner, request: Request):
    return service(request).run_now(str(key), str(options.conversation_id))


@router.post("/occurrences/{key}/approve")
async def approve(key: UUID, options: Consent, request: Request):
    return service(request).grant_once(str(key), str(options.conversation_id), options.binding)


@router.post("/occurrences/{key}/cancel")
async def cancel(key: UUID, options: Owner, request: Request):
    await service(request).cancel(str(key), str(options.conversation_id))
    return {"cancelled": True}


@router.get("/sources")
async def sources(request: Request, conversation_id: UUID):
    return {"items": service(request).watch.listing(str(conversation_id))}


@router.get("/occurrences/{key}/recovery-preview")
async def recovery_preview(key: UUID, request: Request, conversation_id: UUID):
    return await service(request).recovery_preview(str(key), str(conversation_id))


@router.post("/occurrences/{key}/resume")
async def resume(key: UUID, options: Resume, request: Request):
    return await service(request).resume(
        str(key), str(options.conversation_id), options.revision, options.binding
    )


@router.get("/occurrences/{key}/approval-overview")
async def approval_overview(key: UUID, request: Request, conversation_id: UUID):
    return service(request).approval_overview(str(key), str(conversation_id))


@router.post("/sources")
async def register_source(options: Source, request: Request):
    scheduler = service(request)
    return scheduler.watch.register(
        str(options.conversation_id),
        options.kind,
        scheduler.now(),
        str(options.workflow_run_id) if options.workflow_run_id else None,
    )


@router.post("/sources/{key}/revoke")
async def revoke(key: UUID, options: Owner, request: Request):
    service(request).watch.revoke(str(key), str(options.conversation_id))
    return {"revoked": True}


@router.get("/notifications")
async def notifications(request: Request, conversation_id: UUID):
    return {"items": SchedulerNotifications(service(request).store).listing(str(conversation_id))}


@router.get("/notifications/pending")
async def pending(request: Request):
    return {"items": SchedulerNotifications(service(request).store).pending()}


@router.post("/notifications/{key}/claim")
async def claim(key: UUID, request: Request):
    return SchedulerNotifications(service(request).store).claim(str(key))


@router.post("/notifications/{key}/delivery")
async def delivered(key: UUID, options: Delivery, request: Request):
    return SchedulerNotifications(service(request).store).report(str(key), options.result)
