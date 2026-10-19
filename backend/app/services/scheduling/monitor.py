"""Reconcile existing Workflow/Recovery records. Missing evidence never creates a second run."""

from app.services.scheduling.contracts import ACTIVE

UNCERTAIN = {"SIDE_EFFECT_UNCERTAIN", "MCP_SIDE_EFFECT_UNKNOWN", "ACTION_STATE_UNKNOWN"}


class ScheduledRunMonitor:
    def reconcile(self, *, recovering=False):
        workflows = self.app.state.workflows
        # Historical review records cannot hide older active work behind a page limit.
        rows = self.store.occurrences(statuses=ACTIVE, limit=1000)
        for row in rows:
            key, owner, workflow_id = row["occurrence_id"], row["owner_id"], row["workflow_run_id"]
            if not workflow_id:
                if recovering and row["status"] == "CLAIMED":
                    self.store.transition(
                        key, owner, "NEEDS_REVIEW", self.now(), code="SCHEDULE_DISPATCH_INTERRUPTED"
                    )
                elif recovering and row["body"].get("permission"):
                    self.store.transition(
                        key, owner, "WAITING_PERMISSION", self.now(), updates={"permission": None}
                    )
                continue
            try:
                original = workflows.repository.run(workflow_id, owner)
            except ValueError:
                self.store.transition(
                    key, owner, "NEEDS_REVIEW", self.now(), code="SCHEDULE_RUN_UNAVAILABLE"
                )
                continue
            current = original["state"]
            if (
                current in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "NEEDS_REVIEW"}
                and workflow_id in workflows.tasks
            ):
                continue
            if (
                current in {"PENDING", "READY", "RUNNING", "PAUSED"}
                and workflow_id not in workflows.tasks
            ):
                self.store.transition(
                    key, owner, "NEEDS_REVIEW", self.now(), code="SCHEDULE_DISPATCH_INTERRUPTED"
                )
                continue
            unknown = any(node.get("error_code") in UNCERTAIN for node in original["nodes"])
            graph = workflows.repository.version(original["version_id"])
            model_nodes = {
                node.node_id for node in graph.nodes if node.type in {"llm_agent", "agent_skill"}
            }
            unknown = unknown or any(
                node["node_id"] in model_nodes and node["state"] in {"FAILED", "INTERRUPTED"}
                for node in original["nodes"]
            )
            status = (
                "NEEDS_REVIEW"
                if unknown or current in {"INTERRUPTED", "NEEDS_REVIEW"}
                else (
                    "RUNNING" if current in {"PENDING", "READY", "RUNNING", "PAUSED"} else current
                )
            )
            summary = {
                "task_id": original["task_id"],
                "trace_id": original.get("trace_id"),
                "workflow_run_id": workflow_id,
                "workflow_version": original["version_id"],
                "scheduled_time": row["scheduled_for"],
                "actual_start_time": row["started_at"],
                "duration_ms": original.get("active_ms", 0),
                "status": status,
                "verification_result": "typed_workflow_completed"
                if status == "COMPLETED"
                else "not_verified",
                "semantic_goal_verified": None,
                "recovery_count": max(0, original["attempt"] - 1),
                "checkpoint_task_ids": [
                    node["recovery_task_id"]
                    for node in original["nodes"]
                    if node.get("recovery_task_id")
                ],
            }
            changed = self.store.settle(
                key,
                owner,
                status,
                summary,
                self.now(),
                original.get("error_code"),
                expected=row["status"],
            )
            if changed and row["status"] != status:
                self.trace(
                    {"COMPLETED": "RUN_COMPLETED", "FAILED": "RUN_FAILED"}.get(
                        status, "SCHEDULER_RECOVERED"
                    ),
                    owner,
                    row["schedule_id"],
                    key,
                    workflow_id,
                )
            if status not in ACTIVE:
                self.owned_runs.pop(workflow_id, None)

    def recover(self):
        # WorkflowCoordinator has already classified its unfinished runs. Its
        # existing Checkpoint adapter remains the only route to an explicit resume.
        self.reconcile(recovering=True)
        for row in self.store.occurrences(statuses={"QUEUED", "WAITING_PERMISSION"}):
            if row["body"].get("permission") and not row["workflow_run_id"]:
                self.store.transition(
                    row["occurrence_id"],
                    row["owner_id"],
                    "WAITING_PERMISSION",
                    self.now(),
                    updates={"permission": None},
                )
