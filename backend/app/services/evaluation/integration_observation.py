"""Internal QA rescoring of an already executed owned workflow, not a benchmark rerun.

No public start endpoint or caller-supplied PASS is accepted. Read-only evidence
validation uses original Workflow, citations, Checkpoint, Trace and Evaluation CAS.
"""

from dataclasses import dataclass
import hashlib
import re
from pathlib import Path

from app.schemas.evaluation import Mode
from app.database.session import SessionLocal
from app.models import Conversation
from app.services.evaluation.engine import build_identity
from app.services.knowledge.runtime import references, validate_references
from app.services.workspace_paths import scoped_path


@dataclass(frozen=True)
class ObservationOptions:
    mode: Mode = Mode.MOCK
    repetitions: int = 1

    def model_dump(self, **_options):
        return {
            "origin": "integration_observation",
            "test_id": "INT-016",
            "mode": self.mode.value,
            "repetitions": 1,
        }


@dataclass(frozen=True)
class ObservationCase:
    case_id: str = "INT-016"


def evaluate_workflow_artifact(app, workflow_run_id, owner, repository):
    if repository.factory is SessionLocal:
        raise ValueError("Use an explicit independent QA evaluation index")
    run = app.state.workflows.repository.run(workflow_run_id, owner)
    with SessionLocal() as db:
        conversation = db.get(Conversation, owner)
        if not conversation or conversation.is_deleted:
            raise LookupError("Workflow owner unavailable")
        character = conversation.character_id
    outputs = [
        value
        for value in run["output"].values()
        if isinstance(value, dict) and value.get("kind") == "file_reference"
    ]
    if len(outputs) != 1 or not run.get("workspace"):
        raise ValueError("One bounded workflow artifact reference required")
    artifact = outputs[0]
    path = scoped_path(Path(run["workspace"]), artifact["path"])
    if not path.is_file() or path.stat().st_size > 1000000:
        raise ValueError("Workflow artifact unavailable")
    raw = path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8")
    cited = set(re.findall(r"\[K:([^\]]+)\]", text))
    refs = [item for node in run["nodes"] for item in references(node["output"])]
    verified = validate_references(app.state.knowledge, refs, owner, character)
    citations_ok = bool(cited) and cited <= {item["chunk_id"] for item in verified}
    checkpoints = [
        app.state.checkpoint_manager.detail(node["recovery_task_id"], owner)
        for node in run["nodes"]
        if node.get("recovery_task_id")
    ]
    traces = app.state.trace_repository.listing(owner, task_id=run["task_id"])["items"]
    checks = {
        "workflow_completed": run["state"] == "COMPLETED",
        "artifact_hash_matches": actual_hash == artifact["sha256"],
        "artifact_size_matches": len(raw) == artifact["size_bytes"] > 0,
        "citations_resolve_under_current_grants": citations_ok,
        "checkpoints_committed": bool(checkpoints)
        and all(
            all(step["step_state"] == "COMMITTED" for step in row["checkpoints"])
            for row in checkpoints
        ),
        "owned_trace_completed": bool(traces)
        and all(row["status"] == "completed" and row["conversation_id"] == owner for row in traces),
    }
    passed = all(checks.values())
    identity = build_identity()
    created = repository.create(
        ObservationOptions(),
        [ObservationCase()],
        {
            "origin": "integration_observation",
            "test_id": "INT-016",
            "app_version": identity[0],
            "git_commit": identity[1],
            "workflow_run_id": workflow_run_id,
            "boundary": "existing_execution_evidence_rescored_no_worker_rerun",
            "model_boundary": "synthetic_loopback_provider",
        },
    )
    key = created["run_id"]
    attempt = repository.results(key)["items"][0]
    repository.status(key, "RUNNING")
    repository.started(attempt["attempt_id"])
    result = {
        "case_id": "INT-016",
        "completion_status": "PASS" if passed else "FAIL",
        "passed": passed,
        "score": int(passed),
        "grader_type": "integration_observation",
        "duration_ms": None,
        "error_code": None if passed else "ARTIFACT_EVIDENCE_FAILED",
        "evidence": {
            "checks": checks,
            "artifact_sha256": actual_hash,
            "citation_count": len(cited),
            "checkpoint_count": len(checkpoints),
        },
        "model_requests": run["model_calls"],
        "tool_calls": run["tool_calls"],
        "retry_count": run["attempt"] - 1,
        "model_usage": {"status": "synthetic_observation_not_a_bill"},
        "dimensions": {"task_success": passed},
        "explanation": "Existing owned execution evidence was rescored; the benchmark worker did not execute this run.",
    }
    links = traces[0] if traces else {"task_id": run["task_id"], "conversation_id": owner}
    repository.finish(key, attempt["attempt_id"], result, links)
    repository.refresh_metrics(key)
    repository.status(key, "COMPLETED")
    return repository.detail(key)
