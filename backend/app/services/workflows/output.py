"""Bounded, owned references. No raw large file/image is copied into workflow journals."""

import hashlib
from pathlib import Path
from uuid import UUID
from app.services.workspace_paths import scoped_path
from app.services.workflows.contracts import WorkflowError


def workflow_output(app, config, value, context):
    kind = config.get("format", "json")
    if kind == "text" and not isinstance(value, str):
        raise WorkflowError("WORKFLOW_VARIABLE_TYPE")
    if kind == "file_reference":
        if (
            not context.get("workspace")
            or not isinstance(value, dict)
            or set(value) - {"path", "kind"}
            or not isinstance(value.get("path"), str)
        ):
            raise WorkflowError("WORKFLOW_FILE_REFERENCE_INVALID")
        target = scoped_path(Path(context["workspace"]), value["path"])
        if not target.is_file() or target.stat().st_size > 1000000:
            raise WorkflowError("WORKFLOW_FILE_REFERENCE_INVALID")
        return {
            "kind": kind,
            "workflow_run_id": context["workflow_run_id"],
            "path": value["path"],
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "size_bytes": target.stat().st_size,
            "source": "selected_workspace_file",
            "trust": "untrusted_file_data",
        }
    if kind == "trace_reference":
        return {
            "kind": kind,
            "trace_id": context["trace_id"],
            "workflow_run_id": context["workflow_run_id"],
        }
    if kind == "evaluation_reference":
        if not isinstance(value, dict) or set(value) != {"evaluation_run_id"}:
            raise WorkflowError("WORKFLOW_EVALUATION_REFERENCE_INVALID")
        try:
            key = str(UUID(value["evaluation_run_id"]))
            app.state.evaluation.repository.detail(key)
        except (ValueError, LookupError, AttributeError):
            raise WorkflowError("WORKFLOW_EVALUATION_REFERENCE_INVALID") from None
        return {
            "kind": kind,
            "evaluation_run_id": key,
            "workflow_run_id": context["workflow_run_id"],
        }
    if kind == "task_summary":
        return {
            "kind": kind,
            "workflow_run_id": context["workflow_run_id"],
            "completed_nodes": len(context["nodes"]),
            "summary": value,
            "semantic_goal_verified": None,
        }
    return value
