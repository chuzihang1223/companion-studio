"""Real plan/execution ownership, expiry, cancellation and weak reachability."""

import asyncio
from dataclasses import replace
import gc
from types import SimpleNamespace
import weakref

import pytest

from app.services import runtime_registry
from app.services.desktop.lifecycle import TaskCancelled, TaskHandle
from test_agent_trace import runtime_app as runtime_app
from test_trace_persistence import trace_database as trace_database


async def plan(app):
    return await app.state.agent_service.plan(app, "assistant", None, "帮我写一个 Python 示例")


async def execute(app, row):
    return await app.state.agent_service.execute(
        app, row["plan_id"], {call["id"] for call in row["calls"]}, "natural", False, 1.0
    )


def test_RESOURCE_006_prepared_but_unexecuted_plan_releases_expired_handle(runtime_app):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        try:
            row = await plan(app)
            service, runtime = app.state.agent_service, app.state.task_runtime
            session = row["work_session_id"]
            reference = weakref.ref(runtime.handles[session])
            created = service.pending[row["plan_id"]].created
            assert service.expire_pending(app, created + 299) == 0
            assert reference() is runtime.handles[session]
            assert service.expire_pending(app, created + 300) == 1
            gc.collect()
            assert not service.pending and not runtime.handles and reference() is None
            # Only volatile state expires; the original permission plan survives.
            assert app.state.work_state.restore_plan(row["plan_id"])["work_session_id"] == session
        finally:
            await store.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["execute", "select"])
def test_RESOURCE_007_expired_request_is_rejected_and_releases_its_handle(runtime_app, operation):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        try:
            row = await plan(app)
            app.state.agent_service.pending[row["plan_id"]].created -= 301
            with pytest.raises(LookupError):
                if operation == "execute":
                    await execute(app, row)
                else:
                    await app.state.agent_service.select_application(app, row["plan_id"], "unused")
            assert not app.state.agent_service.pending and not app.state.task_runtime.handles
        finally:
            await store.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("restored", [False, True])
def test_RESOURCE_008_normal_and_restored_execution_clean_only_after_reply(runtime_app, restored):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        try:
            row = await plan(app)
            if restored:
                app.state.agent_service.pending.clear()
                app.state.task_runtime.handles.clear()
            result = await execute(app, row)
            assert result["content"] and result["tools"]
            assert not app.state.agent_service.pending
            assert not app.state.task_runtime.handles and not app.state.task_runtime._handle_users
            assert (
                not app.state.runtime_registry.active and not app.state.runtime_registry.by_session
            )
        finally:
            await store.stop()

    asyncio.run(scenario())


def test_RESOURCE_009_concurrent_execution_keeps_live_handle_and_cancellation(
    runtime_app, monkeypatch
):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        tasks = []
        try:
            a, b = await plan(app), await plan(app)
            service, runtime = app.state.agent_service, app.state.task_runtime
            stale = replace(service.pending[a["plan_id"]])
            started, release = asyncio.Event(), asyncio.Event()
            owners = set()

            async def blocked(_app, context, *_args, **_kwargs):
                owners.add(context.work_session_id)
                if len(owners) == 2:
                    started.set()
                await release.wait()
                return {"owner": context.work_session_id}

            monkeypatch.setattr(service, "_execute_context", blocked)
            tasks = [asyncio.create_task(execute(app, row)) for row in (a, b)]
            await asyncio.wait_for(started.wait(), 5)
            stale.created -= 301
            service.pending["stale-alias"] = stale
            assert service.expire_pending(app) == 1
            assert runtime.handles[a["work_session_id"]] is stale.prepared_handle
            assert len(runtime.handles) == len(runtime._handle_users) == 2
            runtime.control(a["work_session_id"], "cancel")
            first = await asyncio.gather(tasks[0], return_exceptions=True)
            assert isinstance(first[0], asyncio.CancelledError)
            assert b["work_session_id"] in runtime.handles
            release.set()
            assert (await tasks[1])["owner"] == b["work_session_id"]
            assert not runtime.handles and not runtime._handle_users and not runtime.executions
            assert not app.state.runtime_registry.active
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await store.stop()

    asyncio.run(scenario())


def test_RESOURCE_010_repeated_abandoned_plans_have_no_retained_handle_references(runtime_app):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        try:
            service, runtime = app.state.agent_service, app.state.task_runtime
            references = []
            for _ in range(30):
                row = await plan(app)
                references.append(weakref.ref(runtime.handles[row["work_session_id"]]))
                created = service.pending[row["plan_id"]].created
                assert service.expire_pending(app, created + 301) == 1
                assert not service.pending and not runtime.handles and not runtime._handle_users
            gc.collect()
            assert all(reference() is None for reference in references)
        finally:
            await store.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_RESOURCE_012_trace_admission_failure_or_cancellation_releases_reservation(
    runtime_app, monkeypatch, cancel
):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        task = None
        try:
            row = await plan(app)
            service, runtime = app.state.agent_service, app.state.task_runtime
            stale = replace(service.pending[row["plan_id"]], created=0)
            entered, release = asyncio.Event(), asyncio.Event()

            async def fail(*_args):
                entered.set()
                await release.wait()
                raise RuntimeError("synthetic trace admission failure")

            monkeypatch.setattr(app.state.trace_recorder, "execution_scope", fail)
            task = asyncio.create_task(execute(app, row))
            await asyncio.wait_for(entered.wait(), 5)
            assert row["work_session_id"] not in app.state.runtime_registry.by_session
            service.pending["stale"] = stale
            assert service.expire_pending(app) == 1
            assert runtime.handles[row["work_session_id"]] is stale.prepared_handle
            if cancel:
                task.cancel()
            else:
                release.set()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(outcome, asyncio.CancelledError if cancel else RuntimeError)
            assert not runtime.handles and not runtime._handle_users and not service.pending
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await store.stop()

    asyncio.run(scenario())


def test_RESOURCE_013_watchdog_calls_cleanup_and_survives_callback_failure(
    runtime_app, monkeypatch
):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        try:
            row = await plan(app)
            service = app.state.agent_service
            service.pending[row["plan_id"]].created -= 301
            ticks = 0

            async def tick(delay):
                nonlocal ticks
                assert delay == 15
                ticks += 1
                if ticks == 3:
                    raise asyncio.CancelledError()

            def cleanup():
                if ticks == 1:
                    raise RuntimeError("synthetic failed cleanup tick")
                service.expire_pending(app)

            monkeypatch.setattr(runtime_registry, "asyncio", SimpleNamespace(sleep=tick))
            with pytest.raises(asyncio.CancelledError):
                await app.state.runtime_registry.watchdog(cleanup)
            assert ticks == 3 and not service.pending and not app.state.task_runtime.handles
        finally:
            await store.stop()

    asyncio.run(scenario())


def test_RESOURCE_014_expiry_never_removes_a_replacement_handle(runtime_app):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        try:
            row = await plan(app)
            service, runtime = app.state.agent_service, app.state.task_runtime
            reference = weakref.ref(runtime.handles[row["work_session_id"]])
            replacement = TaskHandle()
            runtime.handles[row["work_session_id"]] = replacement
            created = service.pending[row["plan_id"]].created
            assert service.expire_pending(app, created + 301) == 1
            gc.collect()
            assert reference() is None and runtime.handles[row["work_session_id"]] is replacement
            assert runtime.discard_prepared(row["work_session_id"], replacement)
        finally:
            await store.stop()

    asyncio.run(scenario())


def test_RESOURCE_015_cancelled_runtime_preserves_other_same_session_references(runtime_app):
    app, store, _, _ = runtime_app

    async def scenario():
        store.start()
        try:
            row = await plan(app)
            runtime = app.state.task_runtime
            context = app.state.agent_service.pending.pop(row["plan_id"])
            context.task_id = row["plan_id"]
            with runtime.handle_scope(row["work_session_id"]) as first:
                with runtime.handle_scope(row["work_session_id"]) as second:
                    assert first is second
                    first.control("cancel")
                    with pytest.raises(TaskCancelled):
                        await runtime.run(context, set())
                    assert runtime.handles[row["work_session_id"]] is first
                    with runtime.handle_scope(row["work_session_id"]) as third:
                        assert third is first and third.cancelled
                    assert runtime._handle_users[row["work_session_id"]] == 2
                assert runtime.handles[row["work_session_id"]] is first
            assert not runtime.handles and not runtime._handle_users and not runtime.executions
        finally:
            await store.stop()

    asyncio.run(scenario())
