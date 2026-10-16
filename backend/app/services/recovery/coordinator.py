"""Resume owns leases and uses the existing registry, runtime and permission checks."""

import asyncio
import time
from contextlib import nullcontext
from uuid import uuid4

from app.models import RecoveryTask, WorkSession
from app.services.recovery.checkpoints import RecoveryError, decoded, plan_hash, workspace_hash
from app.services.recovery.events import emit
from app.services.recovery.classifier import FailureClassifier
from app.services.recovery.types import RecoveryDecision
from app.services.recovery.planner import validate_call
from app.services.recovery.reconciliation import StateReconciler
from app.services.workspace_service import validate_root


class ResumeCoordinator:
    def __init__(self, app, manager):
        self.app, self.manager = app, manager
        self.tasks = {}
        self.leases = {}
        self.requests = {}
        self.observing = set()
        self.closing = False

    def context(self, root, conversation_id, session_id=None):
        with self.manager.factory() as db:
            row = self.manager.scope(db, root, conversation_id, session_id)
            work = db.get(WorkSession, row.work_session_id)
            stored = decoded(work.plan_json)
            if (
                stored.get("plan_id") != root
                or stored.get("conversation_id") != conversation_id
                or stored.get("work_session_id") != work.id
            ):
                raise RecoveryError("CHECKPOINT_CONFLICT")
            if work.workspace_root:
                validate_root(work.workspace_root)
            context = self.app.state.agent_service._restore_context(self.app, stored)
            if len(context.calls) > 5:
                raise RecoveryError("CHECKPOINT_INVALID")
            for call in context.calls:
                validate_call(call)
            if (
                plan_hash(context) != row.plan_hash
                or workspace_hash(context, self.app.state.work_state) != row.workspace_hash
            ):
                raise RecoveryError("CHECKPOINT_CONFLICT")
            context.recovery_task_id = root
            context.task_id = row.execution_id
            return context

    def busy(self, root, session):
        if (
            self.closing
            or root in self.tasks
            or root in self.observing
            or session in self.app.state.runtime_registry.by_session
        ):
            raise RecoveryError("RECOVERY_BUSY_OR_TERMINAL")

    async def preview(self, root, conversation_id, session_id=None):
        context = await asyncio.to_thread(self.context, root, conversation_id, session_id)
        self.busy(root, context.work_session_id)
        self.observing.add(root)
        try:
            async with self.app.state.runtime_registry.run(
                str(uuid4()), conversation_id, context.work_session_id, context.renderer_id
            ):
                return await StateReconciler(self.app, self.manager).inspect(root, context)
        finally:
            self.observing.discard(root)

    async def resume(
        self, root, conversation_id, session_id, renderer_id, request_id, revision, approved_ids
    ):
        detail = await asyncio.to_thread(self.manager.detail, root, conversation_id, session_id)
        if detail["request_id"] == request_id:
            return {
                "accepted": False,
                "duplicate": True,
                "task_id": root,
                "execution_id": detail["execution_id"],
            }
        context = await asyncio.to_thread(self.context, root, conversation_id, session_id)
        current = await asyncio.to_thread(self.manager.detail, root, conversation_id, session_id)
        if current["request_id"] == request_id:
            return {
                "accepted": False,
                "duplicate": True,
                "task_id": root,
                "execution_id": current["execution_id"],
            }
        if self.requests.get(root) == request_id:
            return {"accepted": False, "duplicate": True, "task_id": root}
        self.busy(root, session_id)
        if set(approved_ids) - {call["id"] for call in context.calls}:
            raise RecoveryError("INVALID_APPROVAL_SCOPE", 422)
        claim = await asyncio.to_thread(
            self.manager.claim, root, conversation_id, session_id, request_id, revision
        )
        if claim is None:
            return {"accepted": False, "duplicate": True, "task_id": root}
        lease, execution_id = claim
        context.task_id, context.renderer_id = execution_id, renderer_id
        self.leases[root] = lease
        self.requests[root] = request_id
        self.tasks[root] = asyncio.create_task(
            self._resume(root, lease, context, set(approved_ids))
        )
        self.tasks[root].add_done_callback(self.completed)
        return {"accepted": True, "duplicate": False, "task_id": root, "execution_id": execution_id}

    async def _resume(self, root, lease, context, approved_ids):
        session = context.work_session_id
        attempt_id, started, successful = None, time.perf_counter(), False
        step = None
        try:
            tracing = await self.tracing(root, context)
            with tracing:
                async with self.app.state.runtime_registry.run(
                    context.task_id, context.conversation_id, session, context.renderer_id
                ):
                    detail = await asyncio.to_thread(
                        self.manager.detail, root, context.conversation_id
                    )
                    step = detail["checkpoints"][0] if detail["checkpoints"] else None
                    failure = FailureClassifier().classify(
                        result={"code": detail["failure"].get("error_code", "UNKNOWN_ERROR")},
                        tool_name=step["tool_name"] if step else "unknown",
                        task_id=context.task_id,
                        step_id=step["step_id"] if step else root,
                    )
                    attempt_id = await asyncio.to_thread(
                        self.manager.attempt,
                        root,
                        str(failure.step_id),
                        failure,
                        RecoveryDecision(level=1, strategy="safe_resume", observe=True),
                    )
                    emit("RECOVERY_ATTEMPT_STARTED", recovery_id=attempt_id, strategy="safe_resume")
                    preview = await StateReconciler(self.app, self.manager).inspect(root, context)
                    if not preview["can_resume"]:
                        await asyncio.to_thread(self.manager.state, root, "NEEDS_REVIEW")
                        emit("RESUME_BLOCKED", code="SIDE_EFFECT_UNCERTAIN")
                        self.notify(session, "recovery_blocked", "failed")
                        return
                    needs_approval = {
                        row["tool_call_id"] for row in preview["items"] if row["requires_approval"]
                    }
                    if needs_approval - approved_ids:
                        await asyncio.to_thread(self.manager.state, root, "NEEDS_REVIEW")
                        emit("RESUME_BLOCKED", code="PERMISSION_DENIED")
                        self.notify(session, "recovery_blocked", "failed")
                        return
                    context.resume_verified = {
                        row["tool_call_id"]
                        for row in preview["items"]
                        if row["action"] == "SKIP_VERIFIED"
                    }
                    await asyncio.to_thread(self.manager.state, root, "RECOVERY_AVAILABLE")
                    await asyncio.to_thread(self.manager.state, root, "RESUMING")
                    emit("RESUME_STARTED", strategy="safe_resume")
                    self.notify(session, "recovery_resuming", "observing")
                    response = await self.app.state.agent_service._execute_context(
                        self.app, context, approved_ids, "natural", False, 1.0
                    )
                    self.app.state.work_state.emit(
                        session,
                        "recovery_completed",
                        {
                            "message_id": response["message_id"],
                            "verified": response.get("goal_verified") is True,
                        },
                    )
                    successful = (
                        self.manager.detail(root, context.conversation_id)["state"] == "COMPLETED"
                    )
                    emit(
                        "RECOVERY_ATTEMPT_SUCCEEDED" if successful else "RECOVERY_ATTEMPT_FAILED",
                        recovery_id=attempt_id,
                        evidence={"verified": successful},
                    )
                    emit(
                        "RESUME_COMPLETED",
                        evidence={
                            "verified": self.manager.detail(root, context.conversation_id)["state"]
                            == "COMPLETED"
                        },
                    )
        except asyncio.CancelledError:
            await self.settle(root, "INTERRUPTED" if self.closing else "CANCELLED")
            self.notify(
                session,
                "task_interrupted" if self.closing else "task_cancelled",
                "interrupted" if self.closing else "cancelled",
            )
            raise
        except Exception as error:
            failure = FailureClassifier().classify(
                error,
                tool_name=step["tool_name"] if step else "unknown",
                task_id=context.task_id,
                step_id=step["step_id"] if step else root,
            )
            await self.settle(root, "NEEDS_REVIEW", failure)
            recorder = getattr(self.app.state, "trace_recorder", None)
            segment = recorder.contexts.get(context.task_id) if recorder else None
            if segment:
                # The execution scope has already recorded TASK_FAILED and reset
                # its ContextVar. Append the fixed recovery diagnosis to this new
                # execution segment explicitly, never to the old interrupted Trace.
                segment.send(
                    "event",
                    event_type="RESUME_BLOCKED",
                    phase="recovery",
                    status="failed",
                    error_code=failure.error_code,
                    metadata={"origin_task_id": root},
                )
            self.notify(session, "task_failed", "failed")
        finally:
            from app.services.trusted_actions import trusted_actions

            trusted_actions.finish_task(session)
            self.app.state.task_runtime.handles.pop(session, None)
            try:
                if attempt_id:
                    await asyncio.to_thread(
                        self.manager.finish_attempt,
                        attempt_id,
                        "SUCCEEDED" if successful else "INTERRUPTED" if self.closing else "FAILED",
                        (time.perf_counter() - started) * 1000,
                    )
            finally:
                await self.finalize(root)

    async def tracing(self, root, context):
        recorder = getattr(self.app.state, "trace_recorder", None)
        if recorder:
            try:
                segment = recorder.create(context.task_id, context.conversation_id)
                segment.bind(context.conversation_id, context.work_session_id)
                segment.send(
                    "event",
                    event_type="RESUME_REQUESTED",
                    phase="recovery",
                    metadata={"origin_task_id": root},
                )
                return await recorder.execution_scope(
                    context.task_id, context.conversation_id, context.work_session_id
                )
            except Exception:
                pass  # An unavailable observer never causes an operation retry.
        return nullcontext()

    def notify(self, session, kind, status):
        if kind == "recovery_blocked":
            kind = "task_failed"
        self.app.state.work_state.emit(
            session, kind, {"summary": "恢复状态已更新，请查看恢复预览。"}, status=status
        )

    async def settle(self, root, state, failure=None):
        with self.manager.factory() as db:
            row = db.get(RecoveryTask, root)
            current = row.state if row else None
        if current in {"REVALIDATING", "RECOVERING", "RESUMING", "WAITING_USER"}:
            if current == "REVALIDATING" and state == "INTERRUPTED":
                state = "NEEDS_REVIEW"
            await asyncio.to_thread(self.manager.state, root, state, failure)

    async def cancel(self, root, conversation_id, session_id):
        await asyncio.to_thread(self.manager.detail, root, conversation_id, session_id)
        await asyncio.to_thread(self.manager.state, root, "CANCELLED")
        if root in self.tasks:
            task = self.tasks[root]
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.finalize(root)
        else:
            self.app.state.task_runtime.control(session_id, "cancel")
        return {"cancelled": True, "task_id": root}

    async def revalidate(self, root, conversation_id, session_id):
        preview = await self.preview(root, conversation_id, session_id)
        detail = self.manager.detail(root, conversation_id, session_id)
        if detail["state"] not in {"COMPLETED", "CANCELLED", "UNRECOVERABLE"}:
            self.manager.state(root, "REVALIDATING")
            self.manager.state(
                root, "RECOVERY_AVAILABLE" if preview["can_resume"] else "NEEDS_REVIEW"
            )
        return preview

    async def close(self):
        self.closing = True
        items = list(self.tasks.items())
        tasks = [task for _, task in items]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for root, _ in items:
            try:
                await asyncio.to_thread(self.manager.settle, root, "INTERRUPTED")
            except Exception:
                pass  # DB unavailability cannot prevent owned coroutine cleanup.
            try:
                await self.finalize(root)
            except Exception:
                pass

    async def control(self, root, conversation_id, session_id, action):
        detail = await asyncio.to_thread(self.manager.detail, root, conversation_id, session_id)
        execution = self.app.state.task_runtime.executions.get(session_id)
        if execution and execution.root == root:
            self.app.state.task_runtime.control(
                session_id, "resume" if action == "continue" else action
            )
        elif action == "takeover" and detail["state"] not in {
            "COMPLETED",
            "CANCELLED",
            "UNRECOVERABLE",
        }:
            await asyncio.to_thread(self.manager.state, root, "NEEDS_REVIEW")
        else:
            raise RecoveryError("NO_ACTIVE_RECOVERY")
        return {"task_id": root, "action": action}

    async def restart(self, root, conversation_id, session_id, renderer_id, consent):
        if not consent:
            raise RecoveryError("MODEL_CONSENT_REQUIRED", 422)
        detail = await asyncio.to_thread(self.manager.detail, root, conversation_id, session_id)
        self.busy(root, session_id)
        if any(
            s["side_effect_class"] != "READ_ONLY" and s["step_state"] not in {"PREPARED", "DENIED"}
            for s in detail["checkpoints"]
        ):
            raise RecoveryError("UNSAFE_RESTART_REQUIRES_NEW_USER_GOAL")
        context = await asyncio.to_thread(self.context, root, conversation_id, session_id)
        # New plan/task/call IDs and ordinary approval UI; never execute here.
        return await self.app.state.agent_service.plan(
            self.app,
            context.character_id,
            conversation_id,
            context.user_text,
            renderer_id=renderer_id,
        )

    async def finalize(self, root):
        lease = self.leases.pop(root, None)
        try:
            if lease:
                await asyncio.to_thread(self.manager.release, root, lease)
        finally:
            self.tasks.pop(root, None)
            self.requests.pop(root, None)

    @staticmethod
    def completed(task):
        if not task.cancelled() and task.exception() is not None:
            import logging

            logging.getLogger(__name__).warning("recovery_cleanup_unavailable")
