import asyncio
import hashlib
from uuid import uuid4
import pytest
from app.services.workflows.contracts import Node, WorkflowError
from app.services.workflows.port_types import check_port
from app.services.workflows.metrics import combine_usage
from workflow_fixture import host, graph, start, settle


def test_types_do_not_silently_coerce_or_execute_expressions():
    for kind, value in [("number", True), ("string", 1), ("object", []), ("boolean", "false")]:
        with pytest.raises(WorkflowError, match="WORKFLOW_PORT_VALUE_INVALID"):
            check_port(kind, value, "fixture")
    assert (
        combine_usage(
            {"requests": 1, "observed_requests": 1, "total_tokens": 8},
            {"requests": 0, "observed_requests": 0, "total_tokens": None},
        )["total_tokens"]
        == 8
    )
    assert (
        combine_usage({"requests": 1}, {"requests": 1, "observed_requests": 1, "total_tokens": 8})[
            "total_tokens"
        ]
        is None
    )


def test_typed_inputs_and_owned_file_and_trace_references(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            definition = graph("input", {"name": "n", "type": "number", "required": True})
            definition.input_schema = {
                "type": "object",
                "properties": {"n": {"type": "number"}},
                "required": ["n"],
                "additionalProperties": False,
            }
            with pytest.raises(WorkflowError):
                start(app, owner, definition, {"n": True})
            result = await settle(
                app,
                owner,
                start(
                    app,
                    owner,
                    definition.model_copy(update={"workflow_id": str(uuid4())}),
                    {"n": 2},
                ),
            )
            assert result["output"]["finish"] == 2
            file_graph = graph()
            file_graph.nodes[-1].config = {
                "value": {"kind": "selected_workspace_file", "path": "fixture.txt"},
                "format": "file_reference",
            }
            result = await settle(app, owner, start(app, owner, file_graph))
            reference = result["output"]["finish"]
            assert reference["workflow_run_id"] == result["run_id"]
            assert (
                reference["sha256"]
                == hashlib.sha256((tmp_path / "workspace" / "fixture.txt").read_bytes()).hexdigest()
            )
            assert "content" not in reference and ":" not in reference["path"]
            trace_graph = graph()
            trace_graph.nodes[-1].config = {"value": None, "format": "trace_reference"}
            result = await settle(app, owner, start(app, owner, trace_graph))
            assert result["output"]["finish"]["trace_id"] == result["trace_id"]

    asyncio.run(run())


def test_archive_keeps_versions_and_script_change_after_permission_is_blocked(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            script = tmp_path / "workspace" / "test.py"
            script.write_text("print('safe fixture')\n", encoding="utf-8")
            definition = graph("tool", {"name": "code.run", "arguments": {"path": "test.py"}})
            key = start(app, owner, definition)
            waiting = await settle(app, owner, key, waiting=True)
            pending = next(n["pending"] for n in waiting["nodes"] if n.get("pending"))
            assert pending["call"]["workflow_script_sha256"]
            script.write_text("raise RuntimeError('changed fixture')\n", encoding="utf-8")
            app.state.workflows.approve(key, owner, pending["approval_id"], True)
            result = await settle(app, owner, key)
            assert result["state"] == "FAILED" and result["error_code"] == "FILE_STATE_CHANGED"
            repo = app.state.workflows.repository
            current = repo.get(definition.workflow_id)
            archived = repo.archive(definition.workflow_id, current["revision"])
            assert archived["definition"]["status"] == "ARCHIVED"
            assert repo.versions(definition.workflow_id)
            with pytest.raises(WorkflowError, match="WORKFLOW_ARCHIVED"):
                app.state.workflows.start(
                    definition.workflow_id, result["version_id"], owner, {}, "fixture"
                )
            assert not app.state.task_runtime.handles

    asyncio.run(run())
