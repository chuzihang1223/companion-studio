"""Bounded observation/recovery around one existing tool operation."""

import asyncio
import time

from app.services.desktop.lifecycle import TaskCancelled
from app.services.desktop.task_waiter import TaskWaiter
from app.services.recovery.execution import RecoveryExecution
from app.services.recovery.events import emit
from app.services.trace.scope import span_scope


class ObservedFailure(RuntimeError):
    def __init__(self, result):
        super().__init__("VERIFICATION_FAILED")
        self.result = result


async def run_step(
    runtime,
    context,
    title,
    operation,
    handle,
    max_attempts,
    music_identity,
    operation_name,
    permitted,
):
    session, work = context.work_session_id, runtime.app.state.work_state
    recovery = runtime.executions.get(session)
    if recovery is None:
        recovery = RecoveryExecution(runtime.app, context)
    call = recovery.call or {
        "id": "observation",
        "name": operation_name or "system.get_status",
        "arguments": {},
    }
    await handle.checkpoint()
    position = handle.position
    handle.position += 1
    work.step_status(session, position, "active")
    bound = {"music_context": music_identity} if music_identity else {}
    work.emit(session, "step_started", {"title": title, **bound}, status="executing")
    retry_count, pending = 0, None
    while True:
        await handle.checkpoint()
        if not permitted():
            work.step_status(session, position, "failed")
            return {
                "error": "当前授权已失效，未执行工具。",
                "code": "PERMISSION_DENIED",
                "verified": False,
            }
        try:
            if recovery.planner.steps > recovery.planner.budget.max_total_steps:
                raise RuntimeError("MAX_RETRIES_EXCEEDED")
            remaining = recovery.planner.budget.max_task_duration - (
                time.monotonic() - recovery.planner.started
            )
            if remaining <= 0:
                raise TimeoutError()
            with span_scope(
                "operation",
                "OPERATION_STARTED",
                "OPERATION_COMPLETED",
                metadata={"step": title, "operation": "execute"},
            ) as attempt_span:
                result = await TaskWaiter.run(
                    operation,
                    handle,
                    min(remaining, 1200 if title == "代码智能体执行与验证" else 65),
                )
                attempt_span.result(result)
            await handle.checkpoint()
            if result.get("verified") is False or result.get("error"):
                raise ObservedFailure(result)
            await recovery.finish_attempt(pending, True)
            work.step_status(session, position, "done")
            work.emit(
                session,
                "verification",
                {"title": title, "verified": True, "summary": title + "已验证", **bound},
                status="observing",
            )
            return result
        except Exception as error:
            if isinstance(error, TaskCancelled):
                raise
            await recovery.finish_attempt(pending, False)
            pending = None
            result = getattr(error, "result", {})
            failure, decision, _observation = await recovery.decide(
                error, result, retry_count, call, permitted
            )
            if failure.error_code == "TASK_CANCELLED":
                raise TaskCancelled("任务已停止")
            if decision.retry and retry_count < min(
                max_attempts - 1, recovery.planner.budget.max_retry_per_step
            ):
                pending = await recovery.attempt(failure, decision)
                retry_count += 1
                recovery.planner.steps += 1
                work.emit(
                    session,
                    "retrying",
                    {
                        "title": title,
                        "attempt": retry_count,
                        "summary": "依据最新观察执行有界恢复",
                        **bound,
                    },
                    status="retrying",
                )
                await asyncio.sleep(0.3)
                continue
            work.step_status(session, position, "failed")
            if decision.approval:
                emit("USER_TAKEOVER_REQUESTED", step_id=recovery.step_id, code=failure.error_code)
            code = result.get("code") or (
                "APP_EXITED_BEFORE_READY"
                if failure.error_code == "APP_EXITED"
                else "TIMEOUT"
                if failure.error_code == "TOOL_TIMEOUT"
                else failure.error_code.value
            )
            return {
                **result,
                "error": failure.message,
                "public_error": failure.message,
                "code": code,
                "failure_code": failure.error_code.value,
                "verified": False,
            }
