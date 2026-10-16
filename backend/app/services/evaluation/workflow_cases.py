"""Twenty real coordinator cases inside the original owned Evaluation process.

Only model output and approval decisions are synthetic. SDK pipes, permissions,
runtime gates, SQLite, checkpoint reconciliation, Trace and grading really run.
"""

import asyncio
from pathlib import Path
import sys
import time
from uuid import uuid4

from app.database.session import SessionLocal
from app.models import Conversation
from app.services.evaluation.workflow_fixtures import WorkflowMockModel, linear, branch
from app.services.mcp_ext.adapter import MCPToolAdapter
from app.services.mcp_ext.connection import MCPConnectionManager
from app.services.mcp_ext.contracts import ServerConfig, digest
from app.services.mcp_ext.registry import ExternalToolRegistry
from app.services.mcp_ext.store import ExtensionStore
from app.services.skills.manager import AgentSkillManager
from app.services.trusted_actions import trusted_actions, action_for
from app.services.workflows.coordinator import WorkflowCoordinator
from app.services.workflows.contracts import Definition, Node, Edge, WorkflowError


async def settled(service, run_id, owner, *, waiting=False):
    for _ in range(600):
        run = service.repository.run(run_id, owner)
        if (
            run["state"] in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "NEEDS_REVIEW"}
            or waiting
            and run["state"] == "WAITING_PERMISSION"
        ):
            await asyncio.sleep(0)
            return service.repository.run(run_id, owner)
        await asyncio.sleep(0.01)
    raise WorkflowError("WORKFLOW_BENCHMARK_TIMEOUT")


async def run_case(app, trace_store, case, payload, workspace):
    state, index = app.state, int(case.case_id[-3:])
    started = time.perf_counter()
    store = ExtensionStore()
    state.mcp_connections = MCPConnectionManager(store, workspace / "mcp")
    state.external_tools = ExternalToolRegistry(store, state.mcp_connections)
    state.mcp_adapter = MCPToolAdapter(state.mcp_connections, state.external_tools)
    state.agent_skills = AgentSkillManager(store, workspace / "skills")
    state.llm_service.provider = WorkflowMockModel()
    state.workflows = service = WorkflowCoordinator(app)
    with SessionLocal() as db:
        row = Conversation(
            character_id="assistant", conversation_type="work", preferred_mode="work"
        )
        db.add(row)
        db.commit()
        owner = row.id
    session = state.work_state.ensure(owner)
    state.work_state.set_workspace(session, str(workspace))
    graph, expected, run_id = linear(), None, None
    evidence = {
        "real_gui": False,
        "boundary": "real_workflow_runtime_synthetic_model",
        "unauthorized_actions": 0,
    }
    timings = {}
    try:
        if index in {9, 10, 19}:
            args = (
                ["--mcp-demo"]
                if getattr(sys, "frozen", False)
                else ["-B", str(Path(__file__).parents[3] / "run.py"), "--mcp-demo"]
            )
            cfg = ServerConfig(
                display_name="Workflow Fixture",
                transport="stdio",
                endpoint_or_command=sys.executable,
                args=args,
                enabled=True,
            )
            server = state.mcp_connections.save(cfg)
            await state.mcp_connections.connect(
                server, approved_fingerprint=digest(cfg.model_dump())
            )
            await state.external_tools.discover(server)
            tool = next(
                t
                for t in state.external_tools.listing(server)
                if t["name"] == ("side_effect_unknown" if index == 19 else "echo")
            )
            state.external_tools.configure(
                tool["id"], True, "HIGH_RISK_ACTION" if index == 19 else "READ_ONLY_LOCAL"
            )
            graph = linear(
                "mcp_tool",
                {
                    "tool_id": tool["id"],
                    "server_id": server,
                    "schema_fingerprint": tool["schema_fingerprint"],
                    "arguments": {} if index == 19 else {"text": "workflow fixture"},
                },
            )
            if index == 10:
                await state.mcp_connections.disconnect(server)
                expected = "MCP_SERVER_UNAVAILABLE"
        if index == 1:
            saved = service.repository.save(Definition())
            evidence["condition_verified"] = (
                saved["revision"] == 1 and not saved["definition"]["nodes"]
            )
        elif index in {3, 4, 5, 10}:
            if index == 3:
                graph.nodes.append(
                    Node(
                        node_id="cycle",
                        type="transform",
                        config={"operation": "length", "value": []},
                    )
                )
                graph.edges.append(
                    Edge(edge_id="cycle", source_node_id="cycle", target_node_id="cycle")
                )
                expected = "WORKFLOW_CYCLE"
            elif index == 4:
                graph.edges[0].target_node_id = "absent"
                expected = "WORKFLOW_EDGE_INVALID"
            elif index == 5:
                graph.nodes[-1].config["value"] = {"$ref": "nodes.absent.output"}
                expected = "WORKFLOW_VARIABLE_SCOPE"
            try:
                service.validator.compile(graph)
            except ValueError as error:
                evidence["observed_code"] = getattr(error, "code", "WORKFLOW_INVALID")
            evidence["condition_verified"] = evidence.get("observed_code") == expected
        else:
            if index == 6:
                graph = linear(
                    "tool", {"name": "workspace.read", "arguments": {"path": "fixture.txt"}}
                )
            elif index in {7, 20}:
                graph = linear(
                    "llm_agent", {"instruction": "Summarize synthetic input", "value": "fixture"}
                )
            elif index == 8:
                text = "---\nname: workflow-review\ndescription: Synthetic workflow review\nmetadata:\n  companion-capabilities: read_project_files\n---\nAnalyze supplied synthetic data. Never run scripts.\n"
                skill = state.agent_skills.create("workflow-review", text)
                state.agent_skills.enable(skill["id"], True)
                graph = linear(
                    "agent_skill",
                    {
                        "skill_id": skill["id"],
                        "content_hash": skill["content_hash"],
                        "goal": "Review fixture data",
                        "value": "fixture",
                    },
                )
            elif index in {11, 12, 13}:
                graph = branch(index != 12)
            elif index in {14, 15, 16, 17}:
                graph = linear(
                    "human_approval",
                    {"message": "Synthetic benchmark approval", "value": "fixture"},
                )
                if index == 16:
                    graph.nodes[1].timeout = 0.06
            elif index == 18:
                graph = linear(
                    "tool", {"name": "workspace.read", "arguments": {"path": "fixture.txt"}}
                )
                graph.nodes.insert(
                    2,
                    Node(
                        node_id="wait",
                        type="human_approval",
                        config={"message": "Synthetic interruption"},
                    ),
                )
                graph.edges = [
                    graph.edges[0],
                    Edge(edge_id="wait", source_node_id="step", target_node_id="wait"),
                    Edge(edge_id="end", source_node_id="wait", target_node_id="finish"),
                ]
            before = time.perf_counter()
            pinned = service.catalog.pin(graph)
            service.validator.compile(pinned)
            timings["validation_ms"] = (time.perf_counter() - before) * 1000
            saved = service.repository.save(pinned)
            published = service.repository.publish(pinned, saved["revision"])
            run_id = service.start(
                graph.workflow_id, published["version_id"], owner, {}, "workflow-benchmark"
            )["run_id"]
            if index in {9, 14, 15, 17, 18, 19}:
                waiting = await settled(service, run_id, owner, waiting=True)
                pending = next(n["pending"] for n in waiting["nodes"] if n.get("pending"))
                if index in {9, 19}:
                    call = pending["call"]
                    # Synthetic user consent still uses the production argument/schema-bound grant.
                    trusted_actions.grant_extension(
                        action_for(call),
                        "once",
                        pending["work_session_id"] + ":" + call["id"],
                        readonly=index == 9,
                    )
                if index == 17:
                    await service.control(run_id, owner, "cancel")
                elif index == 18:
                    service.closing = True
                    task = service.tasks[run_id]
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await asyncio.sleep(0)
                    state.workflows = service = WorkflowCoordinator(app)
                    preview = await service.recovery.preview(run_id, owner)
                    evidence["checkpoint_can_resume"] = preview["can_resume"]
                    await service.resume(run_id, owner, preview["revision"])
                    waiting = await settled(service, run_id, owner, waiting=True)
                    pending = next(n["pending"] for n in waiting["nodes"] if n.get("pending"))
                    service.approve(run_id, owner, pending["approval_id"], True)
                else:
                    service.approve(run_id, owner, pending["approval_id"], index != 15)
            result = await settled(service, run_id, owner)
            evidence["run_state"] = result["state"]
            evidence["workflow_version"] = result["version"]
            evidence["model_calls"] = result["model_calls"]
            evidence["tool_calls"] = result["tool_calls"]
            if index == 15:
                verified = (
                    result["state"] == "FAILED" and result["error_code"] == "PERMISSION_DENIED"
                )
            elif index == 16:
                verified = result["state"] == "FAILED" and result["error_code"] == "TIMEOUT"
            elif index == 17:
                verified = result["state"] == "CANCELLED" and not service.approvals
            elif index == 19:
                preview = await service.recovery.preview(run_id, owner)
                connection = state.mcp_connections.connections[server]
                effect_file = connection.transport.workdir / "studio-mcp-effects.txt"
                count = (
                    effect_file.read_text(encoding="utf-8").count("effect")
                    if effect_file.exists()
                    else 0
                )
                evidence["side_effect_count"] = count
                verified = (
                    result["state"] == "NEEDS_REVIEW" and not preview["can_resume"] and count == 1
                )
            else:
                verified = result["state"] == "COMPLETED" and result["output"] is not None
                if index in {11, 12, 13}:
                    verified &= (
                        result["output"] == {"finish": "no" if index == 12 else "yes"}
                        and sum(n["state"] == "SKIPPED" for n in result["nodes"]) == 1
                    )
                if index == 6:
                    verified &= result["output"]["finish"]["content"].startswith(
                        "Public evaluation fixture"
                    )
                if index == 9:
                    verified &= (
                        result["output"]["finish"]["protocol_success"] is True
                        and result["output"]["finish"]["goal_verified"] is False
                    )
                if index == 18:
                    verified &= (
                        next(n for n in result["nodes"] if n["node_id"] == "step")["attempt"] == 1
                        and evidence["checkpoint_can_resume"]
                    )
            evidence["condition_verified"] = bool(verified)
        await trace_store.flush()
        traces = state.trace_repository.listing(owner)["items"] if run_id else []
        evidence["resources_released"] = (
            not state.runtime_registry.active
            and not service.approvals
            and not state.task_runtime.handles
        )
        evidence["trace_consistent"] = (
            bool(traces) and all(t["conversation_id"] == owner for t in traces) if run_id else True
        )
        if index == 20:
            events = state.trace_repository.events(traces[0]["trace_id"], owner)["items"]
            evidence["condition_verified"] &= (
                any(e["event_type"] == "WORKFLOW_NODE_COMPLETED" for e in events)
                and traces[0]["status"] == "completed"
            )
        from app.services.evaluation.grader import EvaluationGrader

        observation = {
            "evidence": evidence,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "workflow_metrics": {
                "validation_ms": timings.get("validation_ms"),
                "total_tokens": None,
                "estimated_cost": None,
                "real_model_quality": "not_measured",
            },
        }
        result_dto = EvaluationGrader().grade(case, observation, traces)
        result_dto["trace_scopes"] = [
            {k: t[k] for k in ("trace_id", "task_id", "conversation_id", "work_session_id")}
            for t in traces
        ]
        return {
            "result": result_dto,
            "links": traces[0] if traces else {},
            "budget": {"requests": 0, "total_tokens": None},
        }
    finally:
        await service.close()
