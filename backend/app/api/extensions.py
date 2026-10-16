"""Owner-only extension administration. No arbitrary shell, URL fetch or tool execution API."""

import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.agent_traces import authorized
from app.services.mcp_ext.contracts import MCPError, ServerConfig, bounded_json
from app.services.mcp_ext.adapter import clean


def available(request: Request):
    if getattr(request.app.state, "mcp_connections", None) is None:
        raise HTTPException(503, detail={"code": "EXTENSION_STORAGE_UNAVAILABLE"})


router = APIRouter(tags=["extensions"], dependencies=[Depends(authorized), Depends(available)])


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectRequest(Payload):
    approved_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ToolPreference(Payload):
    enabled: bool
    risk_level: Literal[
        "READ_ONLY_LOCAL",
        "READ_ONLY_EXTERNAL",
        "WRITE_LOCAL",
        "WRITE_EXTERNAL",
        "PROCESS_EXECUTION",
        "SENSITIVE_DATA_ACCESS",
        "HIGH_RISK_ACTION",
    ]


class CredentialRequest(Payload):
    config_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    token: str = Field(default="", max_length=4096)
    environment: dict[str, str] = Field(default_factory=dict, max_length=12)


class SkillFolder(Payload):
    folder: str = Field(min_length=1, max_length=2048)
    expected_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    skill_id: UUID | None = None


class SkillEdit(Payload):
    name: str = Field(min_length=1, max_length=64)
    text: str = Field(max_length=32000)


class SkillEnabled(Payload):
    enabled: bool


class PreviewRequest(Payload):
    kind: Literal["resource", "prompt"]
    name: str = Field(min_length=1, max_length=2048)
    arguments: dict[str, str] = Field(default_factory=dict, max_length=16)
    approved: bool = False


class GrantRequest(Payload):
    conversation_id: UUID
    work_session_id: UUID
    call_id: str = Field(min_length=1, max_length=100)
    scope: Literal["once", "task", "session", "revoke"]


@router.post("/api/mcp/grants")
async def grant(request: Request, payload: GrantRequest):
    from app.services.trusted_actions import action_for, trusted_actions

    work = request.app.state.work_state
    try:
        session = work.snapshot(str(payload.work_session_id))
    except LookupError:
        raise HTTPException(404, detail={"code": "MCP_PERMISSION_DENIED"}) from None
    if not session or session["conversation_id"] != str(payload.conversation_id):
        raise HTTPException(404, detail={"code": "MCP_PERMISSION_DENIED"})
    if payload.scope == "revoke":
        trusted_actions.revoke_extensions(
            str(payload.work_session_id), str(payload.conversation_id)
        )
        return {"scope": "revoke", "parameters_bound": True}
    from app.database.session import SessionLocal
    from app.models import WorkSession
    import json

    with SessionLocal() as db:
        stored = json.loads(db.get(WorkSession, str(payload.work_session_id)).plan_json)
    call = next((row for row in stored.get("calls", []) if row["id"] == payload.call_id), None)
    if not call or not call["name"].startswith("mcp."):
        raise HTTPException(404, detail={"code": "MCP_TOOL_NOT_FOUND"})
    row = await invoke(request.app.state.external_tools.validate_call, call)
    owner = str(payload.work_session_id if payload.scope == "task" else payload.conversation_id)
    if payload.scope == "once":
        owner = str(payload.work_session_id) + ":" + payload.call_id
    await invoke(
        trusted_actions.grant_extension,
        action_for(call),
        payload.scope,
        owner,
        readonly=row["risk_level"].startswith("READ_ONLY_"),
    )
    return {"scope": payload.scope, "tool_id": call["name"], "parameters_bound": True}


@router.get("/api/mcp/plans/{plan_id}")
async def plan_scope(request: Request, plan_id: UUID):
    from app.database.session import SessionLocal
    from app.models import WorkSession
    from sqlalchemy import select, func
    import json

    with SessionLocal() as db:
        row = db.scalar(
            select(WorkSession).where(
                func.json_extract(WorkSession.plan_json, "$.plan_id") == str(plan_id)
            )
        )
        if not row:
            raise HTTPException(404, detail={"code": "MCP_PLAN_NOT_FOUND"})
        stored = json.loads(row.plan_json)
        calls = [call for call in stored.get("calls", []) if call["name"].startswith("mcp.")]
        return {"conversation_id": row.conversation_id, "work_session_id": row.id, "calls": calls}


async def invoke(target, *args, **kwargs):
    try:
        value = target(*args, **kwargs)
        return await value if asyncio.iscoroutine(value) else value
    except MCPError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code}) from None
    except SQLAlchemyError:
        raise HTTPException(503, detail={"code": "EXTENSION_STORAGE_UNAVAILABLE"}) from None
    except (OSError, ValueError, TypeError, KeyError):
        raise HTTPException(409, detail={"code": "EXTENSION_INVALID"}) from None


@router.get("/api/mcp/servers")
async def servers(request: Request):
    manager = request.app.state.mcp_connections
    return {"items": [manager.status(row["id"]) for row in manager.store.listing("server")]}


@router.post("/api/mcp/servers")
async def create_server(request: Request, payload: ServerConfig):
    key = await invoke(request.app.state.mcp_connections.save, payload)
    return request.app.state.mcp_connections.status(key)


@router.patch("/api/mcp/servers/{server_id}")
async def edit_server(request: Request, server_id: UUID, payload: ServerConfig):
    key = await invoke(request.app.state.mcp_connections.save, payload, str(server_id))
    return request.app.state.mcp_connections.status(key)


@router.delete("/api/mcp/servers/{server_id}")
async def delete_server(request: Request, server_id: UUID):
    await invoke(request.app.state.mcp_connections.remove, str(server_id))
    return {"removed": True}


@router.post("/api/mcp/servers/{server_id}/credentials")
async def credentials(request: Request, server_id: UUID, payload: CredentialRequest):
    manager = request.app.state.mcp_connections
    config = await invoke(manager.config, str(server_id))
    from app.services.mcp_ext.contracts import digest

    if payload.config_fingerprint != digest(config.model_dump()):
        raise HTTPException(409, detail={"code": "MCP_CREDENTIAL_SCOPE_CHANGED"})
    if (
        set(payload.environment) - set(config.environment_names)
        or any(len(v) > 4096 or "\x00" in v for v in payload.environment.values())
        or "\r" in payload.token
        or "\n" in payload.token
    ):
        raise HTTPException(422, detail={"code": "MCP_CREDENTIAL_INVALID"})
    manager.credentials[str(server_id)] = payload.model_dump()
    return {"saved_in_memory": True}


@router.post("/api/mcp/servers/{server_id}/connect")
async def connect_server(request: Request, server_id: UUID, payload: ConnectRequest):
    return await invoke(
        request.app.state.mcp_connections.connect,
        str(server_id),
        approved_fingerprint=payload.approved_fingerprint,
    )


@router.post("/api/mcp/servers/{server_id}/disconnect")
async def disconnect_server(request: Request, server_id: UUID):
    await invoke(request.app.state.mcp_connections.disconnect, str(server_id))
    return {"disconnected": True}


@router.post("/api/mcp/servers/{server_id}/refresh")
async def refresh_server(request: Request, server_id: UUID):
    key = str(server_id)
    # Discovery passes through the same bounded invocation/timeout boundary.
    await invoke(request.app.state.external_tools.discover, key)
    return {"items": request.app.state.external_tools.listing(key)}


@router.get("/api/mcp/servers/{server_id}/tools")
async def tools(request: Request, server_id: UUID):
    return {"items": await invoke(request.app.state.external_tools.listing, str(server_id))}


@router.patch("/api/mcp/tools/{tool_id}")
async def tool_preference(request: Request, tool_id: str, payload: ToolPreference):
    if len(tool_id) != 44 or not tool_id.startswith("mcp."):
        raise HTTPException(422, detail={"code": "MCP_TOOL_NOT_FOUND"})
    return await invoke(
        request.app.state.external_tools.configure, tool_id, payload.enabled, payload.risk_level
    )


@router.get("/api/mcp/servers/{server_id}/catalog")
async def catalog(request: Request, server_id: UUID):
    manager = request.app.state.mcp_connections
    key = str(server_id)
    state = await invoke(manager.status, key)
    result = {"resources": [], "prompts": [], "capabilities": state["capabilities"]}
    creds = manager.credentials.get(key, {})
    secrets = [creds.get("token", ""), *creds.get("environment", {}).values()]
    for kind in ("resources", "prompts"):
        if state["capabilities"].get(kind) is not None:
            page = await invoke(manager.invoke, key, "list_" + kind)
            raw = bounded_json(page.model_dump(mode="json", by_alias=True, exclude_none=True))
            result[kind] = clean(raw.get(kind, [])[:50], secrets)
    return result


@router.post("/api/mcp/servers/{server_id}/preview")
async def preview_external(request: Request, server_id: UUID, payload: PreviewRequest):
    if not payload.approved:
        raise HTTPException(403, detail={"code": "MCP_PERMISSION_DENIED"})
    if any(len(value) > 2000 for value in payload.arguments.values()):
        raise HTTPException(422, detail={"code": "MCP_ARGUMENT_INVALID"})
    entries = await catalog(request, server_id)
    key = str(server_id)
    manager = request.app.state.mcp_connections
    if payload.kind == "resource":
        if not any(row.get("uri") == payload.name for row in entries["resources"]):
            raise HTTPException(403, detail={"code": "MCP_RESOURCE_DENIED"})
        result = await invoke(manager.invoke, key, "read_resource", payload.name)
    else:
        if not any(row.get("name") == payload.name for row in entries["prompts"]):
            raise HTTPException(403, detail={"code": "MCP_PROMPT_DENIED"})
        result = await invoke(manager.invoke, key, "get_prompt", payload.name, payload.arguments)
    raw = bounded_json(
        result.model_dump(mode="json", by_alias=True, exclude_none=True), limit=64000
    )
    creds = manager.credentials.get(key, {})
    raw = clean(raw, [creds.get("token", ""), *creds.get("environment", {}).values()])
    return {
        "server_id": key,
        "trust": "untrusted_external_data",
        "preview": raw,
        "auto_execute": False,
    }


@router.get("/api/mcp/export-template")
async def export_template(request: Request):
    # Deliberately excludes executable paths, args, credential references and env.
    return {
        "schema_version": 1,
        "servers": [
            {
                "display_name": "MCP Server " + str(index + 1),
                "transport": row["config"]["transport"],
                "endpoint_or_command": "REPLACE_BEFORE_ENABLE",
                "enabled": False,
            }
            for index, row in enumerate(request.app.state.extension_store.listing("server"))
        ],
    }


@router.get("/api/skills")
async def skills(request: Request):
    return {"items": await invoke(request.app.state.agent_skills.listing)}


@router.post("/api/skills/preview")
async def skill_preview(request: Request, payload: SkillFolder):
    return await invoke(request.app.state.agent_skills.preview, payload.folder)


@router.post("/api/skills/import")
async def skill_import(request: Request, payload: SkillFolder):
    return await invoke(
        request.app.state.agent_skills.import_folder,
        payload.folder,
        payload.expected_hash,
        skill_id=str(payload.skill_id) if payload.skill_id else None,
    )


@router.post("/api/skills")
async def skill_create(request: Request, payload: SkillEdit):
    return await invoke(request.app.state.agent_skills.create, payload.name, payload.text)


@router.get("/api/skills/{skill_id}")
async def skill_detail(request: Request, skill_id: UUID):
    return await invoke(request.app.state.agent_skills.detail, str(skill_id))


@router.patch("/api/skills/{skill_id}")
async def skill_enable(request: Request, skill_id: UUID, payload: SkillEnabled):
    return await invoke(request.app.state.agent_skills.enable, str(skill_id), payload.enabled)


@router.put("/api/skills/{skill_id}")
async def skill_edit(request: Request, skill_id: UUID, payload: SkillEdit):
    return await invoke(
        request.app.state.agent_skills.create, payload.name, payload.text, skill_id=str(skill_id)
    )


@router.delete("/api/skills/{skill_id}")
async def skill_delete(request: Request, skill_id: UUID):
    await invoke(request.app.state.agent_skills.delete, str(skill_id))
    return {"removed": True}


@router.get("/api/skills/{skill_id}/file")
async def skill_file(request: Request, skill_id: UUID, name: str):
    return {"text": await invoke(request.app.state.agent_skills.read_file, str(skill_id), name)}
