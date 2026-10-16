"""Validate first, then compile an immutable deterministic topological plan."""

from dataclasses import dataclass
import time

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.services.workflows.contracts import Definition, WorkflowError
from app.services.workflows.variables import PATH, references, validate_condition


def schema_check(schema):
    # No references, regex engines or remote schema fetching in user documents.
    def walk(value, depth=0):
        if depth > 12:
            raise WorkflowError("WORKFLOW_SCHEMA_INVALID")
        if isinstance(value, dict):
            if set(value) & {
                "$ref",
                "$dynamicRef",
                "pattern",
                "patternProperties",
                "format",
                "$id",
            }:
                raise WorkflowError("WORKFLOW_SCHEMA_INVALID")
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    walk(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise WorkflowError("WORKFLOW_SCHEMA_INVALID") from None


@dataclass(frozen=True)
class Compiled:
    definition: Definition
    order: tuple[str, ...]
    parents: dict
    ancestors: dict
    validation_ms: float


class GraphValidator:
    def __init__(self, catalog=None):
        self.catalog = catalog

    def compile(self, definition: Definition) -> Compiled:
        started = time.perf_counter()
        nodes = {n.node_id: n for n in definition.nodes}
        if len(nodes) != len(definition.nodes) or len({e.edge_id for e in definition.edges}) != len(
            definition.edges
        ):
            raise WorkflowError("WORKFLOW_DUPLICATE_ID")
        if not nodes:
            raise WorkflowError("WORKFLOW_EMPTY")
        starts = [n for n in nodes.values() if n.type == "manual_trigger"]
        if len(starts) != 1 or not any(n.type == "output" for n in nodes.values()):
            raise WorkflowError("WORKFLOW_TERMINAL_REQUIRED")
        schema_check(definition.input_schema)
        schema_check(definition.output_schema)
        parents = {key: [] for key in nodes}
        children = {key: [] for key in nodes}
        signatures = set()
        for edge in definition.edges:
            a, b = nodes.get(edge.source_node_id), nodes.get(edge.target_node_id)
            if (
                not a
                or not b
                or edge.source_port not in a.output_ports
                or edge.target_port not in b.input_ports
            ):
                raise WorkflowError("WORKFLOW_EDGE_INVALID", edge.target_node_id)
            if b.type == "manual_trigger" or a.type == "output":
                raise WorkflowError("WORKFLOW_EDGE_INVALID", b.node_id)
            signature = (a.node_id, edge.source_port, b.node_id, edge.target_port)
            if signature in signatures:
                raise WorkflowError("WORKFLOW_EDGE_DUPLICATE", b.node_id)
            signatures.add(signature)
            at, bt = a.output_ports[edge.source_port], b.input_ports[edge.target_port]
            if at != "any" and bt != "any" and at != bt:
                raise WorkflowError("WORKFLOW_PORT_TYPE", b.node_id)
            if edge.condition_label and edge.condition_label != edge.source_port:
                raise WorkflowError("WORKFLOW_EDGE_INVALID", b.node_id)
            parents[b.node_id].append(edge)
            children[a.node_id].append(edge)
        indegree = {key: len(value) for key, value in parents.items()}
        queue = sorted(k for k, degree in indegree.items() if degree == 0)
        order, ancestors = [], {key: set() for key in nodes}
        while queue:
            key = queue.pop(0)
            order.append(key)
            for edge in children[key]:
                dest = edge.target_node_id
                ancestors[dest].update(ancestors[key] | {key})
                indegree[dest] -= 1
                if indegree[dest] == 0:
                    queue.append(dest)
                    queue.sort()
        if len(order) != len(nodes):
            raise WorkflowError("WORKFLOW_CYCLE")
        for node in nodes.values():
            key, config = node.node_id, node.config
            if node.type != "manual_trigger" and starts[0].node_id not in ancestors[key]:
                raise WorkflowError("WORKFLOW_DISCONNECTED", key)
            if node.type != "output" and not children[key]:
                raise WorkflowError("WORKFLOW_OUTPUT_UNREACHABLE", key)
            required = {
                "input": {"name", "type"},
                "llm_agent": {"instruction"},
                "tool": {"name", "arguments"},
                "mcp_tool": {"tool_id", "server_id", "schema_fingerprint", "arguments"},
                "agent_skill": {"skill_id", "content_hash", "goal"},
                "condition": {"expression"},
                "transform": {"operation", "value"},
                "human_approval": {"message"},
                "output": {"value"},
            }.get(node.type, set())
            if required - config.keys():
                raise WorkflowError("WORKFLOW_CONFIG_REQUIRED", key)
            for ref in references({k: v for k, v in config.items() if k != "output_schema"}):
                if not isinstance(ref, str) or not PATH.fullmatch(ref):
                    raise WorkflowError("WORKFLOW_VARIABLE_INVALID", key)
                parts = ref.split(".")
                if parts[0] == "nodes" and (
                    len(parts) < 3 or parts[1] not in ancestors[key] or parts[2] != "output"
                ):
                    raise WorkflowError("WORKFLOW_VARIABLE_SCOPE", key)
                if (
                    parts[0] == "inputs"
                    and len(parts) > 1
                    and parts[1] not in definition.input_schema.get("properties", {})
                ):
                    raise WorkflowError("WORKFLOW_VARIABLE_UNDEFINED", key)
                if parts[0] == "variables":
                    raise WorkflowError("WORKFLOW_VARIABLE_UNDEFINED", key)
            if node.type == "condition":
                validate_condition(config["expression"])
                if not any(e.source_port == "true" for e in children[key]) or not any(
                    e.source_port == "false" for e in children[key]
                ):
                    raise WorkflowError("WORKFLOW_CONDITION_BRANCH_REQUIRED", key)
            if node.type == "input":
                if config["type"] not in {
                    "string",
                    "number",
                    "boolean",
                    "object",
                    "array",
                    "file_reference",
                } or config["name"] not in definition.input_schema.get("properties", {}):
                    raise WorkflowError("WORKFLOW_INPUT_INVALID", key)
            if node.type == "merge" and config.get("mode", "all_active") not in {
                "all_active",
                "first_available",
            }:
                raise WorkflowError("WORKFLOW_MERGE_INVALID", key)
            if "output_schema" in config:
                schema_check(config["output_schema"])
            if self.catalog:
                self.catalog.validate_node(node)
        if len(nodes) > definition.budget.max_steps:
            raise WorkflowError("WORKFLOW_STEP_BUDGET")
        return Compiled(
            definition.model_copy(deep=True),
            tuple(order),
            parents,
            ancestors,
            (time.perf_counter() - started) * 1000,
        )


WorkflowCompiler = GraphValidator
