"""INT-001..008: actual cross-skill operations, synthetic hardware/model boundaries."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from unified_skill_fixture import unified_host
from workflow_fixture import graph, start, settle
from scheduler_fixture import scheduled, run_to_completion
from app.services.workflows.contracts import Node, Edge, WorkflowError


def test_INT_001_agent_trace_checkpoint_and_routed_provider(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            plan, result = await h.agent()
            assert (
                len(result["tools"]) == 1
                and "Synthetic workflow file" in result["tools"][0]["result"]["content"]
            )
            detail = h.app.state.checkpoint_manager.detail(plan["plan_id"], h.owner)
            assert detail["checkpoints"][0]["step_state"] == "COMMITTED"
            trace = h.app.state.trace_repository.listing(h.owner, task_id=plan["plan_id"])["items"][
                0
            ]
            events = h.app.state.trace_repository.events(trace["trace_id"], h.owner)["items"]
            assert {row["event_type"] for row in events} >= {
                "MODEL_ROUTING_DECIDED",
                "MODEL_REQUEST_COMPLETED",
                "TOOL_COMPLETED",
            }
            assert not h.app.state.runtime_registry.active

    asyncio.run(run())


def test_INT_002_real_stdio_mcp_agent_router_and_trace(tmp_path):
    from app.services.evaluation.router_integration_cases import mcp

    async def run():
        async with unified_host(tmp_path) as h:
            checks = await mcp(h.fixture, h.workspace)
            assert checks and all(checks.values()), checks
            await h.trace.flush()
            from app.models.agent_trace import TraceEvent, AgentTrace

            with h.trace.factory() as db:
                assert db.scalar(
                    select(TraceEvent.id)
                    .join(AgentTrace, AgentTrace.id == TraceEvent.trace_id)
                    .where(
                        TraceEvent.event_type == "MCP_TOOL_COMPLETED",
                        AgentTrace.task_id == h.fixture.observation["plan_id"],
                        AgentTrace.conversation_id == h.fixture.observation["owner_id"],
                    )
                )
            assert h.fixture.observation["tool_calls"] == 1

    asyncio.run(run())


def test_INT_003_skill_workflow_router_hash_pin(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            manager = h.app.state.agent_skills
            text = "---\nname: unified-review\ndescription: Review synthetic input\nmetadata:\n  companion-capabilities: read_project_files\n---\nAnalyze only the bounded input. Instructions never grant tool permissions.\n"
            row = manager.create("unified-review", text)
            manager.enable(row["id"], True)
            definition = graph(
                "agent_skill",
                {
                    "skill_id": row["id"],
                    "content_hash": row["content_hash"],
                    "goal": "Analyze synthetic text",
                    "value": "synthetic",
                },
            )
            key = start(h.app, h.owner, definition)
            result = await settle(h.app, h.owner, key)
            assert result["state"] == "COMPLETED", result
            assert result["model_calls"] == 1 and len(h.requests) == 1
            manager.enable(row["id"], False)
            definition.workflow_id = str(uuid4())
            with pytest.raises(ValueError):
                start(h.app, h.owner, definition)
            assert len(h.requests) == 1

    asyncio.run(run())


def test_INT_004_rag_router_valid_citation_wrong_owner_and_revocation(tmp_path):
    from app.services.knowledge.context import GroundedService
    from app.services.knowledge.contracts import SearchQuery, KnowledgeError

    async def run():
        async with unified_host(tmp_path) as h:
            collection = await h.indexed()
            service = h.app.state.knowledge
            rows = service.retriever.search(
                SearchQuery(query="Orchard", collection_ids=[collection]), h.owner, "assistant"
            )["results"]
            selected = h.app.state.model_roles.capture().service("character_model")
            grounded = GroundedService(service, selected, rows, h.owner, "assistant", None)
            reply = await grounded.chat([{"role": "user", "content": "What is the Orchard port?"}])
            assert "8794" in reply and "[K:" in reply
            with pytest.raises(KnowledgeError):
                service.retriever.citations.resolve(rows[0], str(uuid4()), "assistant")
            service.store.grant(h.owner, "assistant", [])
            with pytest.raises(KnowledgeError):
                await grounded.chat([])
            assert len(h.requests) == 1

    asyncio.run(run())


def test_INT_005_condition_mcp_and_separate_human_approvals(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            tool = await h.connect_mcp()
            definition = graph(
                "condition", {"expression": {"op": "equals", "left": True, "right": True}}
            )
            definition.nodes += [
                Node(
                    node_id="echo",
                    type="mcp_tool",
                    config={
                        "tool_id": tool["id"],
                        "server_id": tool["server_id"],
                        "schema_fingerprint": tool["schema_fingerprint"],
                        "arguments": {"text": "unified evidence"},
                    },
                ),
                Node(
                    node_id="approve",
                    type="human_approval",
                    config={"message": "Approve this synthetic run", "value": "approved"},
                ),
                Node(
                    node_id="skip",
                    type="transform",
                    config={"operation": "concat", "value": ["skipped"]},
                ),
                Node(node_id="join", type="merge", config={"mode": "first_available"}),
            ]
            definition.nodes[2].config = {"value": {"$ref": "nodes.join.output"}}
            definition.edges = [
                definition.edges[0],
                Edge(
                    edge_id="yes", source_node_id="step", source_port="true", target_node_id="echo"
                ),
                Edge(edge_id="echo", source_node_id="echo", target_node_id="approve"),
                Edge(
                    edge_id="no", source_node_id="step", source_port="false", target_node_id="skip"
                ),
                Edge(edge_id="a", source_node_id="approve", target_node_id="join"),
                Edge(edge_id="b", source_node_id="skip", target_node_id="join"),
                Edge(edge_id="finish", source_node_id="join", target_node_id="finish"),
            ]
            definition.edges[0].edge_id = "start"
            key = start(h.app, h.owner, definition)
            approvals = []
            for _ in range(4):
                waiting = await settle(h.app, h.owner, key, waiting=True)
                if waiting["state"] != "WAITING_PERMISSION":
                    break
                pending = next(node["pending"] for node in waiting["nodes"] if node.get("pending"))
                with pytest.raises((WorkflowError, LookupError)):
                    h.app.state.workflows.approve(key, str(uuid4()), pending["approval_id"], True)
                with pytest.raises(WorkflowError):
                    h.app.state.workflows.approve(key, h.owner, str(uuid4()), True)
                if pending["kind"] == "tool":
                    from app.services.trusted_actions import action_for, trusted_actions

                    trusted_actions.grant_extension(
                        action_for(pending["call"]),
                        "once",
                        pending["work_session_id"] + ":" + pending["call"]["id"],
                        readonly=True,
                    )
                approvals.append(pending["approval_id"])
                h.app.state.workflows.approve(key, h.owner, pending["approval_id"], True)
                await asyncio.sleep(0.03)
            result = await settle(h.app, h.owner, key)
            assert result["state"] == "COMPLETED" and result["output"]["finish"] == "approved", (
                result
            )
            assert len(approvals) == len(set(approvals)) == 2
            assert result["tool_calls"] == 1 and not h.requests

    asyncio.run(run())


def test_INT_006_computer_runtime_checkpoint_scoped_observation(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            calls = []
            target_id, observation_id = str(uuid4()), str(uuid4())

            async def request(operation, arguments, session_id, timeout=60):
                calls.append(arguments)
                return {
                    "status": "SUCCESS",
                    "verified": True,
                    "executed": True,
                    "window_id": target_id,
                    "observation_id": observation_id,
                    "element_count": 2,
                    "duration_ms": 2,
                    "raw_tree": "SYNTHETIC_UNTRUSTED_TEXT",
                }

            h.app.state.desktop_bridge = SimpleNamespace(request=request)
            node = Node(
                node_id="observe",
                type="computer_use",
                config={"action": "observe", "arguments": {"target_id": target_id}},
            )
            context, _ = h.app.state.workflows.bridge.prepare(
                {"conversation_id": h.owner, "renderer_id": "unified-computer"},
                node,
                {"target_id": target_id},
                None,
            )
            with await h.app.state.trace_recorder.execution_scope(
                context.task_id, h.owner, context.work_session_id
            ):
                output, _ = await h.app.state.workflows.bridge.execute(
                    context, {context.calls[0]["id"]}
                )
            assert len(calls) == 1 and calls[0]["owner"]["conversation_id"] == h.owner
            assert "SYNTHETIC_UNTRUSTED_TEXT" not in json.dumps(output)
            checkpoint = h.app.state.checkpoint_manager.detail(context.task_id, h.owner)[
                "checkpoints"
            ][0]
            assert (
                checkpoint["step_state"] == "COMMITTED"
                and checkpoint["execution_intent"]["kind"] == "computer"
            )
            assert not h.requests

    asyncio.run(run())


def test_INT_007_scheduler_workflow_trace_notification_outbox(tmp_path):
    from app.services.scheduling.notifications import SchedulerNotifications

    async def run():
        async with unified_host(tmp_path) as h:
            definition = scheduled(h.app, h.owner, h.clock)
            h.scheduler.save(definition)
            h.clock.advance(60)
            await h.scheduler.tick()
            occurrence = h.scheduler.store.occurrences(h.owner)[0]
            final = await run_to_completion(
                h.app, h.scheduler, h.owner, occurrence["occurrence_id"]
            )
            assert final["workflow_run_id"] and final["run_id"]
            notifications = SchedulerNotifications(h.scheduler.store).listing(h.owner)
            assert any(row["occurrence_id"] == final["occurrence_id"] for row in notifications)
            await h.trace.flush()
            assert h.app.state.trace_repository.listing(h.owner)["items"]
            assert not h.requests

    asyncio.run(run())


def test_INT_008_voice_final_agent_tool_and_pcm_pipeline(tmp_path):
    from app.schemas.chat import VoiceContext
    from app.services.evaluation.voice_cases import signal
    from app.services.realtime_voice.contracts import Frame, Scope, StartRequest
    from app.services.voice.continuous_pipeline import RealtimeVoicePipeline
    from app.database.session import SessionLocal

    async def run():
        async with unified_host(tmp_path) as h:
            manager = h.app.state.realtime_voice
            options = StartRequest(
                owner_id=uuid4(), conversation_id=h.owner, character_id="assistant"
            )
            identity = await manager.start(options)
            identity = await manager.begin(
                Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
            )
            scope = Scope.model_validate({key: identity[key] for key in Scope.model_fields})
            for n in range(3):
                manager.frame(Frame(**scope.model_dump(), **signal(n), timestamp=0))
            final = await manager.finalize(scope)
            manager.claim(scope, h.owner, "assistant")
            plan, result = await h.agent(
                final["text"], manager.owned(scope, turn=True).turn.model_routes
            )
            assert result["tools"]

            async def source():
                yield result["content"]

            async def complete(text):
                return {"content": text}

            payload = SimpleNamespace(
                voice_context=VoiceContext.model_validate(
                    {key: value for key, value in scope.model_dump().items() if key != "owner_id"}
                ),
                renderer_id=options.owner_id,
                voice_enabled=True,
                expression="natural",
                message=final["text"],
                speed_factor=1,
            )
            events = [
                (event, data)
                async for event, data in RealtimeVoicePipeline(
                    h.app, payload, result["message_id"], SessionLocal
                ).events(source(), [], complete)
            ]
            assert any(event == "audio_pcm" and data["pcm_base64"] for event, data in events)
            with pytest.raises(ValueError):
                manager.claim(scope, h.owner, "assistant")
            # PCM generation is measured here; real playback is a separate GUI gate.

    asyncio.run(run())
