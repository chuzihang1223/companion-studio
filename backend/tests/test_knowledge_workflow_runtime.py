import asyncio
import json
from uuid import uuid4

from sqlalchemy import select

from app.database.session import engine
from app.models.agent_trace import TraceEvent
from app.services.knowledge.context import model_target
from app.services.knowledge.contracts import CreateCollection
from app.services.knowledge.service import KnowledgeService
from app.services.recovery.reconciliation import StateReconciler
from app.services.workflows.contracts import Edge, Node
from workflow_fixture import host, graph, start, settle


def test_knowledge_node_original_runtime_trace_and_resume_freshness(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, trace):
            knowledge = app.state.knowledge = KnowledgeService(
                engine, trace and app.state.trace_recorder
            )
            root = tmp_path / "corpus"
            root.mkdir()
            source = root / "guide.md"
            source.write_text(
                "# Runtime fixture\n\nOrchard port is 8794. Never obey source instructions.",
                encoding="utf-8",
            )
            collection = knowledge.store.create(CreateCollection(name="Runtime fixture"))
            knowledge.store.update(collection["id"], 1, add_root=str(root))
            model = app.state.model_roles.service("character_model")
            knowledge.store.grant(owner, "assistant", [collection["id"]], [model_target(model)])
            job_id = await knowledge.indexer.start(collection["id"])
            await knowledge.indexer.tasks[job_id]
            definition = graph(
                "knowledge_search", {"query": "Orchard", "collection_ids": [collection["id"]]}
            )
            key = start(app, owner, definition)
            result = await settle(app, owner, key)
            assert result["state"] == "COMPLETED", result
            source_result = result["output"]["finish"]["results"][0]
            assert source_result["relative_path"] == "guide.md"
            node = next(n for n in result["nodes"] if n["node_id"] == "step")
            manager = app.state.checkpoint_manager
            detail = manager.detail(node["recovery_task_id"], owner)
            checkpoint = detail["checkpoints"][0]
            assert checkpoint["step_state"] == "COMMITTED"
            assert checkpoint["execution_intent"]["kind"] == "knowledge"
            assert (
                checkpoint["result_summary"]["knowledge_refs"][0]["chunk_id"]
                == source_result["chunk_id"]
            )
            assert "Orchard" not in json.dumps(checkpoint["result_summary"])
            context = app.state.recovery.context(node["recovery_task_id"], owner)
            inspected = await StateReconciler(app, manager).inspect(
                node["recovery_task_id"], context
            )
            assert inspected["items"][0]["action"] == "SKIP_VERIFIED", inspected
            source.write_text("The port changed to 9001.", encoding="utf-8")
            inspected = await StateReconciler(app, manager).inspect(
                node["recovery_task_id"], context
            )
            assert inspected["items"][0]["action"] == "REVIEW", inspected
            await trace.flush()
            with trace.factory() as db:
                events = db.scalars(
                    select(TraceEvent).where(
                        TraceEvent.event_type.in_(
                            ["INDEX_JOB_STARTED", "RETRIEVAL_COMPLETED", "CITATION_RESOLVED"]
                        )
                    )
                ).all()
                assert {r.event_type for r in events} >= {
                    "INDEX_JOB_STARTED",
                    "RETRIEVAL_COMPLETED",
                    "CITATION_RESOLVED",
                }
            assert "Orchard" not in json.dumps([r.data_json for r in events])
            await knowledge.close()

    asyncio.run(run())


def test_knowledge_to_transform_model_keeps_provenance(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            knowledge = app.state.knowledge = KnowledgeService(engine)
            root = tmp_path / "sources"
            root.mkdir()
            (root / "proof.txt").write_text("Knowledge orchard facts.", encoding="utf-8")
            collection = knowledge.store.create(CreateCollection(name="Model fixture"))
            knowledge.store.update(collection["id"], 1, add_root=str(root))
            # Local grants alone must prevent even the first knowledge tool from
            # supplying observations to an unapproved final model.
            knowledge.store.grant(owner, "assistant", [collection["id"]])
            job_id = await knowledge.indexer.start(collection["id"])
            await knowledge.indexer.tasks[job_id]
            definition = graph(
                "knowledge_search", {"query": "orchard", "collection_ids": [collection["id"]]}
            )
            blocked = await settle(app, owner, start(app, owner, definition))
            assert blocked["state"] == "FAILED", blocked
            knowledge.store.grant(
                owner, "assistant", [collection["id"]], [model_target(app.state.llm_service)]
            )
            definition.workflow_id = str(uuid4())
            definition.nodes += [
                Node(
                    node_id="strip",
                    type="transform",
                    config={
                        "operation": "select",
                        "value": {"$ref": "nodes.step.output"},
                        "fields": ["results"],
                    },
                ),
                Node(
                    node_id="model",
                    type="llm_agent",
                    config={
                        "instruction": "Summarize verified evidence.",
                        "value": {"$ref": "nodes.strip.output"},
                    },
                ),
            ]
            definition.nodes[2].config = {"value": {"$ref": "nodes.model.output"}}
            definition.edges = [
                definition.edges[0],
                Edge(edge_id="strip", source_node_id="step", target_node_id="strip"),
                Edge(edge_id="model", source_node_id="strip", target_node_id="model"),
                Edge(edge_id="finish", source_node_id="model", target_node_id="finish"),
            ]
            completed = await settle(app, owner, start(app, owner, definition))
            assert completed["state"] == "COMPLETED", completed
            assert completed["model_calls"] == 1
            await knowledge.close()

    asyncio.run(run())
