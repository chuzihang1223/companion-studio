"""Actual Skill boundaries used by router benchmarks inside their owned worker profile."""

import json
from uuid import uuid4
from fastapi import FastAPI
import httpx

from app.database.session import SessionLocal
from app.models import Conversation
from app.services.llm.routing.contracts import Requirements
from app.services.evaluation.router_fixtures import blocked


async def voice(index, f):
    from app.services.evaluation.voice_cases import SyntheticASR, signal, rejected
    from app.services.realtime_voice.contracts import Frame, Scope, StartRequest
    from app.services.realtime_voice.sessions import VoiceSessionManager

    with SessionLocal.begin() as db:
        owner = str(uuid4())
        db.add(Conversation(id=owner, character_id="assistant"))
    manager = VoiceSessionManager(f.app, SyntheticASR)
    options = StartRequest(owner_id=uuid4(), conversation_id=owner, character_id="assistant")
    identity = await manager.start(options)
    identity = await manager.begin(
        Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
    )
    scope = Scope.model_validate({key: identity[key] for key in Scope.model_fields})
    try:
        for sequence in range(3):
            manager.frame(Frame(**scope.model_dump(), **signal(sequence), timestamp=0))
        if index == 37:
            await manager.interrupt(scope)
            return {
                "cancelled_generation_rejected": rejected(lambda: manager.owned(scope, turn=True)),
                "zero_model_requests": not any(f.calls.values()),
            }
        await manager.finalize(scope)
        if index == 38:
            manager.claim(scope, owner, "assistant")
            return {
                "duplicate_claim_rejected": rejected(
                    lambda: manager.claim(scope, owner, "assistant")
                ),
                "zero_model_requests": not any(f.calls.values()),
            }
        selected = manager.owned(scope, turn=True).turn.model_routes.service("character_model")
        f.models.clear()
        f.add("next", api_model="next-synthetic")
        f.state()
        retained = manager.owned(scope, turn=True).turn.model_routes.service("character_model")
        await retained.chat([])
        return {
            "turn_retains_model": retained is selected and retained.config.model == "synthetic",
            "new_model_not_called": not f.calls["next"],
        }
    finally:
        await manager.close()


async def agent(f):
    from app.services.agent_tools import ToolRegistry

    f.models.clear()
    schema = next(
        tool for tool in ToolRegistry().schemas() if tool["function"]["name"] == "system_get_status"
    )
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "router-plan",
                "type": "function",
                "function": {"name": schema["function"]["name"], "arguments": "{}"},
            }
        ],
    }
    f.add(
        "planner", responder=lambda _: httpx.Response(200, json={"choices": [{"message": message}]})
    )
    f.state()
    with SessionLocal.begin() as db:
        owner = str(uuid4())
        db.add(Conversation(id=owner, character_id="assistant", conversation_type="work"))
    plan = await f.app.state.agent_service.plan(
        f.app, "assistant", owner, "Analyze the available local status and explain the result."
    )
    return {
        "real_agent_plan": len(plan["calls"]) == 1
        and plan["calls"][0]["name"] == "system.get_status",
        "routed_provider_used": len(f.calls["planner"]) == 1,
    }


async def workflow(f, workspace):
    from app.services.evaluation.scheduler_support import SchedulerFixture
    from app.services.evaluation.workflow_fixtures import linear

    fixture = SchedulerFixture(f.app, workspace)
    try:
        definition = fixture.definition(
            graph=linear("llm_agent", {"instruction": "Read synthetic input", "value": "fixture"})
        )
        service = f.app.state.workflows
        run = service.start(
            definition.workflow_id, definition.workflow_version_id, fixture.owner, {}, str(uuid4())
        )
        for task in tuple(service.tasks.values()):
            await task
        final = service.repository.run(run["run_id"], fixture.owner)
        return {
            "workflow_completed": final["state"] == "COMPLETED",
            "routed_model_called": len(f.calls["one"]) == 1,
            "workflow_budget_scope": f.ledger.recent()[0]["model_id"] == "one",
        }
    finally:
        await fixture.close()


async def scheduler(f, workspace):
    from app.services.evaluation.scheduler_support import SchedulerFixture
    from app.services.evaluation.workflow_fixtures import linear
    from app.services.scheduling.permissions import ScheduledPermissionAdapter

    fixture = SchedulerFixture(f.app, workspace)
    try:
        definition = fixture.definition(
            graph=linear("llm_agent", {"instruction": "Synthetic analysis", "value": "fixture"})
        )
        adapter = ScheduledPermissionAdapter(f.app)
        original = adapter.snapshot(definition)
        f.state(preference="LOW_COST")
        changed = adapter.snapshot(definition)
        return {
            "real_permission_snapshot_detects_router_change": original["dependencies_hash"]
            != changed["dependencies_hash"],
            "metadata_does_not_call_model": not any(f.calls.values()),
        }
    finally:
        await fixture.close()


async def skill(f, workspace):
    from app.services.mcp_ext.store import ExtensionStore
    from app.services.skills.manager import AgentSkillManager
    from app.services.evaluation.mcp_cases import SKILL

    manager = AgentSkillManager(ExtensionStore(), workspace / "router-skills")
    saved = manager.create("project-review", SKILL)
    manager.enable(saved["id"], True)
    selected = manager.select("$project-review", {"workspace.read"})
    # The Skill's capability contract is resolved by the original manager; it
    # cannot change the router's hard requirements or authorize an API target.
    service = f.app.state.model_roles.capture().service("character_model")
    await service.chat([{"role": "user", "content": selected["context"]}])
    denied = blocked(
        lambda: f.app.state.model_roles.service(
            "character_model",
            requirements=Requirements(privacy="PINNED", pinned_model_id="missing"),
        )
    )
    return {
        "skill_selected": bool(selected["skills"]),
        "router_permission_not_granted_by_skill": denied,
        "provider_called_once": len(f.calls["one"]) == 1,
    }


async def vision(f):
    from app.services.computer_use.vision import target, analyze
    from app.services.computer_use.contracts import ComputerUseError

    record, service = f.models[0]
    service.config.capabilities["vision"] = True
    f.state()
    selected, metadata = target(f.app)
    try:
        await analyze(f.app, "invalid-fingerprint", "data:image/png;base64,")
    except ComputerUseError:
        denied = True
    else:
        denied = False
    return {
        "vision_model_selected": selected.entry.record.model_id == "one",
        "wrong_target_denied_before_image": denied,
        "no_model_request": not any(f.calls.values()),
        "configured_vision": metadata["configured_vision"] is True,
    }


async def run(index, f, trace_store, workspace):
    if index in {37, 38, 46}:
        return await voice(index, f)
    if index == 39:
        return await agent(f)
    if index == 40:
        return await mcp(f, workspace)
    if index == 43:
        return await knowledge(f, trace_store, workspace)
    if index == 48:
        return await consistency(f)
    if index == 41:
        return await skill(f, workspace)
    if index == 42:
        return await workflow(f, workspace)
    if index == 44:
        return await vision(f)
    if index == 45:
        return await scheduler(f, workspace)
    if index == 47:
        from app.services.trace.scope import current_trace

        with SessionLocal.begin() as db:
            owner = str(uuid4())
            db.add(Conversation(id=owner, character_id="assistant", conversation_type="work"))
        trace = f.app.state.trace_recorder.create(str(uuid4()), owner)
        with f.app.state.trace_recorder._execution(trace):
            await f.app.state.model_roles.service("character_model").chat([])
        trace.finish()
        await trace_store.flush()
        tree = f.app.state.trace_repository.events(trace.trace_id, owner)
        return {
            "routing_trace_recorded": any(
                row["event_type"] == "MODEL_ROUTING_DECIDED" for row in tree["items"]
            ),
            "no_extra_model_attempt": len(f.calls["one"]) == 1,
            "trace_context_released": current_trace.get() is None,
        }
    return None


async def mcp(f, workspace):
    import sys
    from pathlib import Path
    from app.services.mcp_ext.adapter import MCPToolAdapter
    from app.services.mcp_ext.connection import MCPConnectionManager
    from app.services.mcp_ext.contracts import ServerConfig, digest
    from app.services.mcp_ext.registry import ExternalToolRegistry
    from app.services.mcp_ext.store import ExtensionStore
    from app.services.trusted_actions import trusted_actions, action_for

    store = ExtensionStore()
    manager = f.app.state.mcp_connections = MCPConnectionManager(store, workspace / "router-mcp")
    registry = f.app.state.external_tools = ExternalToolRegistry(store, manager)
    f.app.state.mcp_adapter = MCPToolAdapter(manager, registry)
    config = ServerConfig(
        display_name="Router synthetic MCP",
        transport="stdio",
        endpoint_or_command=sys.executable,
        args=["--mcp-demo"]
        if getattr(sys, "frozen", False)
        else ["-B", str(Path(__file__).resolve().parents[3] / "run.py"), "--mcp-demo"],
        enabled=True,
    )
    try:
        server = manager.save(config)
        await manager.connect(server, approved_fingerprint=digest(config.model_dump()))
        await registry.discover(server)
        tool = next(row for row in registry.listing(server) if row["name"] == "echo")
        registry.configure(tool["id"], True, "READ_ONLY_EXTERNAL")

        def respond(request):
            body = json.loads(request.content)
            message = {
                "role": "assistant",
                "content": "Synthetic MCP report; protocol success is separate from goal verification.",
            }
            if body.get("tools"):
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "router-mcp-call",
                            "type": "function",
                            "function": {
                                "name": tool["id"].replace(".", "_"),
                                "arguments": json.dumps({"text": "router MCP evidence"}),
                            },
                        }
                    ],
                }
            return httpx.Response(200, json={"choices": [{"message": message}]})

        f.models.clear()
        f.calls.clear()
        f.add(responder=respond)
        f.state()
        with SessionLocal.begin() as db:
            owner = str(uuid4())
            db.add(Conversation(id=owner, character_id="assistant", conversation_type="work"))
        plan = await f.app.state.agent_service.plan(
            f.app,
            "assistant",
            owner,
            "Use the provided synthetic echo tool to return router MCP evidence.",
        )
        for call in plan["calls"]:
            trusted_actions.grant_extension(
                action_for(call), "once", plan["work_session_id"] + ":" + call["id"], readonly=True
            )
        result = await f.app.state.agent_service.execute(
            f.app, plan["plan_id"], {call["id"] for call in plan["calls"]}, "natural", False, 1.0
        )
        tools = result.get("tools", [])
        f.observation["tool_calls"] = len(tools)
        f.observation["plan_id"] = plan["plan_id"]
        f.observation["owner_id"] = owner
        return {
            "routed_mcp_plan": len(plan["calls"]) == 1,
            "actual_stdio_response": len(tools) == 1
            and tools[0]["result"].get("protocol_success") is True,
            "original_model_path": len(f.calls["one"]) >= 1,
        }
    finally:
        await manager.close()


async def knowledge(f, trace_store, workspace):
    from app.services.evaluation.knowledge_cases import KnowledgeCase
    from app.services.knowledge.context import GroundedService
    from app.services.knowledge.contracts import KnowledgeError

    def respond(request):
        messages = json.loads(request.content)["messages"]
        for message in messages:
            text = message.get("content", "")
            marker = "UNTRUSTED_KNOWLEDGE_DATA\n"
            if isinstance(text, str) and marker in text:
                rows = json.loads(text.split(marker)[-1])
                chunk = rows[0]["chunk_id"]
                content = "Orchard launch port is 8794. [K:" + chunk + "]"
                return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "No evidence"}}]})

    f.models.clear()
    f.calls.clear()
    f.add(responder=respond)
    f.state()
    f.app.state.llm_service = f.models[0][1]
    fixture = KnowledgeCase(f.app, trace_store, workspace)
    try:
        fixture.setup()
        await fixture.index()
        rows = fixture.search("Orchard")["results"]
        selected = f.app.state.model_roles.capture().service("character_model")
        grounded = GroundedService(
            fixture.service, selected, rows, fixture.owner, "assistant", None
        )
        text = await grounded.chat(
            [{"role": "user", "content": "What is the Orchard launch port?"}]
        )
        f.add("other", api_model="different")
        f.state()
        other = f.app.state.model_roles.intelligent.service(
            "character_model", requirements=Requirements(privacy="PINNED", pinned_model_id="other")
        )
        try:
            await GroundedService(
                fixture.service, other, rows, fixture.owner, "assistant", None
            ).chat([])
        except KnowledgeError:
            denied = True
        else:
            denied = False
        return {
            "actual_citation_from_router": "[K:" in text and "8794" in text,
            "ungranted_target_blocked": denied,
            "other_provider_not_called": not f.calls["other"],
        }
    finally:
        await fixture.service.close()


async def consistency(f):
    from app.services.evaluation.registry import BenchmarkRegistry

    await f.app.state.model_roles.service("character_model").chat([])
    rows = f.ledger.recent()
    registered = f.app.state.model_roles.intelligent.registry.entries[0]
    metric = f.metrics.public(registered.version)
    dataset = BenchmarkRegistry("router-core")
    return {
        "attempt_count_matches_usage_ledger": len(rows)
        == len(f.calls["one"])
        == metric["successes"]
        == 1,
        "actual_usage_is_labelled": rows[0]["usage"]["status"] == "ACTUAL_PROVIDER_REPORTED",
        "dataset_identity_matches_runtime": dataset.public()["case_count"] == len(dataset.by_id),
    }
