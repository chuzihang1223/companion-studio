import asyncio
import json
import time
from uuid import uuid4
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from app.database.session import Base
from app.database.migrations import migrate
from app.models import (
    Conversation,
    Message,
    WorkSession,
    ExtensionRecord,
    EvaluationRun,
    RecoveryTask,
)
from app.services.workflows.contracts import Definition, Node, Edge
from app.services.workflows.compiler import GraphValidator
from app.services.workflows.repository import WorkflowRepository
from workflow_fixture import host, graph, start, settle


def test_idempotent_additive_migration_preserves_all_existing_records(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "old.db").as_posix())
    new_tables = {
        "workflow_definitions",
        "workflow_versions",
        "workflow_runs",
        "workflow_node_runs",
        "workflow_run_events",
    }
    Base.metadata.create_all(
        engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in new_tables]
    )
    factory = sessionmaker(engine)
    owner, session, task = str(uuid4()), str(uuid4()), str(uuid4())
    with factory() as db:
        db.add(
            Conversation(
                id=owner,
                character_id="assistant",
                conversation_type="work",
                preferred_mode="work",
                work_session_id=session,
            )
        )
        db.add(
            Message(
                character_id="assistant",
                conversation_id=owner,
                role="user",
                content="Synthetic migration marker",
            )
        )
        db.add(WorkSession(id=session, conversation_id=owner, goal="Synthetic old task"))
        db.add(ExtensionRecord(id=str(uuid4()), kind="skill", body='{"synthetic":true}'))
        db.add(
            EvaluationRun(
                id=str(uuid4()), mode="mock", total=1, snapshot_json="{}", request_json="{}"
            )
        )
        db.add(
            RecoveryTask(
                id=task,
                execution_id=task,
                conversation_id=owner,
                work_session_id=session,
                plan_hash="a" * 64,
                workspace_hash="b" * 64,
            )
        )
        db.commit()
    old_tables = inspect(engine).get_table_names()

    def snapshot():
        with engine.connect() as connection:
            return {
                name: [
                    tuple(row)
                    for row in connection.execute(text('SELECT * FROM "' + name + '" ORDER BY 1'))
                ]
                for name in old_tables
            }

    before = snapshot()
    migrate(engine)
    migrate(engine)
    assert new_tables <= set(inspect(engine).get_table_names())
    assert snapshot() == before
    repository = WorkflowRepository(factory)
    assert repository.save(graph())["revision"] == 1
    engine.dispose()


def test_twenty_node_serial_queue_and_hundred_node_two_hundred_edge_validation(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            definition = graph()
            definition.nodes = (
                [definition.nodes[0]]
                + [
                    Node(
                        node_id=f"n{i:03}",
                        type="transform",
                        config={"operation": "length", "value": []},
                    )
                    for i in range(18)
                ]
                + [definition.nodes[-1]]
            )
            definition.nodes[-1].config = {"value": "completed"}
            definition.edges = [
                Edge(
                    edge_id="e" + str(i),
                    source_node_id=definition.nodes[i].node_id,
                    target_node_id=definition.nodes[i + 1].node_id,
                )
                for i in range(19)
            ]
            started = time.perf_counter()
            keys = [
                start(app, owner, definition.model_copy(update={"workflow_id": str(uuid4())}))
                for _ in range(4)
            ]
            results = await asyncio.gather(*(settle(app, owner, key) for key in keys))
            assert all(r["state"] == "COMPLETED" and len(r["nodes"]) == 20 for r in results)
            assert not app.state.runtime_registry.active
            metrics = {
                "queued_workflows": 4,
                "serial_nodes_each": 20,
                "duration_ms": (time.perf_counter() - started) * 1000,
            }
            large = graph()
            large.nodes = [
                Node(
                    node_id=f"p{i:03}",
                    type="manual_trigger" if i == 0 else "output" if i == 99 else "transform",
                    config={}
                    if i == 0
                    else {"value": "done"}
                    if i == 99
                    else {"operation": "length", "value": []},
                )
                for i in range(100)
            ]
            pairs = (
                [(i, i + 1) for i in range(99)]
                + [(i, i + 2) for i in range(98)]
                + [(0, 3), (0, 4), (0, 5)]
            )
            large.edges = [
                Edge(
                    edge_id="p" + str(i),
                    source_node_id=large.nodes[a].node_id,
                    target_node_id=large.nodes[b].node_id,
                )
                for i, (a, b) in enumerate(pairs)
            ]
            compiled = GraphValidator(app.state.workflows.catalog).compile(large)
            assert len(compiled.order) == 100 and len(large.edges) == 200
            metrics["validation_100_nodes_200_edges_ms"] = compiled.validation_ms
            (tmp_path / "workflow-performance.json").write_text(
                json.dumps(metrics, indent=2), encoding="utf-8"
            )

    asyncio.run(run())


def test_optimistic_writer_collision_does_not_damage_saved_graph(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "concurrent.db").as_posix(), connect_args={"timeout": 0.05}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    repository = WorkflowRepository(factory)
    saved = repository.save(graph())
    import pytest
    from sqlalchemy.exc import OperationalError

    with engine.connect() as connection:
        connection.execute(text("BEGIN IMMEDIATE"))
        with pytest.raises(OperationalError):
            repository.save(
                Definition.model_validate(saved["definition"]),
                saved["revision"],
            )
        connection.rollback()
    assert repository.get(saved["definition"]["workflow_id"])["revision"] == saved["revision"]
    with factory() as db:
        assert db.scalar(select(Conversation)) is None
    engine.dispose()
