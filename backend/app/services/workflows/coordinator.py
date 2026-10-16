"""Own a serial workflow coroutine; inner tools retain the original Agent concurrency gate.

外层使用独立 workflow 配额，内层仍取得 agent 会话锁，避免先持有同一个锁再等待自己。
审批只针对本次已经解析的参数；通用 Human Approval 不会授予之后节点的工具权限。
"""

import asyncio
from contextlib import nullcontext
import time

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.database.session import SessionLocal
from app.models import Conversation
from app.services.trace.scope import current_trace
from app.services.workflows.catalog import WorkflowCatalog
from app.services.workflows.checkpoint_adapter import WorkflowCheckpointAdapter
from app.services.workflows.compiler import GraphValidator
from app.services.workflows.contracts import WorkflowError, bounded
from app.services.workflows.node_executor import NodeExecutor
from app.services.workflows.repository import WorkflowRepository
from app.services.workflows.runtime_bridge import WorkflowRuntimeBridge
from app.services.workflows.scheduler import next_node
from app.services.workflows.trace_adapter import emit, execution_trace
from app.services.workflows.node_runner import WorkflowNodeRunner
from app.services.workflows.controls import WorkflowControls
from app.services.workflows.metrics import combine_usage
from app.services.llm.usage import UsageBudget, usage_scope, current_usage_budget


class WorkflowCoordinator(WorkflowNodeRunner, WorkflowControls):
    def __init__(self, app, repository=None):
        self.app, self.repository = app, repository or WorkflowRepository()
        self.catalog = WorkflowCatalog(app)
        self.validator = GraphValidator(self.catalog)
        self.executor = NodeExecutor(app, self.catalog)
        self.bridge = WorkflowRuntimeBridge(app, self.repository, self.catalog)
        self.recovery = WorkflowCheckpointAdapter(app, self.repository)
        self.tasks, self.approvals, self.pauses = {}, {}, {}
        self.closing = False
        self.repository.recover()

    def scope(self, conversation_id):
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id)
            if (
                not conversation
                or conversation.is_deleted
                or conversation.conversation_type not in {"work", "project"}
            ):
                raise WorkflowError("WORKFLOW_CONVERSATION_REQUIRED", status=404)
        try:
            selected = self.app.state.work_state.for_conversation(conversation_id)
        except LookupError:
            return None  # New Work conversations need no pre-existing Agent operation.
        return selected.get("workspace_root")

    def start(self, workflow_id, version_id, conversation_id, inputs, renderer_id):
        workspace = self.scope(conversation_id)
        if self.repository.get(workflow_id)["definition"]["status"] == "ARCHIVED":
            raise WorkflowError("WORKFLOW_ARCHIVED", status=409)
        definition = self.repository.version(version_id)
        if definition.workflow_id != workflow_id:
            raise WorkflowError("WORKFLOW_VERSION_NOT_FOUND", status=404)
        self.validator.compile(definition)
        try:
            Draft202012Validator(definition.input_schema).validate(bounded(inputs, limit=64000))
        except ValidationError:
            raise WorkflowError("WORKFLOW_INPUT_INVALID") from None
        if sum(not task.done() for task in self.tasks.values()) >= 8:
            raise WorkflowError("WORKFLOW_QUEUE_FULL", status=409)
        run_id = self.repository.create_run(
            definition, version_id, conversation_id, inputs, renderer_id
        )
        session = self.app.state.work_state.create(
            "Workflow · " + definition.name, conversation_id, [], workspace
        )
        self.repository.change(
            run_id, conversation_id, {"work_session_id": session, "workspace": workspace}
        )
        self.launch(run_id, conversation_id)
        return self.repository.run(run_id, conversation_id)

    def launch(self, run_id, conversation_id):
        if run_id in self.tasks or self.closing:
            raise WorkflowError("WORKFLOW_BUSY", status=409)
        pause = asyncio.Event()
        pause.set()
        self.pauses[run_id] = pause
        task = asyncio.create_task(self.run(run_id, conversation_id))
        self.tasks[run_id] = task
        task.add_done_callback(lambda done: self.finished(run_id, done))

    def finished(self, run_id, task):
        self.tasks.pop(run_id, None)
        self.pauses.pop(run_id, None)
        self.approvals.pop(run_id, None)
        if not task.cancelled():
            task.exception()  # Exception is recorded; never leave an unobserved background failure.

    async def run(self, run_id, conversation_id):
        run = self.repository.run(run_id, conversation_id)
        definition = self.repository.version(run["version_id"])
        recorder = self.app.state.trace_recorder
        if recorder:
            recorder.create(run["task_id"], conversation_id)
        tracing = (
            await recorder.execution_scope(run["task_id"], conversation_id, run["work_session_id"])
            if recorder
            else nullcontext()
        )
        started = time.perf_counter()
        previous_usage = run.get("model_usage", {})
        previous_tokens = previous_usage.get("total_tokens")
        remaining_tokens = definition.budget.max_total_tokens - (previous_tokens or 0)
        if run["model_calls"] and previous_tokens is None:
            remaining_tokens = 0  # Unknown historic usage must not silently refill the budget.
        usage = current_usage_budget() or UsageBudget(
            definition.budget.max_model_calls - run["model_calls"], remaining_tokens
        )
        try:
            with execution_trace(tracing, self, run), usage_scope(usage):
                trace = current_trace.get()
                emit("WORKFLOW_CREATED" if run["attempt"] == 1 else "WORKFLOW_RESUMED", run)
                self.repository.change(
                    run_id,
                    conversation_id,
                    {"state": "READY", "trace_id": trace.trace_id if trace else None},
                    event="WORKFLOW_VALIDATED",
                )
                async with self.app.state.runtime_registry.run(
                    run["task_id"],
                    conversation_id,
                    run["work_session_id"],
                    run["renderer_id"],
                    kind="workflow",
                ):
                    compiled = self.validator.compile(definition)
                    self.repository.change(
                        run_id,
                        conversation_id,
                        {
                            "validation_ms": compiled.validation_ms,
                            "compile_ms": compiled.compile_ms,
                            "queue_wait_ms": (time.perf_counter() - started) * 1000,
                        },
                    )
                    emit("WORKFLOW_VALIDATED", run)
                    async with asyncio.timeout(
                        max(
                            0.05,
                            definition.budget.max_duration_seconds - run.get("active_ms", 0) / 1000,
                        )
                    ):
                        self.repository.change(
                            run_id, conversation_id, {"state": "RUNNING"}, event="WORKFLOW_STARTED"
                        )
                        emit("WORKFLOW_STARTED", run)
                        self.app.state.work_state.emit(
                            run["work_session_id"],
                            "workflow_run_started",
                            {"run_id": run_id},
                            status="executing",
                        )
                        await self.schedule(compiled, run_id, conversation_id)
                self.repository.change(
                    run_id,
                    conversation_id,
                    {"state": "COMPLETED", "error_code": None},
                    event="WORKFLOW_COMPLETED",
                )
                emit("WORKFLOW_COMPLETED", run)
                self.app.state.work_state.emit(
                    run["work_session_id"],
                    "work_completed",
                    {"summary": "工作流已执行完成", "run_id": run_id},
                    status="done",
                )
        except BaseException as error:
            state = (
                "INTERRUPTED"
                if self.closing
                else "CANCELLED"
                if isinstance(error, asyncio.CancelledError)
                else "NEEDS_REVIEW"
                if getattr(error, "code", "")
                in {"SIDE_EFFECT_UNCERTAIN", "MCP_SIDE_EFFECT_UNKNOWN"}
                else "FAILED"
            )
            code = (
                "PROCESS_RESTARTED"
                if self.closing
                else "CANCELLED"
                if state == "CANCELLED"
                else "TIMEOUT"
                if isinstance(error, TimeoutError)
                else getattr(error, "code", "WORKFLOW_EXECUTION_FAILED")
            )
            self.repository.change(
                run_id,
                conversation_id,
                {"state": state, "error_code": code},
                event="WORKFLOW_CANCELLED" if state == "CANCELLED" else "WORKFLOW_FAILED",
            )
            self.app.state.work_state.emit(
                run["work_session_id"],
                "task_interrupted"
                if self.closing
                else "task_cancelled"
                if state == "CANCELLED"
                else "task_failed",
                {"summary": "工作流未完成", "run_id": run_id, "code": code},
                status="interrupted"
                if self.closing
                else "cancelled"
                if state == "CANCELLED"
                else "failed",
            )
            # execution_scope saw the real exception; Trace final state cannot claim success.
        finally:
            self.repository.change(
                run_id,
                conversation_id,
                {
                    "active_ms": round(
                        run.get("active_ms", 0) + (time.perf_counter() - started) * 1000, 2
                    ),
                    "model_usage": combine_usage(previous_usage, usage.public()),
                    "estimated_cost": None,
                },
            )

    async def schedule(self, compiled, run_id, owner):
        while True:
            await self.pauses[run_id].wait()
            run = self.repository.run(run_id, owner)
            records = {row["node_id"]: row for row in run["nodes"]}
            selected = next_node(compiled, records)
            if selected is None:
                outputs = [
                    n
                    for n in compiled.definition.nodes
                    if n.type == "output" and records[n.node_id]["state"] == "COMPLETED"
                ]
                if not outputs or any(
                    row["state"] not in {"COMPLETED", "SKIPPED", "FAILED"}
                    for row in records.values()
                ):
                    raise WorkflowError("WORKFLOW_NO_OUTPUT")
                output = {n.node_id: records[n.node_id]["output"] for n in outputs}
                try:
                    Draft202012Validator(compiled.definition.output_schema).validate(output)
                except ValidationError:
                    raise WorkflowError("WORKFLOW_OUTPUT_INVALID") from None
                self.repository.change(run_id, owner, {"output": output})
                return
            node, active_inputs, skipped = selected
            row = records[node.node_id]
            if skipped:
                self.repository.change(
                    run_id,
                    owner,
                    {},
                    node_id=node.node_id,
                    node_changes={"state": "SKIPPED", "trace_id": run.get("trace_id")},
                    event="WORKFLOW_NODE_SKIPPED",
                )
                emit("WORKFLOW_NODE_SKIPPED", run, row)
                continue
            context = {
                "inputs": run["inputs"],
                "nodes": {
                    key: {"output": item["output"]}
                    for key, item in records.items()
                    if item["state"] in {"COMPLETED", "FAILED"}
                },
                "variables": {},
                "workspace": run.get("workspace"),
                "workflow_run_id": run_id,
                "trace_id": run.get("trace_id"),
            }
            await self.node(compiled, run, node, row, context, active_inputs)
            await asyncio.sleep(0)
