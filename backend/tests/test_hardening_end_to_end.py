"""New closed loops through original services; model responses are synthetic.

Native Computer Use is E2E-006 in the separate real GUI harness. These tests do
not claim that a mock desktop, marker PCM or mock vectors establish device QA.
"""

import asyncio
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from unified_skill_fixture import unified_host
from unified_skill_scenarios import complete_schedule, report_graph, scheduled_run
from workflow_fixture import settle, start
from app.database.migrations import migrate
from app.services.evaluation.integration_observation import evaluate_workflow_artifact
from app.services.evaluation.repository import EvaluationRepository
from app.services.scheduling.notifications import SchedulerNotifications
from app.services.workflows.contracts import Edge, Node, WorkflowError


def test_E2E_001_api_plan_routing_tool_goal_trace_and_no_reexecute(tmp_path):
    from app.api.agent import router

    async def run():
        async with unified_host(tmp_path) as h:
            h.app.include_router(router)
            original = (h.workspace / "fixture.txt").read_bytes()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=h.app), base_url="http://127.0.0.1"
            ) as client:
                response = await client.post(
                    "/api/agent/plan",
                    json={
                        "character_id": "assistant",
                        "conversation_id": h.owner,
                        "message": "读取 fixture.txt 并报告实际内容",
                    },
                )
                assert response.status_code == 200, response.text
                plan = response.json()
                payload = {
                    "plan_id": plan["plan_id"],
                    "approved_ids": [item["id"] for item in plan["calls"]],
                    "voice_enabled": False,
                }
                result = await client.post("/api/agent/execute", json=payload)
                assert result.status_code == 200, result.text
                assert len(result.json()["tools"]) == 1
                assert original.decode().strip() in result.json()["tools"][0]["result"]["content"]
                calls = len(h.requests)
                again = await client.post("/api/agent/execute", json=payload)
                assert again.status_code in {404, 409, 422}
                assert (
                    len(h.requests) == calls
                    and (h.workspace / "fixture.txt").read_bytes() == original
                )
            await h.trace.flush()
            traces = h.app.state.trace_repository.listing(h.owner, task_id=plan["plan_id"])["items"]
            assert len(traces) == 1 and traces[0]["status"] == "completed"
            events = h.app.state.trace_repository.events(traces[0]["trace_id"], h.owner)["items"]
            assert {row["event_type"] for row in events} >= {
                "MODEL_ROUTING_DECIDED",
                "TOOL_COMPLETED",
            }
            assert not h.app.state.runtime_registry.active

    asyncio.run(run())


def test_E2E_002_mcp_failure_same_workflow_recovery_and_artifact_evaluation(tmp_path, monkeypatch):
    async def run():
        async with unified_host(tmp_path) as h:
            collection = await h.indexed()
            tool = await h.connect_mcp()
            manager = h.app.state.mcp_connections
            invoke = manager.invoke
            failing, attempts, dispatched = [True], [], []

            async def controlled_invoke(server, method, *args, **kwargs):
                if server == tool["server_id"] and method == "call_tool":
                    attempts.append("failed_before_dispatch" if failing[0] else "actual_dispatch")
                    if failing[0]:
                        from app.services.mcp_ext.contracts import MCPError

                        raise MCPError("MCP_TOOL_TIMEOUT")
                    dispatched.append(True)
                return await invoke(server, method, *args, **kwargs)

            monkeypatch.setattr(manager, "invoke", controlled_invoke)
            definition = report_graph(collection)
            definition.nodes.append(
                Node(
                    node_id="mcp",
                    type="mcp_tool",
                    config={
                        "tool_id": tool["id"],
                        "server_id": tool["server_id"],
                        "schema_fingerprint": tool["schema_fingerprint"],
                        "arguments": {"text": "verified same-workflow MCP"},
                    },
                )
            )
            definition.edges[0] = Edge(
                edge_id="mcp-first", source_node_id="start", target_node_id="mcp"
            )
            definition.edges.append(
                Edge(edge_id="mcp-search", source_node_id="mcp", target_node_id="search")
            )
            key = await scheduled_run(h, definition)

            def approve_extension(pending):
                if pending["kind"] == "tool" and pending["call"]["name"].startswith("mcp."):
                    from app.services.trusted_actions import action_for, trusted_actions

                    trusted_actions.grant_extension(
                        action_for(pending["call"]),
                        "once",
                        pending["work_session_id"] + ":" + pending["call"]["id"],
                        readonly=True,
                    )

            _, failed, first_approvals = await complete_schedule(h, key, approve_extension)
            assert failed["state"] == "FAILED" and not dispatched, failed
            assert not (h.workspace / "report.md").exists() and not h.requests
            failed_step = next(row for row in failed["nodes"] if row["node_id"] == "mcp")
            assert h.app.state.checkpoint_manager.detail(failed_step["recovery_task_id"], h.owner)[
                "checkpoints"
            ]
            preview = await h.scheduler.recovery_preview(key, h.owner)
            assert preview["can_resume"], preview
            failing[0] = False
            resumed = await h.scheduler.resume(key, h.owner, preview["revision"])
            assert resumed["workflow_run_id"] == failed["run_id"]
            final, workflow, approvals = await complete_schedule(h, key, approve_extension)
            assert final["status"] == workflow["state"] == "COMPLETED", workflow
            assert (
                workflow["tool_calls"] == 4 and approvals
            )  # failed attempt + three successful tools
            assert first_approvals.isdisjoint(approvals)
            assert workflow["run_id"] == failed["run_id"] and workflow["attempt"] == 2
            assert attempts.count("actual_dispatch") == len(dispatched) == 1
            assert "failed_before_dispatch" in attempts
            step = next(row for row in workflow["nodes"] if row["node_id"] == "mcp")
            checkpoint = h.app.state.checkpoint_manager.detail(step["recovery_task_id"], h.owner)
            assert checkpoint["checkpoints"][0]["step_state"] == "COMMITTED"
            history = len(h.app.state.mcp_connections.connections[tool["server_id"]].history)
            with pytest.raises(WorkflowError, match="WORKFLOW_NOT_RESUMABLE"):
                await h.app.state.workflows.recovery.preview(workflow["run_id"], h.owner)
            assert (
                len(h.app.state.mcp_connections.connections[tool["server_id"]].history) == history
            )
            qa_engine = create_engine(f"sqlite:///{(tmp_path / 'mcp-evaluation.db').as_posix()}")
            try:
                migrate(qa_engine)
                observed = evaluate_workflow_artifact(
                    h.app,
                    workflow["run_id"],
                    h.owner,
                    EvaluationRepository(sessionmaker(qa_engine)),
                )
                assert observed["passed_cases"] == 1
            finally:
                qa_engine.dispose()

    asyncio.run(run())


def test_E2E_003_rag_output_citation_invalidated_by_source_change(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            collection = await h.indexed()
            key = start(h.app, h.owner, report_graph(collection, second_model=True))
            workflow = await settle(h.app, h.owner, key)
            assert workflow["state"] == "COMPLETED"
            text = workflow["output"]["finish"]
            assert "8794" in text and "[K:" in text
            refs = next(row for row in workflow["nodes"] if row["node_id"] == "search")["output"]
            from app.services.knowledge.runtime import references, validate_references
            from app.services.knowledge.contracts import KnowledgeError

            assert validate_references(
                h.app.state.knowledge, references(refs), h.owner, "assistant"
            )
            (h.workspace / "sources/proof.md").write_text(
                "Changed fixture source", encoding="utf-8"
            )
            with pytest.raises(KnowledgeError):
                validate_references(h.app.state.knowledge, references(refs), h.owner, "assistant")

    asyncio.run(run())


def test_E2E_004_and_010_scheduled_report_notification_trace_independent_tamper_grading(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            collection = await h.indexed()
            key = await scheduled_run(h, report_graph(collection))
            final, workflow, _ = await complete_schedule(h, key)
            assert final["workflow_run_id"] == workflow["run_id"]
            path = h.workspace / "report.md"
            artifact = workflow["output"]["finish"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
            notices = SchedulerNotifications(h.scheduler.store).listing(h.owner)
            assert any(row["occurrence_id"] == key for row in notices)
            traces = h.app.state.trace_repository.listing(h.owner, task_id=workflow["task_id"])[
                "items"
            ]
            assert traces and all(row["conversation_id"] == h.owner for row in traces)
            evaluation_engine = create_engine(
                f"sqlite:///{(tmp_path / 'demo-evaluation.db').as_posix()}"
            )
            try:
                migrate(evaluation_engine)
                repository = EvaluationRepository(sessionmaker(evaluation_engine))
                result = evaluate_workflow_artifact(h.app, workflow["run_id"], h.owner, repository)
                assert result["passed_cases"] == 1
                path.write_text("tampered output", encoding="utf-8")
                result = evaluate_workflow_artifact(h.app, workflow["run_id"], h.owner, repository)
                assert result["failed_cases"] == 1 and len(h.requests) == 1
            finally:
                evaluation_engine.dispose()

    asyncio.run(run())


def test_E2E_007_provider_budget_failure_has_no_tool_side_effect(tmp_path, monkeypatch):
    from unified_skill_scenarios import chain
    from app.services.coding.workspace import CodingWorkspaceExecutor
    from app.services.llm.routing.metrics import ModelMetrics

    async def run():
        async with unified_host(tmp_path) as h:
            # Control only the health clock so each scenario deliberately starts
            # outside the prior request's cooldown. Never clear budget history.
            clock = [0.0]
            h.fixture.metrics = ModelMetrics(clock=lambda: clock[0])
            h.fixture.models.clear()
            h.fixture.add("failing", priority=9, responder=lambda _: httpx.Response(503))
            h.fixture.add("fallback", responder=h.respond)
            h.fixture.state(
                fallback_enabled=True, preference="QUALITY_FIRST", max_requests_per_task=1
            )
            dispatches = []
            original = CodingWorkspaceExecutor.execute

            def execute(executor, call):
                dispatches.append(call["name"])
                return original(executor, call)

            monkeypatch.setattr(CodingWorkspaceExecutor, "execute", execute)

            def definition():
                return chain(
                    Node(
                        node_id="model",
                        type="llm_agent",
                        config={"instruction": "Analyze synthetic input", "value": {}},
                    ),
                    Node(
                        node_id="write",
                        type="tool",
                        config={
                            "name": "workspace.create",
                            "coding_mode": "write_workspace",
                            "arguments": {"path": "budget-proof.txt", "content": "owned once"},
                        },
                    ),
                    Node(node_id="finish", type="output", config={"value": "done"}),
                )

            key = await scheduled_run(h, definition())
            _, denied, _ = await complete_schedule(h, key)
            assert denied["state"] == "FAILED" and denied["error_code"] == "TASK_REQUEST_LIMIT", (
                denied
            )
            assert not dispatches and not (h.workspace / "budget-proof.txt").exists()
            assert not h.fixture.calls["fallback"]
            assert len(h.fixture.calls["failing"]) == 1
            clock[0] = 10.0
            h.fixture.state(
                fallback_enabled=True, preference="QUALITY_FIRST", max_requests_per_task=2
            )
            key = await scheduled_run(h, definition())
            _, result, _ = await complete_schedule(h, key)
            # Router budget permits another attempt, but workflow usage is
            # unknown after HTTP503. The original fail-closed usage guard must
            # still stop before sending a fallback or writing the artifact.
            assert (
                result["state"] == "FAILED" and result["error_code"] == "WORKFLOW_MODEL_BUDGET"
            ), result
            assert not dispatches and not (h.workspace / "budget-proof.txt").exists()
            assert len(h.fixture.calls["failing"]) == 2 and not h.fixture.calls["fallback"]
            from sqlalchemy import select
            from app.database.session import SessionLocal
            from app.models.model_routing import ModelRequestRow

            with SessionLocal() as db:
                rows = db.scalars(
                    select(ModelRequestRow).where(ModelRequestRow.workflow_id == result["run_id"])
                ).all()
            assert (
                len(rows) == 2
                and sum(json.loads(row.decision_json).get("is_fallback", False) for row in rows)
                == 1
            )
            assert {row.status for row in rows} == {"FAILED_UNKNOWN"}
            # A different, healthy model and a new workflow can proceed. Do
            # not erase durable budget history or replay the failed workflow.
            h.fixture.add("healthy", priority=10, responder=h.respond)
            h.fixture.state(
                fallback_enabled=True, preference="QUALITY_FIRST", max_requests_per_task=2
            )
            key = await scheduled_run(h, definition())
            _, result, _ = await complete_schedule(h, key)
            assert result["state"] == "COMPLETED", result
            assert dispatches == ["workspace.create"] and len(h.fixture.calls["healthy"]) == 1
            write = next(node for node in result["nodes"] if node["node_id"] == "write")
            detail = h.app.state.checkpoint_manager.detail(write["recovery_task_id"], h.owner)
            assert len(detail["checkpoints"]) == 1
            assert (h.workspace / "budget-proof.txt").read_text(encoding="utf-8") == "owned once"

    asyncio.run(run())
