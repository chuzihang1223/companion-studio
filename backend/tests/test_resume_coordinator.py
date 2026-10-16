"""Production plan/runtime/registry/SQLite/Trace/resume; model and desktop boundaries mocked."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.api.agent_recovery import router
from app.models import AgentCheckpoint, Message, RecoveryTask
from app.services.recovery.checkpoints import CheckpointManager, RecoveryError
from app.services.recovery.coordinator import ResumeCoordinator
from app.services.runtime_registry import RuntimeTaskRegistry
from test_agent_trace import runtime_app as runtime_app
from test_trace_persistence import trace_database as trace_database


class SimulatedCrash(BaseException):
    """Logical crash boundary; separate process-kill acceptance is still required."""


@pytest.fixture
def recovery_app(runtime_app, monkeypatch):
    app, store, conversation, session = runtime_app
    manager = CheckpointManager(store.factory)
    manager.on_change = app.state.work_state.notify_recovery
    app.state.checkpoint_manager = manager
    app.state.recovery_required = True
    app.state.recovery = ResumeCoordinator(app, manager)
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "local-recovery-test")
    app.include_router(router)
    return app, store, conversation, session


async def crashed_write(app, conversation=None, *, read_only=False):
    if read_only:
        app.state.llm_service.provider.tool = "code_read_file"
    plan = await app.state.agent_service.plan(
        app, "assistant", conversation, "帮我写一个 Python 示例"
    )
    if read_only:
        context = app.state.agent_service.pending[plan["plan_id"]]
        context.executor._path("fixture.txt").write_text("controlled read\n", encoding="utf-8")
    original = app.state.task_runtime.execute_call
    calls = []

    async def crash(context, call, handle):
        result = await original(context, call, handle)
        calls.append(call["id"])
        assert not result.get("error"), result
        raise SimulatedCrash()

    app.state.task_runtime.execute_call = crash
    try:
        with pytest.raises(SimulatedCrash):
            await app.state.agent_service.execute(
                app, plan["plan_id"], {c["id"] for c in plan["calls"]}, "natural", False, 1.0
            )
    finally:
        app.state.task_runtime.execute_call = original
    manager = CheckpointManager(app.state.checkpoint_manager.factory)
    manager.on_change = app.state.work_state.notify_recovery
    manager.recover()
    app.state.checkpoint_manager = manager
    app.state.runtime_registry = RuntimeTaskRegistry()
    app.state.work_state.registry = app.state.runtime_registry
    app.state.work_state.recover_interrupted()
    app.state.runtime_registry.trace_recorder = app.state.trace_recorder
    app.state.recovery = ResumeCoordinator(app, manager)
    return plan, calls


def test_resume_written_draft_deduplicates_and_reuses_trace_with_no_model_calls(recovery_app):
    app, store, _, _ = recovery_app

    async def run():
        store.start()
        plan, executed = await crashed_write(app)
        root, conversation, session = (
            plan["plan_id"],
            plan["conversation_id"],
            plan["work_session_id"],
        )
        manager, coordinator = app.state.checkpoint_manager, app.state.recovery
        detail = manager.detail(root, conversation)
        assert (
            detail["state"] == "INTERRUPTED"
            and detail["checkpoints"][0]["step_state"] == "EXECUTING"
        )
        preview = await coordinator.preview(root, conversation, session)
        assert preview["can_resume"] and preview["items"][0]["action"] == "SKIP_VERIFIED"
        model_calls = app.state.llm_service.provider.calls
        request = str(uuid4())
        first, second = await asyncio.gather(
            *[
                coordinator.resume(
                    root, conversation, session, "new-renderer", request, detail["revision"], []
                )
                for _ in range(2)
            ]
        )
        assert sum(bool(r["accepted"]) for r in (first, second)) == 1
        await asyncio.gather(*list(coordinator.tasks.values()))
        assert manager.detail(root, conversation)["state"] == "COMPLETED"
        assert manager.detail(root, conversation)["checkpoints"][0]["step_state"] == "COMMITTED"
        assert len(executed) == 1 and app.state.llm_service.provider.calls == model_calls
        assert not coordinator.tasks and not coordinator.leases
        assert not app.state.runtime_registry.active
        assert await store.flush()
        traces = app.state.trace_repository.listing(conversation)["items"]
        resumed = next(t for t in traces if t["task_id"] != root)
        events = app.state.trace_repository.events(resumed["trace_id"], conversation)["items"]
        assert {
            "RESUME_REQUESTED",
            "RESUME_STARTED",
            "RESUME_COMPLETED",
            "CHECKPOINT_VALIDATED",
        } <= {e["event_type"] for e in events}
        assert resumed["status"] == "completed"
        assert any(e["metadata"].get("origin_task_id") == root for e in events)
        assert not any(e["event_type"] == "MODEL_REQUEST_STARTED" for e in events)
        with store.factory() as db:
            assert (
                len(
                    list(
                        db.scalars(
                            select(Message).where(
                                Message.conversation_id == conversation, Message.role == "user"
                            )
                        )
                    )
                )
                == 1
            )
        await coordinator.close()
        await store.stop()

    asyncio.run(run())


def test_normal_success_never_enters_recovery_and_storage_failure_prevents_tool(
    recovery_app, monkeypatch
):
    app, store, _, _ = recovery_app

    async def run():
        store.start()
        plan = await app.state.agent_service.plan(app, "assistant", None, "帮我写一个 Python 示例")
        await app.state.agent_service.execute(
            app, plan["plan_id"], {c["id"] for c in plan["calls"]}, "natural", False, 1.0
        )
        detail = app.state.checkpoint_manager.detail(plan["plan_id"], plan["conversation_id"])
        assert (
            detail["state"] == "COMPLETED" and detail["counters"].get("recovery_attempts", 0) == 0
        )
        assert await store.flush()
        trace = app.state.trace_repository.listing(plan["conversation_id"])["items"][0]
        events = app.state.trace_repository.events(trace["trace_id"], plan["conversation_id"])[
            "items"
        ]
        assert not any(e["event_type"] == "RECOVERY_DETECTED" for e in events)
        second = await app.state.agent_service.plan(
            app, "assistant", None, "帮我写一个 Python 示例"
        )
        executed = []

        async def prohibited(*args):
            executed.append(True)
            return {"verified": True}

        def fail(*args):
            raise RecoveryError("CHECKPOINT_STORAGE_UNAVAILABLE", 503)

        monkeypatch.setattr(app.state.checkpoint_manager, "prepare", fail)
        monkeypatch.setattr(app.state.task_runtime, "execute_call", prohibited)
        with pytest.raises(RecoveryError):
            await app.state.agent_service.execute(
                app, second["plan_id"], {c["id"] for c in second["calls"]}, "natural", False, 1.0
            )
        assert executed == [] and not app.state.runtime_registry.active
        await store.stop()

    asyncio.run(run())


def test_recovery_api_auth_scope_preview_and_early_cancel(recovery_app):
    app, store, _, _ = recovery_app

    async def run():
        store.start()
        plan, _ = await crashed_write(app)
        root, conversation, session = (
            plan["plan_id"],
            plan["conversation_id"],
            plan["work_session_id"],
        )
        headers = {"X-Companion-Control-Token": "local-recovery-test"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            url = "/api/agent/recovery/tasks/" + root
            query = {"conversation_id": conversation, "work_session_id": session}
            assert (await client.get(url, params=query)).status_code == 403
            assert (
                await client.get(url, params={"conversation_id": str(uuid4())}, headers=headers)
            ).status_code == 404
            assert (await client.get(url + "/checkpoints", params=query, headers=headers)).json()[
                "items"
            ]
            assert (await client.get(url + "/preview", params=query, headers=headers)).json()[
                "can_resume"
            ]
            detail = (await client.get(url, params=query, headers=headers)).json()
            body = {
                **query,
                "renderer_id": "test-renderer",
                "request_id": str(uuid4()),
                "revision": detail["revision"],
            }
            assert (
                await client.post(url + "/restart", json=body, headers=headers)
            ).status_code == 422
            assert (
                await client.post(
                    url + "/resume", json={**body, "path": "forbidden"}, headers=headers
                )
            ).status_code == 422
            # Cancel immediately after accepting, including before _resume's first timeslice.
            coordinator = app.state.recovery
            result = await coordinator.resume(
                root,
                conversation,
                session,
                "test-renderer",
                body["request_id"],
                detail["revision"],
                [],
            )
            assert result["accepted"]
            await coordinator.cancel(root, conversation, session)
            cancelled = app.state.checkpoint_manager.detail(root, conversation)
            assert (
                cancelled["state"] == "CANCELLED"
                and not coordinator.tasks
                and not coordinator.leases
            )
            assert (
                await client.post(
                    url + "/resume",
                    json={**body, "request_id": str(uuid4()), "revision": cancelled["revision"]},
                    headers=headers,
                )
            ).status_code == 409
        with store.factory() as db:
            assert db.get(RecoveryTask, root).lease is None
        await store.stop()

    asyncio.run(run())


def test_unsupported_checkpoint_and_changed_saved_plan_fail_closed(recovery_app):
    app, store, _, _ = recovery_app

    async def run():
        store.start()
        plan, _ = await crashed_write(app)
        manager = app.state.checkpoint_manager
        with store.factory() as db:
            checkpoint = db.scalar(
                select(AgentCheckpoint).where(AgentCheckpoint.task_id == plan["plan_id"])
            )
            checkpoint.intent_json = "broken-json"
            db.commit()
        with pytest.raises(RecoveryError, match="CHECKPOINT_INVALID"):
            await app.state.recovery.preview(plan["plan_id"], plan["conversation_id"])
        assert not app.state.runtime_registry.active
        await store.stop()

    asyncio.run(run())


def test_interrupted_resume_can_resume_again_without_rewriting_or_duplicate_user(recovery_app):
    app, store, _, _ = recovery_app

    async def run():
        store.start()
        plan, written = await crashed_write(app)
        root, conversation, session = (
            plan["plan_id"],
            plan["conversation_id"],
            plan["work_session_id"],
        )
        manager, coordinator = app.state.checkpoint_manager, app.state.recovery
        original = app.state.task_runtime.run
        entered = asyncio.Event()

        async def hold(*args):
            entered.set()
            await asyncio.Event().wait()

        app.state.task_runtime.run = hold
        first = manager.detail(root, conversation)
        await coordinator.resume(
            root, conversation, session, "fixture", str(uuid4()), first["revision"], []
        )
        await asyncio.wait_for(entered.wait(), 3)
        await coordinator.close()
        assert manager.detail(root, conversation)["state"] == "INTERRUPTED"
        assert (
            not coordinator.tasks
            and not coordinator.leases
            and not app.state.runtime_registry.active
        )
        app.state.task_runtime.run = original
        app.state.recovery = ResumeCoordinator(app, manager)
        current = manager.detail(root, conversation)
        await app.state.recovery.resume(
            root, conversation, session, "fixture", str(uuid4()), current["revision"], []
        )
        await asyncio.gather(*list(app.state.recovery.tasks.values()))
        assert manager.detail(root, conversation)["state"] == "COMPLETED"
        assert len(written) == 1
        with store.factory() as db:
            assert (
                len(
                    list(
                        db.scalars(
                            select(Message).where(
                                Message.conversation_id == conversation, Message.role == "user"
                            )
                        )
                    )
                )
                == 1
            )
        await app.state.recovery.close()
        await store.stop()

    asyncio.run(run())


def test_inflight_readonly_checkpoint_reexecutes_only_under_fresh_resume_lease(recovery_app):
    app, store, _, _ = recovery_app

    async def run():
        store.start()
        plan, _ = await crashed_write(app, read_only=True)
        root, conversation, session = (
            plan["plan_id"],
            plan["conversation_id"],
            plan["work_session_id"],
        )
        manager, coordinator = app.state.checkpoint_manager, app.state.recovery
        detail = manager.detail(root, conversation)
        assert detail["checkpoints"][0]["step_state"] == "EXECUTING"
        preview = await coordinator.preview(root, conversation, session)
        assert preview["can_resume"] and preview["items"][0]["action"] == "EXECUTE"
        model_calls = app.state.llm_service.provider.calls
        await coordinator.resume(
            root, conversation, session, "fixture", str(uuid4()), detail["revision"], []
        )
        await asyncio.gather(*list(coordinator.tasks.values()))
        final = manager.detail(root, conversation)
        assert final["state"] == "COMPLETED"
        assert final["checkpoints"][0]["step_state"] == "COMMITTED"
        assert final["counters"]["total_steps"] == 2
        assert app.state.llm_service.provider.calls == model_calls
        assert not coordinator.leases and not app.state.runtime_registry.active
        await coordinator.close()
        await store.stop()

    asyncio.run(run())


def test_resume_storage_guard_retains_fixed_failure_code(recovery_app, monkeypatch):
    app, store, _, _ = recovery_app

    async def run():
        store.start()
        plan, _ = await crashed_write(app)
        root, conversation, session = (
            plan["plan_id"],
            plan["conversation_id"],
            plan["work_session_id"],
        )
        manager, coordinator = app.state.checkpoint_manager, app.state.recovery

        def fail(*args):
            raise RecoveryError("SIDE_EFFECT_UNCERTAIN")

        monkeypatch.setattr(manager, "reuse", fail)
        detail = manager.detail(root, conversation)
        await coordinator.resume(
            root, conversation, session, "fixture", str(uuid4()), detail["revision"], []
        )
        await asyncio.gather(*list(coordinator.tasks.values()))
        final = manager.detail(root, conversation)
        assert final["state"] == "NEEDS_REVIEW"
        assert final["failure"]["error_code"] == "SIDE_EFFECT_UNCERTAIN"
        assert not coordinator.leases and not app.state.runtime_registry.active
        assert await store.flush()
        traces = app.state.trace_repository.listing(conversation)["items"]
        resumed = next(row for row in traces if row["task_id"] == final["execution_id"])
        events = app.state.trace_repository.events(resumed["trace_id"], conversation)["items"]
        assert any(
            row["event_type"] == "RESUME_BLOCKED" and row["error_code"] == "SIDE_EFFECT_UNCERTAIN"
            for row in events
        )
        await coordinator.close()
        await store.stop()

    asyncio.run(run())
