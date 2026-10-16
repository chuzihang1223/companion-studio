"""Run recovery faults through the SAME production Agent/Trace/Evaluation chain."""

import asyncio
import time
from uuid import uuid4

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models import Conversation, RecoveryTask
from app.services.evaluation.fixtures import FIXTURE_TEXT, MARKDOWN, digest
from app.services.evaluation.grader import EvaluationGrader
from app.services.evaluation.recovery_fixtures import ControlledCrash, FaultExecutor
from app.services.recovery.checkpoints import RecoveryError
from app.services.recovery.coordinator import ResumeCoordinator
from app.services.runtime_registry import RuntimeTaskRegistry


async def run_case(app, store, case, payload, workspace, guarded_executor):
    state, key = app.state, case.case_id
    started = time.perf_counter()
    evidence = {
        "boundary": "simulated_desktop_real_files",
        "unauthorized_actions": 0,
        "real_gui": False,
        "real_script_executed": False,
    }
    replies, accepted, paused = [], 0, False
    fault = None
    if key == "REC-012" and payload.get("restart_stage") == "reopen":
        with SessionLocal() as db:
            row = db.scalar(select(RecoveryTask))
            if not row:
                raise RecoveryError("CHECKPOINT_INVALID")
            root, conversation, session = row.id, row.conversation_id, row.work_session_id
        before_resume_mtime = (workspace / "result.md").stat().st_mtime_ns
        evidence["worker_restart_observed"] = payload.get("parent_observed_restart") is True
        evidence["loaded_interrupted_checkpoint"] = (
            state.checkpoint_manager.detail(root, conversation)["state"] == "INTERRUPTED"
        )
    else:
        with SessionLocal() as db:
            conversation_row = Conversation(
                character_id="assistant", conversation_type="work", preferred_mode="work"
            )
            db.add(conversation_row)
            db.commit()
            conversation = conversation_row.id
        session = state.work_state.ensure(conversation)
        state.work_state.set_workspace(session, str(workspace))
        plan = await state.agent_service.plan(app, "assistant", conversation, case.input)
        root, session = plan["plan_id"], plan["work_session_id"]
        context = state.agent_service.pending[root]
        fault = FaultExecutor(guarded_executor(context.executor, case), key)
        if key == "REC-012":
            loop = asyncio.get_running_loop()
            fault.before_crash = lambda: asyncio.run_coroutine_threadsafe(
                store.flush(), loop
            ).result(3)
        context.executor = fault
        approved = {call["id"] for call in context.calls} if key != "REC-009" else set()
        task = asyncio.create_task(
            state.agent_service.execute(app, root, approved, "natural", False, 1.0)
        )
        if key in {"REC-010", "REC-011"}:
            await asyncio.wait_for(state.desktop_bridge.started.wait(), 3)
            state.task_runtime.control(session, "pause" if key == "REC-010" else "cancel")
            if key == "REC-010":
                state.desktop_bridge.release.set()
                await asyncio.sleep(0.03)
                paused = (
                    not task.done()
                    and state.checkpoint_manager.detail(root, conversation)["state"]
                    == "WAITING_USER"
                )
                state.task_runtime.control(session, "resume")
        try:
            replies.append(await task)
        except ControlledCrash:
            # Logical fault cases retain ordinary finally; REC-012 independently
            # hard-exits its process to cover actual abrupt SQLite/WAL recovery.
            pass
        except asyncio.CancelledError:
            evidence["cancel_observed"] = key == "REC-011"
        if key in {"REC-002", "REC-003", "REC-013", "REC-015"}:
            state.checkpoint_manager.recover()
            state.runtime_registry = RuntimeTaskRegistry()
            state.runtime_registry.trace_recorder = state.trace_recorder
            state.work_state.registry = state.runtime_registry
            state.work_state.recover_interrupted()
            state.recovery = ResumeCoordinator(app, state.checkpoint_manager)

    manager, coordinator = state.checkpoint_manager, state.recovery
    preview = None
    if key in {"REC-002", "REC-003", "REC-012", "REC-013", "REC-014", "REC-015"}:
        if key == "REC-015":
            replacement = workspace.parent / "replacement-workspace"
            replacement.mkdir()
            state.work_state.set_workspace(session, str(replacement))
        try:
            preview = await coordinator.preview(root, conversation, session)
            evidence["preview_can_resume"] = preview["can_resume"]
            evidence["preview_conflict"] = any(
                row["action"] == "REVIEW" for row in preview["items"]
            )
        except RecoveryError as error:
            evidence["workspace_change_blocked"] = error.code == "CHECKPOINT_CONFLICT"
        if key in {"REC-003", "REC-012", "REC-013"}:
            if not preview or not preview["can_resume"]:
                evidence["resume_blocked"] = True
            else:
                detail = manager.detail(root, conversation)
                request_id = str(uuid4())
                calls = [
                    coordinator.resume(
                        root,
                        conversation,
                        session,
                        "evaluation-renderer",
                        request_id,
                        detail["revision"],
                        [],
                    )
                    for _ in range(2 if key == "REC-013" else 1)
                ]
                outcomes = await asyncio.gather(*calls)
                accepted = sum(bool(row["accepted"]) for row in outcomes)
                await asyncio.gather(*list(coordinator.tasks.values()))
    detail = manager.detail(root, conversation)
    await state.runtime_registry.close()
    await store.flush()
    traces = state.trace_repository.listing(conversation, work_session_id=session)["items"]
    events, summaries = [], []
    for trace in traces:
        events += state.trace_repository.events(trace["trace_id"], conversation, limit=250)["items"]
        summaries.append(state.trace_repository.summary(trace["trace_id"], conversation))
    typed = {row["event_type"] for row in events}
    codes = {row["error_code"] for row in events}
    calls = [operation for operation, _ in state.desktop_bridge.calls]
    file_calls, writes = getattr(fault, "calls", 0), getattr(fault, "writes", 0)
    checkpoints = detail["checkpoints"]
    counter = detail["counters"]
    executed = sum(row["tools_executed"] for row in summaries)
    evidence.update(
        goal_completed=detail["state"] == "COMPLETED",
        recovery_state=detail["state"],
        resources_released=not state.runtime_registry.active
        and not state.desktop_bridge.waiting
        and not coordinator.tasks
        and not coordinator.leases,
        checkpoint_count=len(checkpoints),
        checkpoint_committed=bool(checkpoints)
        and all(row["step_state"] == "COMMITTED" for row in checkpoints),
        authorized_execution=key != "REC-009" or executed == 0,
        pause_observed=paused,
        accepted_resumes=accepted,
        state_reobserved="app.status" in calls or "browser.observe" in calls,
        typed_recovery="RECOVERY_CLASSIFIED" in typed,
        trace_final_consistent=any(
            row["status"] == "completed" and row["task_id"] == detail["execution_id"]
            for row in traces
        )
        if detail["state"] == "COMPLETED"
        else True,
        trace_ownership=all(
            row["conversation_id"] == conversation and row["work_session_id"] == session
            for row in events
        ),
        trace_has_interruption=any(row["status"] == "interrupted" for row in traces),
        trace_incomplete_count=sum(row["incomplete"] for row in traces),
        created_hash_matches=(workspace / "result.md").is_file()
        and digest((workspace / "result.md").read_text(encoding="utf-8")) == digest(MARKDOWN),
        changed_file_preserved=(workspace / "fixture.txt").read_text(encoding="utf-8")
        == "EXTERNALLY_CHANGED\n",
        fixture_read_unchanged=(workspace / "fixture.txt").read_text(encoding="utf-8")
        == FIXTURE_TEXT,
        side_effect_replay_blocked=key != "REC-014" or bool(preview and not preview["can_resume"]),
        duplicate_execution_count=max(0, executed - 1)
        if key in {"REC-003", "REC-012", "REC-013", "REC-015"}
        else 0,
        native_operations=0,
    )
    if key == "REC-012":
        evidence["file_not_rewritten"] = (
            workspace / "result.md"
        ).stat().st_mtime_ns == before_resume_mtime
    conditions = {
        "REC-001": detail["state"] == "COMPLETED"
        and file_calls == 2
        and counter.get("retry_count") == 1,
        "REC-002": evidence.get("preview_conflict") is True and evidence["changed_file_preserved"],
        "REC-003": evidence["created_hash_matches"]
        and accepted == 1
        and evidence["checkpoint_committed"]
        and evidence["duplicate_execution_count"] == 0,
        "REC-004": "TARGET_WINDOW_CLOSED" in codes and calls.count("app.focus") == 1,
        "REC-005": evidence["state_reobserved"]
        and calls.count("app.focus") == 2
        and detail["state"] == "COMPLETED",
        "REC-006": evidence["state_reobserved"]
        and calls.count("browser.search_site") == 2
        and detail["state"] == "COMPLETED",
        "REC-007": evidence["state_reobserved"]
        and calls.count("browser.search_site") == 1
        and detail["state"] == "NEEDS_REVIEW",
        "REC-008": counter.get("retry_count") == 1 and detail["state"] == "COMPLETED",
        "REC-009": "PERMISSION_DENIED" in codes and not calls and executed == 0,
        "REC-010": paused and detail["state"] == "COMPLETED",
        "REC-011": evidence.get("cancel_observed") is True and detail["state"] == "CANCELLED",
        "REC-012": evidence.get("worker_restart_observed") is True
        and evidence.get("loaded_interrupted_checkpoint") is True
        and accepted == 1
        and evidence.get("file_not_rewritten") is True
        and evidence["trace_has_interruption"],
        "REC-013": accepted == 1
        and counter.get("resume_count") == 1
        and evidence["duplicate_execution_count"] == 0,
        "REC-014": evidence["side_effect_replay_blocked"] and file_calls == 1,
        "REC-015": evidence.get("workspace_change_blocked") is True and writes == 1,
        "REC-016": detail["state"] == "COMPLETED"
        and {"RECOVERY_ATTEMPT_STARTED", "RECOVERY_ATTEMPT_SUCCEEDED"} <= typed
        and evidence["trace_final_consistent"],
    }
    evidence["condition_verified"] = bool(conditions[key])
    observation = {
        "evidence": evidence,
        "selected_tools": [recovery_call_name(case)],
        # Monotonic time cannot span a hard process exit. Do not label only the
        # resumed segment as the full original task's duration.
        "duration_ms": None if key == "REC-012" else (time.perf_counter() - started) * 1000,
        "summary": {
            field: sum(row[field] for row in summaries)
            for field in ("tools_executed", "model_requests", "retries")
        },
        "model_usage": {"status": "Not applicable (simulated)"},
        "recovery_metrics": {
            "attempt_count": counter.get("recovery_attempts", 0),
            "successful_attempts": counter.get("recovery_successes", 0),
            "duration_ms": counter.get("recovery_duration_ms", 0),
            "replanning_count": counter.get("replanning_count", 0),
            "duplicate_execution_count": evidence["duplicate_execution_count"],
            "checkpoint_checks": counter.get("checkpoint_checks", 0),
            "validated_checkpoints": counter.get("validated_checkpoints", 0),
            "unresolved_side_effects": int(evidence.get("preview_conflict", False)),
            "human_interventions": int(paused),
            "goal_completed": evidence["goal_completed"],
        },
    }
    result = EvaluationGrader().grade(case, observation, traces)
    result["trace_scopes"] = [
        {
            field: row[field]
            for field in ("trace_id", "task_id", "conversation_id", "work_session_id")
        }
        for row in traces
    ]
    return {
        "result": result,
        "links": traces[0] if traces else {},
        "budget": {"requests": 0, "total_tokens": None},
    }


def recovery_call_name(case):
    from app.services.evaluation.recovery_fixtures import recovery_call

    return recovery_call(case.case_id)[0]
