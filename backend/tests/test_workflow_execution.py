import asyncio
import json
from uuid import uuid4
import pytest
from app.services.workflows.contracts import Node, Edge, WorkflowError
from app.services.trace.repository import TraceRepository
from workflow_fixture import host, graph, start, settle


def test_linear_real_coordinator_trace_and_persistence(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, trace):
            key = start(app, owner, graph())
            result = await settle(app, owner, key)
            assert result["state"] == "COMPLETED", result
            assert result["output"] == {"finish": "Hello Studio"}
            await trace.flush()
            with trace.factory() as db:
                from app.models.agent_trace import AgentTrace, TraceEvent
                from sqlalchemy import select

                events = list(
                    db.scalars(select(TraceEvent).where(TraceEvent.trace_id == result["trace_id"]))
                )
                parent = db.get(AgentTrace, result["trace_id"])
                assert parent.task_id == result["task_id"] and parent.conversation_id == owner
            assert any(e.event_type == "WORKFLOW_NODE_COMPLETED" for e in events)
            assert any(e.event_type == "TASK_COMPLETED" for e in events)
            assert not app.state.runtime_registry.active
            assert len({r["node_execution_id"] for r in result["nodes"]}) == 3
            assert all(e.trace_id == result["trace_id"] for e in events)

    asyncio.run(run())


@pytest.mark.parametrize("choice", [True, False])
def test_branch_merge_never_waits_for_skipped_path(tmp_path, choice):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            definition = graph()
            definition.nodes[1] = Node(
                node_id="step",
                type="condition",
                config={"expression": {"op": "equals", "left": choice, "right": True}},
            )
            definition.nodes += [
                Node(
                    node_id="yes",
                    type="transform",
                    config={"operation": "concat", "value": ["yes"]},
                ),
                Node(
                    node_id="no", type="transform", config={"operation": "concat", "value": ["no"]}
                ),
                Node(node_id="join", type="merge", config={"mode": "first_available"}),
            ]
            definition.nodes[2].config = {"value": {"$ref": "nodes.join.output"}}
            definition.edges = [
                definition.edges[0],
                Edge(
                    edge_id="yes", source_node_id="step", source_port="true", target_node_id="yes"
                ),
                Edge(edge_id="no", source_node_id="step", source_port="false", target_node_id="no"),
                Edge(edge_id="join_y", source_node_id="yes", target_node_id="join"),
                Edge(edge_id="join_n", source_node_id="no", target_node_id="join"),
                Edge(edge_id="end", source_node_id="join", target_node_id="finish"),
            ]
            key = start(app, owner, definition)
            result = await settle(app, owner, key)
            assert result["state"] == "COMPLETED", result
            assert result["output"]["finish"] == ("yes" if choice else "no")
            assert (
                next(n for n in result["nodes"] if n["node_id"] == ("no" if choice else "yes"))[
                    "state"
                ]
                == "SKIPPED"
            )

    asyncio.run(run())


def test_original_tool_runtime_and_checkpoint(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, trace):
            key = start(
                app,
                owner,
                graph("tool", {"name": "workspace.read", "arguments": {"path": "fixture.txt"}}),
            )
            result = await settle(app, owner, key)
            assert result["state"] == "COMPLETED", result
            assert result["output"]["finish"]["content"] == "Synthetic workflow file.\n"
            node = next(n for n in result["nodes"] if n["node_id"] == "step")
            detail = app.state.checkpoint_manager.detail(node["recovery_task_id"], owner)
            assert detail["checkpoints"][0]["step_state"] == "COMMITTED"
            assert not app.state.task_runtime.handles
            await trace.flush()

    asyncio.run(run())


@pytest.mark.parametrize("approved", [True, False])
def test_human_wait_requires_matching_owner_and_nonce(tmp_path, approved):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            key = start(
                app, owner, graph("human_approval", {"message": "确认合成内容", "value": "fixture"})
            )
            waiting = await settle(app, owner, key, waiting=True)
            assert waiting["state"] == "WAITING_PERMISSION"
            pending = next(n["pending"] for n in waiting["nodes"] if n.get("pending"))
            with pytest.raises(WorkflowError):
                app.state.workflows.approve(key, str(uuid4()), pending["approval_id"], True)
            with pytest.raises(WorkflowError):
                app.state.workflows.approve(key, owner, str(uuid4()), True)
            app.state.workflows.approve(key, owner, pending["approval_id"], approved)
            result = await settle(app, owner, key)
            assert result["state"] == ("COMPLETED" if approved else "FAILED")
            assert not app.state.workflows.approvals

    asyncio.run(run())


def test_timeout_cancel_and_no_leaked_quota(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            definition = graph("human_approval", {"message": "timeout"})
            definition.nodes[1].timeout = 0.08
            key = start(app, owner, definition)
            result = await settle(app, owner, key)
            assert result["state"] == "FAILED" and result["error_code"] == "TIMEOUT", result
            key = start(app, owner, graph("human_approval", {"message": "cancel"}))
            await settle(app, owner, key, waiting=True)
            await app.state.workflows.control(key, owner, "cancel")
            assert app.state.workflows.repository.run(key, owner)["state"] == "CANCELLED"
            assert not app.state.runtime_registry.active and not app.state.workflows.approvals
            assert app.state.runtime_registry.gates["workflow"]._value == 2

    asyncio.run(run())


def test_model_and_resume_pure_nodes_without_replay(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            key = start(
                app, owner, graph("llm_agent", {"instruction": "分析合成输入", "value": "fixture"})
            )
            result = await settle(app, owner, key)
            assert result["state"] == "COMPLETED", result
            assert result["model_calls"] == 1
            key = start(app, owner, graph("human_approval", {"message": "resume"}))
            await settle(app, owner, key, waiting=True)
            app.state.workflows.closing = True
            task = app.state.workflows.tasks[key]
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)
            app.state.workflows.closing = False
            preview = await app.state.workflows.recovery.preview(key, owner)
            assert preview["can_resume"], preview
            await app.state.workflows.resume(key, owner, preview["revision"])
            resumed = await settle(app, owner, key, waiting=True)
            assert next(n for n in resumed["nodes"] if n["node_id"] == "start")["attempt"] == 1
            await app.state.workflows.control(key, owner, "cancel")

    asyncio.run(run())
