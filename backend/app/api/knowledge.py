"""All administration and source reads require the desktop's private control token."""

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.agent_traces import authorized
from app.services.knowledge.context import model_target
from app.services.knowledge.contracts import CreateCollection, KnowledgeError, SearchQuery, Strict
from app.services.knowledge.memory import MemoryInput


class ProtectedKnowledgeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                size = request.headers.get("content-length", "0")
                if not size.isdecimal() or int(size) > 32000:
                    raise KnowledgeError("KNOWLEDGE_REQUEST_LIMIT", 413)
                return await handler(request)
            except KnowledgeError as error:
                return JSONResponse(
                    status_code=error.status, content={"detail": {"code": error.code}}
                )
            except SQLAlchemyError:
                return JSONResponse(
                    status_code=503, content={"detail": {"code": "KNOWLEDGE_STORAGE_UNAVAILABLE"}}
                )
            except (ValueError, OSError):
                return JSONResponse(
                    status_code=409,
                    content={"detail": {"code": "KNOWLEDGE_INVALID_OR_UNAVAILABLE"}},
                )

        return handle


router = APIRouter(
    prefix="/api/knowledge",
    tags=["knowledge"],
    route_class=ProtectedKnowledgeRoute,
    dependencies=[Depends(authorized)],
)


def service(request):
    value = getattr(request.app.state, "knowledge", None)
    if value is None:
        raise HTTPException(503, detail={"code": "KNOWLEDGE_UNAVAILABLE"})
    return value


class Owner(Strict):
    conversation_id: UUID
    character_id: str = Field(min_length=1, max_length=100)


class UpdateCollection(Strict):
    revision: int = Field(ge=1)
    payload: CreateCollection | None = None
    add_root: str | None = Field(default=None, max_length=2000)
    remove_root: UUID | None = None


class Grant(Owner):
    collection_ids: list[UUID] = Field(max_length=10)
    allow_model_transfer: bool = False
    expected_targets: list[str] = Field(default_factory=list, max_length=4)


def targets(request):
    roles = request.app.state.model_roles
    result = []
    for role in ("character_model", "planning_model", "tool_execution_model", "coding_model"):
        current = roles.service(role)
        config = getattr(current, "config", None)
        result.append(
            {
                "role": role,
                "fingerprint": model_target(current),
                "provider": getattr(config, "provider", "unconfigured"),
                "base_url": getattr(config, "base_url", "unconfigured"),
                "model": getattr(config, "model", "unconfigured"),
            }
        )
    return result


@router.get("/targets")
def model_targets(request: Request):
    return {"targets": targets(request)}


@router.get("/collections")
def collections(request: Request):
    return {"collections": service(request).store.list()}


@router.post("/collections", status_code=201)
def create(request: Request, payload: CreateCollection):
    return service(request).store.create(payload)


@router.patch("/collections/{collection_id}")
def update(request: Request, collection_id: UUID, payload: UpdateCollection):
    return service(request).store.update(
        str(collection_id),
        payload.revision,
        payload=payload.payload,
        add_root=payload.add_root,
        remove_root=payload.remove_root,
    )


@router.delete("/collections/{collection_id}")
def remove(request: Request, collection_id: UUID, revision: int):
    service(request).store.remove(str(collection_id), revision)
    return {"removed": True, "source_files_deleted": False}


@router.get("/collections/{collection_id}/jobs")
def jobs(request: Request, collection_id: UUID):
    return {"jobs": service(request).indexer.jobs(str(collection_id))}


class Index(Strict):
    full: bool = False


class Embedding(Strict):
    revision: int = Field(ge=1)
    mode: Literal["DISABLED", "LOCAL", "REMOTE"] = "DISABLED"
    endpoint: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=100)
    dimension: int = Field(default=384, ge=1, le=2048)
    revision_name: str = Field(default="", max_length=100)
    consent: bool = False


@router.post("/collections/{collection_id}/embedding")
def embedding(request: Request, collection_id: UUID, payload: Embedding):
    values = payload.model_dump(exclude={"revision", "revision_name"})
    values["revision"] = payload.revision_name
    return service(request).configure_embedding(str(collection_id), payload.revision, values)


@router.post("/collections/{collection_id}/index")
async def start_index(request: Request, collection_id: UUID, payload: Index):
    return {"job_id": await service(request).indexer.start(str(collection_id), full=payload.full)}


class Control(Strict):
    action: Literal["pause", "continue", "cancel"]


@router.post("/jobs/{job_id}/control")
def control(request: Request, job_id: UUID, payload: Control):
    service(request).indexer.control(str(job_id), payload.action)
    return {"accepted": True}


@router.get("/selection")
def selection(request: Request, conversation_id: UUID, character_id: str):
    current = service(request)
    grants = current.store.grants(str(conversation_id), character_id)
    return {
        "collection_ids": [r.collection_id for r in grants],
        "preference": current.preference(str(conversation_id), character_id),
        "transfer_approved": bool(grants) and all(r.model_targets_json != "[]" for r in grants),
    }


@router.post("/selection")
def set_selection(request: Request, payload: Grant):
    fingerprints = (
        sorted({r["fingerprint"] for r in targets(request)}) if payload.allow_model_transfer else []
    )
    if fingerprints != sorted(set(payload.expected_targets)):
        raise KnowledgeError("KNOWLEDGE_MODEL_TARGET_CHANGED", 403)
    return service(request).store.grant(
        str(payload.conversation_id), payload.character_id, payload.collection_ids, fingerprints
    )


class Preferences(Owner):
    rag_enabled: bool
    memory_enabled: bool


@router.post("/preferences")
def preferences(request: Request, payload: Preferences):
    return service(request).preference(
        str(payload.conversation_id),
        payload.character_id,
        {"rag_enabled": payload.rag_enabled, "memory_enabled": payload.memory_enabled},
    )


class Search(Owner):
    search: SearchQuery


@router.post("/search")
async def search(request: Request, payload: Search):
    return await asyncio.to_thread(
        service(request).retriever.search,
        payload.search,
        str(payload.conversation_id),
        payload.character_id,
    )


class Resolve(Owner):
    reference: dict = Field(max_length=20)


@router.post("/resolve")
def resolve(request: Request, payload: Resolve):
    return service(request).retriever.citations.resolve(
        payload.reference, str(payload.conversation_id), payload.character_id, include_path=True
    )


@router.get("/evidence/{message_id}")
def evidence(request: Request, message_id: UUID, conversation_id: UUID, character_id: str):
    return service(request).evidence(str(message_id), str(conversation_id), character_id)


class MemorySave(Owner):
    memory: MemoryInput
    memory_id: UUID | None = None


@router.post("/memories")
def memory_save(request: Request, payload: MemorySave):
    return service(request).memory.save(
        payload.memory,
        payload.character_id,
        str(payload.conversation_id),
        str(payload.memory_id) if payload.memory_id else None,
    )


@router.get("/memories")
def memories(
    request: Request,
    conversation_id: UUID,
    character_id: str,
    scope: Literal["GLOBAL_ASSISTANT", "CHARACTER", "CONVERSATION", "WORKSPACE"],
    owner_id: str,
):
    return {
        "memories": service(request).memory.list(
            scope, owner_id, character_id, str(conversation_id)
        )
    }


class MemoryRemove(Owner):
    scope: Literal["GLOBAL_ASSISTANT", "CHARACTER", "CONVERSATION", "WORKSPACE"]
    owner_id: str = Field(max_length=100)
    memory_id: UUID | None = None


@router.post("/memories/remove")
def memory_remove(request: Request, payload: MemoryRemove):
    return service(request).memory.remove(
        payload.scope,
        payload.owner_id,
        payload.character_id,
        str(payload.conversation_id),
        str(payload.memory_id) if payload.memory_id else None,
    )
