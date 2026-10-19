"""Owned SDK pipe fixtures; no external server, model, or account is contacted."""

from pathlib import Path
import sys

from app.services.evaluation.workflow_fixtures import linear
from app.services.evaluation.workflow_cases import settled
from app.services.mcp_ext.adapter import MCPToolAdapter
from app.services.mcp_ext.connection import MCPConnectionManager
from app.services.mcp_ext.contracts import ServerConfig, digest
from app.services.mcp_ext.registry import ExternalToolRegistry
from app.services.mcp_ext.store import ExtensionStore
from app.services.skills.manager import AgentSkillManager
from app.services.trusted_actions import trusted_actions, action_for


async def run(fx, index):
    state, scheduler, owner = fx.app.state, fx.scheduler, fx.owner
    store = ExtensionStore()
    state.mcp_connections = MCPConnectionManager(store, fx.workspace / "mcp")
    state.external_tools = ExternalToolRegistry(store, state.mcp_connections)
    state.mcp_adapter = MCPToolAdapter(state.mcp_connections, state.external_tools)
    state.agent_skills = AgentSkillManager(store, fx.workspace / "skills")
    cfg = ServerConfig(
        display_name="Scheduler Fixture",
        transport="stdio",
        endpoint_or_command=sys.executable,
        args=["--mcp-demo"]
        if getattr(sys, "frozen", False)
        else ["-B", str(Path(__file__).parents[3] / "run.py"), "--mcp-demo"],
        enabled=True,
    )
    server = state.mcp_connections.save(cfg)
    await state.mcp_connections.connect(server, approved_fingerprint=digest(cfg.model_dump()))
    await state.external_tools.discover(server)
    tool = next(
        t
        for t in state.external_tools.listing(server)
        if t["name"] == ("side_effect_unknown" if index == 25 else "echo")
    )
    state.external_tools.configure(
        tool["id"], True, "HIGH_RISK_ACTION" if index == 25 else "READ_ONLY_LOCAL"
    )
    definition = fx.definition(
        graph=linear(
            "mcp_tool",
            {
                "tool_id": tool["id"],
                "server_id": server,
                "schema_fingerprint": tool["schema_fingerprint"],
                "arguments": {} if index == 25 else {"text": "synthetic"},
            },
        )
    )
    scheduler.save(definition)
    occurrence = scheduler.run_now(definition.schedule_id, owner)
    if index == 27:
        await state.mcp_connections.disconnect(server)
        await scheduler.tick()
        row = scheduler.store.occurrence(occurrence["occurrence_id"], owner)
        skill = state.agent_skills.create(
            "scheduler-fixture",
            "---\nname: scheduler-fixture\ndescription: Synthetic review\n---\nSummarize supplied synthetic data.\n",
        )
        state.agent_skills.enable(skill["id"], True)
        second = fx.definition(
            graph=linear(
                "agent_skill",
                {
                    "skill_id": skill["id"],
                    "content_hash": skill["content_hash"],
                    "goal": "Review fixture",
                    "value": "synthetic",
                },
            )
        )
        scheduler.save(second)
        other = scheduler.run_now(second.schedule_id, owner)
        state.agent_skills.enable(skill["id"], False)
        await scheduler.tick()
        denied = scheduler.store.occurrence(other["occurrence_id"], owner)
        return {
            "mcp_not_reconnected": state.mcp_connections.connections[server].client is None
            and state.mcp_connections.connections[server].owner is None,
            "mcp_no_execution": row["workflow_run_id"] is None,
            "disabled_skill_blocked": denied["workflow_run_id"] is None,
            "review_required": row["status"] == denied["status"] == "WAITING_PERMISSION",
        }
    await scheduler.tick()
    scheduler.grant_once(occurrence["occurrence_id"], owner)
    await scheduler.tick()
    current = scheduler.store.occurrence(occurrence["occurrence_id"], owner)
    waiting = await settled(state.workflows, current["workflow_run_id"], owner, waiting=True)
    pending = next(n["pending"] for n in waiting["nodes"] if n.get("pending"))
    call = pending["call"]
    trusted_actions.grant_extension(
        action_for(call), "once", pending["work_session_id"] + ":" + call["id"], readonly=False
    )
    state.workflows.approve(waiting["run_id"], owner, pending["approval_id"], True)
    final = await fx.complete(current)
    preview = await scheduler.recovery_preview(current["occurrence_id"], owner)
    effects = state.mcp_connections.connections[server].transport.workdir / "studio-mcp-effects.txt"
    count = effects.read_text(encoding="utf-8").count("effect") if effects.exists() else 0
    await scheduler.tick()
    return {
        "unknown_not_replayed": count == 1,
        "review_required": final["status"] == "NEEDS_REVIEW",
        "recovery_blocked": not preview["can_resume"],
        "one_run": len(fx.rows(definition)) == 1,
    }
