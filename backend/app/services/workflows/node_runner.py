"""One scheduled node: timeout, original runtime, result limits and error outlet."""

import asyncio
import json
import time
from app.services.trace.scope import span_scope
from app.services.workflows.contracts import WorkflowError, bounded
from app.services.workflows.variables import resolve
from app.services.workflows.trace_adapter import emit
from app.services.llm.usage import EvaluationBudgetExceeded
from app.services.workflows.port_types import check_port


class WorkflowNodeRunner:
    async def node(self, compiled, run, node, row, context, active_inputs):
        run_id, owner = run["run_id"], run["conversation_id"]
        started = time.perf_counter()
        agent = None
        self.repository.change(
            run_id,
            owner,
            {},
            node_id=node.node_id,
            node_changes={"state": "READY", "trace_id": run.get("trace_id")},
            event="WORKFLOW_NODE_READY",
        )
        emit("WORKFLOW_NODE_READY", run, row)
        try:
            with span_scope(
                "workflow_node",
                "WORKFLOW_NODE_STARTED",
                "WORKFLOW_NODE_COMPLETED",
                phase="workflow",
                metadata={"workflow_run_id": run_id, "node_execution_id": row["node_execution_id"]},
            ) as span:
                self.repository.change(
                    run_id,
                    owner,
                    {},
                    node_id=node.node_id,
                    node_changes={"state": "RUNNING", "attempt": row["attempt"] + 1},
                    event="WORKFLOW_NODE_STARTED",
                )
                async with asyncio.timeout(node.timeout):
                    for item in active_inputs:
                        check_port(
                            node.input_ports[item["target_port"]], item["output"], node.node_id
                        )
                    if node.type in {"tool", "mcp_tool"}:
                        if self.scope(owner) != run.get("workspace"):
                            raise WorkflowError("WORKFLOW_WORKSPACE_CHANGED", node.node_id)
                        reservation = (
                            node.retry_policy.max_attempts
                            if node.retry_policy.strategy == "safe_read"
                            else 1
                        )
                        if (
                            run.get("tool_call_budget_used", 0) + reservation
                            > compiled.definition.budget.max_tool_calls
                        ):
                            raise WorkflowError("WORKFLOW_TOOL_BUDGET", node.node_id)
                        arguments = bounded(resolve(node.config["arguments"], context), limit=16000)
                        agent, pending = self.bridge.prepare(
                            run, node, arguments, run.get("workspace")
                        )
                        self.repository.change(
                            run_id,
                            owner,
                            {},
                            node_id=node.node_id,
                            node_changes={
                                "recovery_task_id": agent.task_id,
                                "work_session_id": agent.work_session_id,
                            },
                        )
                        approved = set()
                        if pending["call"]["requires_confirmation"]:
                            await self.wait_permission(run, node, row, pending)
                            approved = {pending["call"]["id"]}
                        self.repository.change(
                            run_id,
                            owner,
                            {
                                "tool_calls": run["tool_calls"] + 1,
                                "tool_call_budget_used": run.get("tool_call_budget_used", 0)
                                + reservation,
                            },
                        )
                        output, goal_verified = await self.bridge.execute(agent, approved)
                        self.repository.change(
                            run_id,
                            owner,
                            {},
                            node_id=node.node_id,
                            node_changes={"goal_verified": goal_verified},
                        )
                    elif node.type == "human_approval":
                        output = resolve(node.config.get("value"), context)
                        await self.wait_permission(
                            run,
                            node,
                            row,
                            {
                                "kind": "human",
                                "message": str(node.config["message"])[:2000],
                                "value": bounded(output, limit=16000),
                            },
                        )
                    else:
                        if node.type in {"llm_agent", "agent_skill"}:
                            if run["model_calls"] >= compiled.definition.budget.max_model_calls:
                                raise WorkflowError("WORKFLOW_MODEL_BUDGET", node.node_id)
                            self.repository.change(
                                run_id, owner, {"model_calls": run["model_calls"] + 1}
                            )
                        output = await self.executor.execute(node, context, active_inputs)
                    bounded(output, limit=compiled.definition.budget.max_intermediate_bytes)
                    total = len(json.dumps(output, ensure_ascii=False).encode()) + sum(
                        len(json.dumps(n["output"], ensure_ascii=False).encode())
                        for n in run["nodes"]
                    )
                    if total > compiled.definition.budget.max_intermediate_bytes:
                        raise WorkflowError("WORKFLOW_INTERMEDIATE_BUDGET", node.node_id)
                port = "true" if output is True else "false" if node.type == "condition" else "out"
                if node.type != "condition":
                    port = "out"
                check_port(node.output_ports.get(port, "any"), output, node.node_id)
                self.repository.change(
                    run_id,
                    owner,
                    {},
                    node_id=node.node_id,
                    node_changes={
                        "state": "COMPLETED",
                        "output": output,
                        "active_port": port,
                        "pending": None,
                        "duration_ms": (time.perf_counter() - started) * 1000,
                    },
                    event="WORKFLOW_NODE_COMPLETED",
                )
                if node.type == "condition":
                    emit("WORKFLOW_CONDITION_EVALUATED", run, row, verified=output)
                span.metadata["verified"] = True
        except BaseException as error:
            code = (
                "CANCELLED"
                if isinstance(error, asyncio.CancelledError)
                else "TIMEOUT"
                if isinstance(error, TimeoutError)
                else "WORKFLOW_MODEL_BUDGET"
                if isinstance(error, EvaluationBudgetExceeded)
                else getattr(error, "code", "WORKFLOW_NODE_FAILED")
            )
            state = (
                "INTERRUPTED"
                if self.closing
                else "CANCELLED"
                if isinstance(error, asyncio.CancelledError)
                else "FAILED"
            )
            self.repository.change(
                run_id,
                owner,
                {},
                node_id=node.node_id,
                node_changes={
                    "state": state,
                    "error_code": code,
                    "active_port": "error",
                    "output": {"error_code": code},
                    "pending": None,
                    "duration_ms": (time.perf_counter() - started) * 1000,
                },
                event="WORKFLOW_NODE_FAILED",
            )
            emit("WORKFLOW_NODE_FAILED", run, row)
            routed = any(
                e.source_node_id == node.node_id and e.source_port == "error"
                for e in compiled.definition.edges
            )
            if (
                not routed
                or isinstance(error, asyncio.CancelledError)
                or code in {"PERMISSION_DENIED", "MCP_SIDE_EFFECT_UNKNOWN"}
            ):
                raise
        finally:
            if agent:
                from app.services.trusted_actions import trusted_actions

                trusted_actions.finish_task(agent.work_session_id)
                self.app.state.task_runtime.handles.pop(agent.work_session_id, None)
                record = next(
                    n
                    for n in self.repository.run(run_id, owner)["nodes"]
                    if n["node_id"] == node.node_id
                )
                if record["state"] != "COMPLETED":
                    self.app.state.checkpoint_manager.settle(
                        agent.task_id,
                        "INTERRUPTED"
                        if self.closing
                        else "CANCELLED"
                        if record["state"] == "CANCELLED"
                        else "NEEDS_REVIEW",
                    )
                    self.app.state.work_state.emit(
                        agent.work_session_id,
                        "task_failed",
                        {"summary": "工作流节点未完成"},
                        status="failed",
                    )
