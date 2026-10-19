"""Owned approval futures, pause/cancel and explicit recovery control."""

import asyncio
from uuid import uuid4
from app.services.workflows.contracts import WorkflowError
from app.services.workflows.trace_adapter import emit
from app.services.trace.scope import current_trace, span_scope


class WorkflowControls:
    async def interrupt(self, run_id, owner):
        """Persist an owned shutdown interruption without cancelling unrelated workflows."""
        run = self.repository.run(run_id, owner)
        self.interrupting.add(run_id)
        task_ids = {run["task_id"]} | {
            node["recovery_task_id"] for node in run["nodes"] if node.get("recovery_task_id")
        }
        self.app.state.runtime_registry.interrupting.update(task_ids)
        context = getattr(self.app.state.trace_recorder, "contexts", {}).get(run["task_id"])
        if context:
            context.terminal = ("interrupted", None)
        try:
            task = self.tasks.get(run_id)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            else:
                self.repository.change(
                    run_id, owner, {"state": "INTERRUPTED", "error_code": "PROCESS_RESTARTED"}
                )
        finally:
            self.interrupting.discard(run_id)
            self.app.state.runtime_registry.interrupting.difference_update(task_ids)

    async def wait_permission(self, run, node, row, details):
        key = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        pending = {"approval_id": key, "node_execution_id": row["node_execution_id"], **details}
        self.approvals[run["run_id"]] = (key, future)
        self.repository.change(
            run["run_id"],
            run["conversation_id"],
            {"state": "WAITING_PERMISSION"},
            node_id=node.node_id,
            node_changes={"state": "WAITING_PERMISSION", "pending": pending},
            event="WORKFLOW_APPROVAL_REQUESTED",
        )
        emit("WORKFLOW_APPROVAL_REQUESTED", run, row)
        try:
            trace = current_trace.get()
            if trace:
                trace.send("state", status="waiting_permission")
            with span_scope(
                "permission",
                "PERMISSION_REQUESTED",
                "PERMISSION_RESOLVED",
                metadata={
                    "workflow_run_id": run["run_id"],
                    "node_execution_id": row["node_execution_id"],
                },
            ):
                if not await future:
                    raise WorkflowError("PERMISSION_DENIED", node.node_id)
            if trace:
                trace.send("state", status="running")
            self.repository.change(
                run["run_id"],
                run["conversation_id"],
                {"state": "RUNNING"},
                node_id=node.node_id,
                node_changes={"state": "RUNNING", "pending": None},
            )
        finally:
            self.approvals.pop(run["run_id"], None)

    def approve(self, run_id, owner, approval_id, decision):
        run = self.repository.run(run_id, owner)
        current = self.approvals.get(run_id)
        if (
            run["state"] != "WAITING_PERMISSION"
            or not current
            or current[0] != approval_id
            or current[1].done()
        ):
            raise WorkflowError("WORKFLOW_APPROVAL_EXPIRED", status=409)
        current[1].set_result(decision)

    async def control(self, run_id, owner, action):
        run = self.repository.run(run_id, owner)
        task = self.tasks.get(run_id)
        if action == "cancel":
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            elif run["state"] != "COMPLETED":
                self.repository.change(
                    run_id, owner, {"state": "CANCELLED"}, event="WORKFLOW_CANCELLED"
                )
        elif action == "pause" and task and run["state"] in {"READY", "RUNNING"}:
            self.pauses[run_id].clear()
            self.repository.change(run_id, owner, {"state": "PAUSED"}, event="WORKFLOW_PAUSED")
        elif action == "continue" and task and run["state"] == "PAUSED":
            self.pauses[run_id].set()
            self.repository.change(run_id, owner, {"state": "RUNNING"}, event="WORKFLOW_RESUMED")
        else:
            raise WorkflowError("WORKFLOW_CONTROL_INVALID", status=409)

    async def resume(self, run_id, owner, revision):
        if run_id in self.tasks or self.closing:
            raise WorkflowError("WORKFLOW_BUSY", status=409)
        selected = self.scope(owner)
        run = self.repository.run(run_id, owner)
        if selected != run.get("workspace"):
            raise WorkflowError("WORKFLOW_WORKSPACE_CHANGED", status=409)
        self.validator.compile(self.repository.version(run["version_id"]))
        await self.recovery.resume(run_id, owner, revision)
        self.launch(run_id, owner)
        return self.repository.run(run_id, owner)

    async def close(self):
        self.closing = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
