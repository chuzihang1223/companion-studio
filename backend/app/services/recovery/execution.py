"""Per-execution recovery context owned by the existing Runtime, not a scheduler."""

import asyncio
import time
from uuid import UUID, uuid5

from app.services.recovery.checkpoints import RecoveryError
from app.services.recovery.classifier import FailureClassifier
from app.services.recovery.events import emit
from app.services.recovery.planner import RecoveryPlanner
from app.services.recovery.policy import FILE_WRITES, READ_ONLY
from app.services.recovery.reconciliation import resource_snapshot
from app.services.trace.scope import current_trace


class RecoveryExecution:
    def __init__(self, app, context, manager=None, root=None):
        self.app, self.context, self.manager = app, context, manager
        self.root = root or context.task_id
        self.planner = RecoveryPlanner()
        self.call = None
        self.step_id = None
        self.failure = None
        self.blocked = False

    async def before(self, call, index, allowed):
        self.call = call
        self.step_id = str(uuid5(UUID(self.root), call["id"]))
        self.planner.steps += 1
        if self.planner.steps > self.planner.budget.max_total_steps:
            raise RecoveryError("MAX_RETRIES_EXCEEDED")
        if self.manager:
            try:
                intent = await asyncio.to_thread(resource_snapshot, self.context, call)
            except (ValueError, OSError):
                if allowed:
                    raise
                intent = {"kind": "denied"}
            self.step_id = await asyncio.to_thread(
                self.manager.prepare, self.root, call, index, intent
            )
            emit("CHECKPOINT_CREATED", step_id=self.step_id, checkpoint_id=self.step_id)
            await asyncio.to_thread(
                self.manager.step,
                self.step_id,
                "EXECUTING" if allowed else "DENIED",
                permission="freshly_checked" if allowed else "denied",
            )

    async def after(self, result):
        if not self.manager:
            return
        if (
            self.call["name"].startswith(("app.", "browser.", "media.", "music."))
            and self.call["name"] not in READ_ONLY
            and not result.get("error")
            and result.get("verified") is not True
        ):
            result.update(
                verified=False, code="VERIFICATION_FAILED", error="没有提供可核验的外部状态证据。"
            )
            self.blocked = True
            self.classify(result=result)
        if self.call["name"] in FILE_WRITES and not result.get("error"):
            intent = await asyncio.to_thread(self.manager.intent, self.step_id)
            observed = await asyncio.to_thread(resource_snapshot, self.context, self.call)
            if observed.get("before_hash") != intent.get("expected_hash"):
                result.update(
                    verified=False,
                    code="FILE_STATE_CHANGED",
                    error="写入后的实际文件或草稿未达到预期状态。",
                )
                self.blocked = True
                self.classify(result=result)
        await asyncio.to_thread(self.manager.step, self.step_id, "OBSERVED", result=result)
        failed = bool(result.get("error")) or result.get("verified") is False
        if not failed:
            await asyncio.to_thread(self.manager.step, self.step_id, "VERIFIED", result=result)
            await asyncio.to_thread(self.manager.step, self.step_id, "COMMITTED")
            emit(
                "CHECKPOINT_VALIDATED",
                checkpoint_id=self.step_id,
                step_id=self.step_id,
                evidence={"verified": True},
            )
        else:
            await asyncio.to_thread(self.manager.step, self.step_id, "FAILED", result=result)

    def classify(self, error=None, result=None, name=None):
        trace = current_trace.get()
        self.failure = FailureClassifier().classify(
            error,
            result=result,
            tool_name=name or self.call["name"],
            task_id=self.context.task_id,
            step_id=self.step_id or uuid5(UUID(self.root), "step"),
            trace_id=trace.trace_id if trace else None,
        )
        emit("RECOVERY_DETECTED", step_id=self.failure.step_id, code=self.failure.error_code)
        emit("RECOVERY_CLASSIFIED", step_id=self.failure.step_id, code=self.failure.error_code)
        return self.failure

    async def observe(self, call):
        name = call["name"]
        if name.startswith("browser."):
            return await self.app.state.desktop_bridge.request(
                "browser.observe", {}, self.context.work_session_id, timeout=8
            )
        if name.startswith("app."):
            observation = await self.app.state.desktop_bridge.request(
                "app.status", call["arguments"], self.context.work_session_id, timeout=8
            )
            return {
                "running": observation.get("running") is True,
                "has_window": bool(observation.get("windows") or observation.get("window")),
                "verified": observation.get("verified") is True,
            }
        return await asyncio.to_thread(resource_snapshot, self.context, call)

    async def decide(self, error, result, retry_count, call, permitted):
        failure = self.classify(error, result, call["name"])
        observation = {}
        if failure.error_code in {
            "DOM_CHANGED",
            "ELEMENT_NOT_FOUND",
            "PAGE_CHANGED",
            "VERIFICATION_FAILED",
        }:
            try:
                observation = await self.observe(call)
            except (TimeoutError, ValueError, RuntimeError):
                observation = {"verified": False}
        decision = self.planner.plan(
            failure, call, retry_count=retry_count, observation=observation, permission=permitted()
        )
        emit("RECOVERY_PLAN_CREATED", step_id=self.step_id, strategy=decision.strategy)
        self.blocked = decision.approval
        return failure, decision, observation

    async def attempt(self, failure, decision):
        if self.manager:
            attempt_id = await asyncio.to_thread(
                self.manager.attempt, self.root, self.step_id, failure, decision
            )
        else:
            from uuid import uuid4

            attempt_id = str(uuid4())
        emit(
            "RECOVERY_ATTEMPT_STARTED",
            step_id=self.step_id,
            recovery_id=attempt_id,
            strategy=decision.strategy,
        )
        return attempt_id, time.perf_counter()

    async def finish_attempt(self, attempt, successful):
        if not attempt:
            return
        attempt_id, started = attempt
        if self.manager:
            await asyncio.to_thread(
                self.manager.finish_attempt,
                attempt_id,
                "SUCCEEDED" if successful else "FAILED",
                (time.perf_counter() - started) * 1000,
            )
        emit(
            "RECOVERY_ATTEMPT_SUCCEEDED" if successful else "RECOVERY_ATTEMPT_FAILED",
            step_id=self.step_id,
            recovery_id=attempt_id,
            evidence={"verified": successful},
        )
