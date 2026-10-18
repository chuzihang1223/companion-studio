"""Node → original AgentContext / RuntimeRegistry / AgentTaskRuntime / CheckpointManager."""

from pathlib import Path
import asyncio
import hashlib
import json
import time
from uuid import uuid4

from app.services.agent_service import AgentContext
from app.services.coding.workspace import CodingWorkspaceExecutor
from app.services.trusted_actions import action_for, risk_for, trusted_actions
from app.services.workflows.contracts import WorkflowError, bounded
from app.services.recovery.reconciliation import resource_snapshot, file_hash
from app.services.trace.scope import current_trace


class WorkflowRuntimeBridge:
    def __init__(self, app, repository, catalog):
        self.app, self.repository, self.catalog = app, repository, catalog

    def prepare(self, run, node, arguments, workspace):
        self.catalog.validate_node(node)
        task_id, call_id = str(uuid4()), str(uuid4())
        call = self.catalog.parse_call(node, arguments, call_id)
        mode = node.config.get("coding_mode", "virtual_only")
        if mode not in {"virtual_only", "preview_patch", "write_workspace"}:
            raise WorkflowError("WORKFLOW_CODING_MODE_INVALID", node.node_id)
        if mode == "write_workspace" and not workspace:
            raise WorkflowError("WORKFLOW_WORKSPACE_REQUIRED", node.node_id)
        executor = CodingWorkspaceExecutor(Path(workspace) if workspace else None, mode, task_id)
        # Validate the selected root before exposing a plan or executing a tool.
        executor._path(".")
        call["coding_mode"] = mode
        if call["name"] == "code.run":
            script = executor._path(arguments["path"])
            if not script.is_file() or script.stat().st_size > 100000:
                raise WorkflowError("WORKFLOW_SCRIPT_INVALID", node.node_id)
            call["workflow_script_sha256"] = file_hash(script)
        action = action_for(call, executor.workspace)
        risk = risk_for(action)
        if risk == "critical" or trusted_actions.decision(action) == "deny":
            raise WorkflowError("PERMISSION_DENIED", node.node_id)
        call["risk_level"] = risk
        call["requires_confirmation"] = not trusted_actions.allowed(action)
        work = self.app.state.work_state
        session = work.create(
            "Workflow node · " + node.title, run["conversation_id"], [call], workspace
        )
        try:
            executor.bind(session)
            character_id = "assistant"
            knowledge = getattr(self.app.state, "knowledge", None)
            if call["name"].startswith("knowledge.") and knowledge:
                with knowledge.store.sessions() as db:
                    character_id = knowledge.store.conversation(
                        db, run["conversation_id"]
                    ).character_id
            context = AgentContext(
                character_id,
                run["conversation_id"],
                "执行并验证单个已配置工作流工具",
                [],
                {},
                [call],
                time.monotonic(),
                session,
                executor=executor,
                coding_mode=mode,
                renderer_id=run["renderer_id"],
                task_id=task_id,
                knowledge_service=knowledge,
            )
            context.retry_max_attempts = (
                node.retry_policy.max_attempts if node.retry_policy.strategy == "safe_read" else 1
            )
            stored = {
                "plan_id": task_id,
                "character_id": context.character_id,
                "conversation_id": context.conversation_id,
                "user_text": context.user_text,
                "prompt": [],
                "assistant_plan": {},
                "calls": [call],
                "work_session_id": session,
                "workspace": str(executor.workspace),
                "coding_mode": mode,
                "sandbox_task_id": task_id,
                "renderer_id": context.renderer_id,
                "user_message_id": None,
                "selected_skills": [],
            }
            work.save_plan(session, stored)
            manager = self.app.state.checkpoint_manager
            if not manager:
                raise WorkflowError("CHECKPOINT_STORAGE_UNAVAILABLE", status=503)
            trace = current_trace.get()
            root = manager.begin(context, work, trace.trace_id if trace else None)
            manager.prepare(root, call, 0, resource_snapshot(context, call))
            if call["requires_confirmation"]:
                manager.state(root, "WAITING_USER")
            self.app.state.task_runtime.prepare(context)
            return context, {
                "kind": "tool",
                "plan_id": task_id,
                "task_id": task_id,
                "work_session_id": session,
                "call": call,
                "action": action,
                "risk": risk,
                "parameter_digest": hashlib.sha256(
                    json.dumps(arguments, sort_keys=True).encode()
                ).hexdigest(),
            }
        except BaseException:
            self.app.state.task_runtime.handles.pop(session, None)
            work.emit(
                session, "task_failed", {"summary": "节点准备失败，未执行工具"}, status="failed"
            )
            raise

    async def execute(self, context, approved):
        runtime = self.app.state.task_runtime
        work = self.app.state.work_state
        manager = self.app.state.checkpoint_manager
        if not manager:
            raise WorkflowError("CHECKPOINT_STORAGE_UNAVAILABLE", status=503)
        session = context.work_session_id
        try:
            if context.calls[0].get("workflow_script_sha256"):
                if (
                    file_hash(context.executor._path(context.calls[0]["arguments"]["path"]))
                    != context.calls[0]["workflow_script_sha256"]
                ):
                    raise WorkflowError("FILE_STATE_CHANGED")
            if manager.detail(context.task_id, context.conversation_id)["state"] == "WAITING_USER":
                manager.state(context.task_id, "RECOVERING")
            async with self.app.state.runtime_registry.run(
                context.task_id, context.conversation_id, session, context.renderer_id
            ):
                work.decide(session, approved)
                _, details, verified = await runtime.run(context, approved)
                if len(details) != 1 or details[0]["status"] != "done":
                    result = details[0]["result"] if details else {}
                    code = result.get("code") or result.get("error_code") or "WORKFLOW_TOOL_FAILED"
                    raise WorkflowError(code)
                # COMPLETED means the typed operation completed, not a semantic AI goal proof.
                # MCP protocol success stays explicitly separate from goal_verified.
                result = bounded(details[0]["result"])
                manager.settle(context.task_id, "COMPLETED")
                work.emit(
                    session,
                    "work_completed",
                    {"summary": "工作流节点执行结束", "goal_verified": verified},
                    status="done",
                )
                return result, verified
        except BaseException as error:
            handle = runtime.handles.get(session)
            if handle and isinstance(error, asyncio.CancelledError):
                handle.control("cancel")
            manager.settle(
                context.task_id,
                "INTERRUPTED"
                if self.app.state.workflows.closing
                else "CANCELLED"
                if isinstance(error, asyncio.CancelledError)
                else "NEEDS_REVIEW",
            )
            raise
        finally:
            trusted_actions.finish_task(session)
            runtime.handles.pop(session, None)
