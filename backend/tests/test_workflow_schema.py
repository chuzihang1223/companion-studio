"""Protocol/validator/versioning security, independent of model and desktop services."""

from uuid import uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.session import Base
from app.services.workflows.contracts import Definition, Node, Edge, WorkflowError, bounded
from app.services.workflows.compiler import GraphValidator
from app.services.workflows.variables import resolve, condition, transform
from app.services.workflows.repository import WorkflowRepository


def linear():
    return Definition(
        nodes=[
            Node(node_id="start", type="manual_trigger"),
            Node(node_id="finish", type="output", config={"value": "ok"}),
        ],
        edges=[Edge(edge_id="e1", source_node_id="start", target_node_id="finish")],
    )


@pytest.fixture
def repository(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "workflow.db").as_posix())
    Base.metadata.create_all(engine)
    yield WorkflowRepository(sessionmaker(engine))
    engine.dispose()


def test_linear_compilation_and_empty_draft_save(repository):
    empty = repository.save(Definition())
    assert empty["revision"] == 1
    with pytest.raises(WorkflowError, match="WORKFLOW_EMPTY"):
        GraphValidator().compile(Definition())
    compiled = GraphValidator().compile(linear())
    assert compiled.order == ("start", "finish")


def test_duplicate_and_invalid_edge():
    graph = linear()
    graph.nodes.append(graph.nodes[0])
    with pytest.raises(WorkflowError, match="WORKFLOW_DUPLICATE_ID"):
        GraphValidator().compile(graph)
    graph = linear()
    graph.edges[0].target_node_id = "missing"
    with pytest.raises(WorkflowError, match="WORKFLOW_EDGE_INVALID"):
        GraphValidator().compile(graph)


def test_cycle_and_disconnected():
    graph = linear()
    graph.nodes.insert(
        1, Node(node_id="step", type="transform", config={"operation": "length", "value": []})
    )
    graph.edges += [Edge(edge_id="e2", source_node_id="step", target_node_id="step")]
    with pytest.raises(WorkflowError, match="WORKFLOW_CYCLE"):
        GraphValidator().compile(graph)
    graph.edges = graph.edges[:1]
    with pytest.raises(WorkflowError, match="WORKFLOW_DISCONNECTED"):
        GraphValidator().compile(graph)


@pytest.mark.parametrize(
    "ref",
    [
        "nodes.missing.output",
        "nodes.finish.output",
        "inputs.missing",
        "__import__('os')",
        "nodes.start.__class__",
    ],
)
def test_bad_variables(ref):
    graph = linear()
    graph.nodes[-1].config = {"value": {"$ref": ref}}
    with pytest.raises(WorkflowError):
        GraphValidator().compile(graph)


def test_resolver_conditions_and_transform():
    context = {"inputs": {"name": "测试", "n": 2}, "nodes": {"a": {"output": [1, 2]}}}
    assert resolve("你好，{{inputs.name}}", context) == "你好，测试"
    assert resolve({"$ref": "nodes.a.output"}, context) == [1, 2]
    assert condition(
        {
            "op": "and",
            "items": [
                {"op": "greater_than", "left": {"$ref": "inputs.n"}, "right": 1},
                {"op": "exists", "left": {"$ref": "inputs.name"}},
            ],
        },
        context,
    )
    assert not condition({"op": "exists", "left": {"$ref": "inputs.absent"}}, context)
    assert transform({"operation": "map", "value": [{"x": 1}, {"x": 2}], "path": "x"}) == [1, 2]
    assert transform({"operation": "extract", "value": '{"x": [3]}', "path": "x.0"}) == 3
    with pytest.raises(WorkflowError):
        condition({"op": "python", "left": "print('no')"}, context)
    with pytest.raises(WorkflowError):
        condition({"op": "greater_than", "left": True, "right": 0}, context)


def test_strict_schema_and_secret_fields():
    with pytest.raises(ValidationError):
        Definition.model_validate({"schema_version": 2})
    with pytest.raises(ValidationError):
        Node(node_id="a", type="tool", config={"shell": "anything"})
    with pytest.raises(WorkflowError):
        bounded({"password": "not-a-real-password"})
    graph = linear()
    graph.input_schema = {"$ref": "https://schema.invalid/object"}
    with pytest.raises(WorkflowError, match="WORKFLOW_SCHEMA_INVALID"):
        GraphValidator().compile(graph)


def test_immutable_versions_optimistic_edits_and_runs(repository):
    graph = linear()
    saved = repository.save(graph)
    published = repository.publish(graph, saved["revision"])
    current = repository.get(graph.workflow_id)
    edited = Definition.model_validate(current["definition"])
    edited.nodes[-1].config["value"] = "edited"
    repository.save(edited, current["revision"])
    with pytest.raises(WorkflowError, match="WORKFLOW_EDIT_CONFLICT"):
        repository.save(edited, current["revision"])
    assert repository.version(published["version_id"]).nodes[-1].config["value"] == "ok"
    owner = str(uuid4())
    run = repository.create_run(graph, published["version_id"], owner, {}, "renderer")
    with pytest.raises(WorkflowError, match="WORKFLOW_RUN_NOT_FOUND"):
        repository.run(run, str(uuid4()))
    repository.change(run, owner, {"state": "RUNNING"}, event="WORKFLOW_STARTED")
    repository.recover()
    assert repository.run(run, owner)["state"] == "INTERRUPTED"
    assert len(repository.events(run, owner)) == 2
    assert repository.events(run, owner, repository.events(run, owner)[-1]["cursor"]) == []
    repository.delete(graph.workflow_id, repository.get(graph.workflow_id)["revision"])
    assert repository.run(run, owner)["version_id"] == published["version_id"]
