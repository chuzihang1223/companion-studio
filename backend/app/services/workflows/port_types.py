"""Declared ports are checked against actual values; no implicit string/boolean coercion."""

from app.services.workflows.contracts import WorkflowError


def check_port(kind, value, node_id):
    checks = {
        "any": True,
        "string": isinstance(value, str),
        "number": type(value) in {int, float},
        "boolean": type(value) is bool,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "file_reference": isinstance(value, dict)
        and value.get("kind") in {"file_reference", "selected_workspace_file"}
        and isinstance(value.get("path"), str),
    }
    if not checks.get(kind, False):
        raise WorkflowError("WORKFLOW_PORT_VALUE_INVALID", node_id)
