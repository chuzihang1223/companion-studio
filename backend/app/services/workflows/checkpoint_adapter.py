"""Reuse Skill 03 reconciliation and recovery policy. Scheduling rows are not execution intents."""

import asyncio
from uuid import uuid4
from app.services.workflows.contracts import WorkflowError
from app.services.recovery.reconciliation import StateReconciler
from app.services.recovery.policy import side_effect_class


class WorkflowCheckpointAdapter:
    def __init__(self, app, repository):
        self.app, self.repository = app, repository

    async def preview(self, run_id, conversation_id):
        run = self.repository.run(run_id, conversation_id)
        if run["state"] not in {"FAILED", "INTERRUPTED", "NEEDS_REVIEW", "PAUSED"}:
            raise WorkflowError("WORKFLOW_NOT_RESUMABLE", status=409)
        definition = self.repository.version(run["version_id"])
        by_id = {n.node_id: n for n in definition.nodes}
        items = []
        for record in run["nodes"]:
            node = by_id[record["node_id"]]
            state = record["state"]
            action, code, checkpoints = (
                "KEEP" if state in {"COMPLETED", "SKIPPED"} else "EXECUTE",
                None,
                [],
            )
            task_id = record.get("recovery_task_id")
            if node.type in {"llm_agent", "agent_skill"} and state in {
                "RUNNING",
                "INTERRUPTED",
                "FAILED",
            }:
                action, code = "REVIEW", "WORKFLOW_MODEL_REQUEST_UNCERTAIN"
            if task_id:
                try:
                    context = self.app.state.recovery.context(
                        task_id, conversation_id, record["work_session_id"]
                    )
                    inspected = await StateReconciler(
                        self.app, self.app.state.checkpoint_manager
                    ).inspect(task_id, context)
                    checkpoints = inspected["items"]
                    decisions = {i["action"] for i in checkpoints}
                    if "REVIEW" in decisions:
                        action, code = "REVIEW", "SIDE_EFFECT_UNCERTAIN"
                    elif state == "COMPLETED" and decisions == {"SKIP_VERIFIED"}:
                        action = "KEEP_VERIFIED"
                    elif state == "COMPLETED" or any(
                        side_effect_class(c["name"], c) != "READ_ONLY" for c in context.calls
                    ):
                        # 已提交的外部读也可能过期；不能重放工具来重建下游数据。
                        # 无法用文件摘要重新核验的结果交给人工，新运行需重新确认。
                        action, code = "REVIEW", "SIDE_EFFECT_UNCERTAIN"
                    else:
                        # Existing policy decides whether an interrupted attempt is retryable.
                        from app.services.recovery.classifier import FailureClassifier
                        from app.services.recovery.planner import RecoveryPlanner

                        failure = FailureClassifier().classify(
                            TimeoutError(),
                            tool_name=context.calls[0]["name"],
                            task_id=task_id,
                            step_id=task_id,
                        )
                        decision = RecoveryPlanner(external=self.app.state.external_tools).plan(
                            failure,
                            context.calls[0],
                            retry_count=max(0, record["attempt"] - 1),
                            permission=True,
                        )
                        if decision.approval or decision.strategy in {
                            "stop",
                            "budget_exhausted",
                            "inspect_side_effect",
                        }:
                            action, code = "REVIEW", "SIDE_EFFECT_UNCERTAIN"
                except (ValueError, LookupError, AttributeError, OSError):
                    action, code = "REVIEW", "CHECKPOINT_CONFLICT"
            items.append(
                {
                    "node_id": record["node_id"],
                    "node_execution_id": record["node_execution_id"],
                    "action": action,
                    "code": code,
                    "checkpoints": checkpoints,
                }
            )
        return {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "revision": run["revision"],
            "version_id": run["version_id"],
            "can_resume": run["attempt"] < 3 and not any(i["action"] == "REVIEW" for i in items),
            "items": items,
            "extra_model_requests": 0,
            "external_exactly_once": False,
        }

    async def resume(self, run_id, conversation_id, revision):
        preview = await self.preview(run_id, conversation_id)
        if revision != preview["revision"] or not preview["can_resume"]:
            raise WorkflowError("WORKFLOW_RESUME_BLOCKED", status=409)
        # 核验后再以修订号抢占，第二个恢复请求不能重复执行同一份历史。
        run = self.repository.run(run_id, conversation_id)
        self.repository.change(
            run_id,
            conversation_id,
            {
                "state": "PENDING",
                "task_id": str(uuid4()),
                "attempt": run["attempt"] + 1,
                "error_code": None,
            },
            expected_revision=revision,
            event="WORKFLOW_RESUMED",
        )
        keep = {i["node_id"] for i in preview["items"] if i["action"].startswith("KEEP")}
        for node in run["nodes"]:
            if node["node_id"] not in keep:
                self.repository.change(
                    run_id,
                    conversation_id,
                    {},
                    node_id=node["node_id"],
                    node_changes={
                        "state": "PENDING",
                        "error_code": None,
                        "pending": None,
                        "recovery_task_id": None,
                    },
                )
        await asyncio.sleep(0)
        return preview
