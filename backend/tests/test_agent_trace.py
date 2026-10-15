"""Real planner/runtime/registry/WorkState with local model and tool doubles."""

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.config import settings
from app.database.migrations import migrate
from app.models import Message, WorkEvent
from app.services import agent_service, runtime_registry, work_state
from app.services.agent_tools import ToolExecutor
from app.services.character.loader import CharacterLoader
from app.services.coding import documents
from app.services.desktop.runtime import AgentTaskRuntime
from app.services.desktop.bridge import DesktopBridge
from app.services.trusted_actions import TrustedActionStore
from app.services.llm.configuration import LLMConfig
from app.services.llm.model_roles import ModelRoleRouter
from app.services.llm.service import LLMService
from app.services.trace.recorder import TraceRecorder
from app.services.trace.repository import TraceRepository
from app.services.trace.scope import current_trace
from app.services.trace.store import TraceStore
from test_trace_persistence import trace_database as trace_database


class Model:
    calls = 0
    tool = "code_create_file"

    async def plan_tools(self, messages, tools):
        self.calls += 1
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "local-call",
                    "type": "function",
                    "function": {
                        "name": self.tool,
                        "arguments": json.dumps(
                            {"path": "demo.py", "content": "private source do not trace"}
                            if self.tool == "code_create_file"
                            else {"app": "notepad"}
                        ),
                    },
                }
            ],
        }

    async def chat(self, messages):
        self.calls += 1
        return "本机模拟任务已完成。"


@pytest.fixture
def runtime_app(trace_database, monkeypatch, tmp_path):
    factory, conversation, session = trace_database
    for module in [agent_service, work_state, runtime_registry, documents]:
        monkeypatch.setattr(module, "SessionLocal", factory)
    from app.services.desktop import runtime

    permissions = TrustedActionStore(tmp_path / "permissions.json")
    monkeypatch.setattr(agent_service, "trusted_actions", permissions)
    monkeypatch.setattr(runtime, "trusted_actions", permissions)
    app = FastAPI()
    store = TraceStore(factory)
    app.state.trace_recorder = TraceRecorder(store)
    app.state.trace_repository = TraceRepository(store)
    app.state.work_state = work_state.WorkState()
    app.state.work_state.trace_recorder = app.state.trace_recorder
    app.state.runtime_registry = runtime_registry.RuntimeTaskRegistry()
    app.state.runtime_registry.trace_recorder = app.state.trace_recorder
    app.state.work_state.registry = app.state.runtime_registry
    store.on_commit = app.state.work_state.notify_trace
    app.state.agent_service = agent_service.AgentService()
    app.state.agent_service.executor = ToolExecutor(tmp_path / "workspace")
    app.state.task_runtime = AgentTaskRuntime(app)
    app.state.desktop_bridge = DesktopBridge()
    app.state.characters = CharacterLoader(settings.project_root / "characters")
    app.state.characters.load()
    app.state.llm_service = LLMService(
        Model(),
        LLMConfig(
            base_url="http://127.0.0.1:9", model="local-mock", capabilities={"tool_calling": True}
        ),
    )
    app.state.model_roles = ModelRoleRouter(app)
    return app, store, conversation, session


async def execute(app, approved=True):
    plan = await app.state.agent_service.plan(app, "assistant", None, "帮我写一个 Python 示例")
    result = await app.state.agent_service.execute(
        app,
        plan["plan_id"],
        {row["id"] for row in plan["calls"]} if approved else set(),
        "natural",
        False,
        1.0,
    )
    return plan, result


@pytest.mark.parametrize("approved", [True, False])
def test_real_agent_trace_and_refused_permission(runtime_app, approved):
    app, store, _, _ = runtime_app
    app.state.llm_service.provider.tool = "app_close"

    async def local_desktop(*args, **kwargs):
        return {"verified": True}

    app.state.desktop_bridge.request = local_desktop

    async def run():
        store.start()
        plan, _ = await execute(app, approved)
        assert await store.flush()
        repo = app.state.trace_repository
        trace = repo.listing(plan["conversation_id"])["items"][0]
        assert trace["task_id"] == plan["plan_id"]
        assert trace["work_session_id"] == plan["work_session_id"]
        assert trace["status"] == ("completed" if approved else "failed")
        rows = repo.events(trace["trace_id"], plan["conversation_id"])["items"]
        names = [row["event_type"] for row in rows]
        assert {
            "TASK_CREATED",
            "TASK_PLANNING",
            "MODEL_REQUEST_COMPLETED",
            "VERIFICATION_STARTED",
            "PERMISSION_REQUESTED",
            "PERMISSION_RESOLVED",
        } <= set(names)
        assert ("TOOL_COMPLETED" if approved else "TOOL_FAILED") in names
        totals = repo.summary(trace["trace_id"], plan["conversation_id"])
        assert totals["tool_requests"] == 1 and totals["tools_executed"] == int(approved)
        assert totals["permission_wait_ms"] >= 0 and totals["model_requests"] >= 1
        assert totals["verification_passes" if approved else "verification_failures"] == 1
        assert all(
            row["span"]["parent_span_id"]
            for row in rows
            if row["span"] and row["span"]["kind"] != "task"
        )
        assert any(row["source_work_event_id"] for row in rows)
        assert "private source" not in json.dumps(rows) and "帮我写" not in json.dumps(rows)
        before = app.state.llm_service.provider.calls
        # Repeated history/replay reads have no model or tool side effects.
        for _ in range(5):
            repo.events(trace["trace_id"], plan["conversation_id"])
        assert app.state.llm_service.provider.calls == before
        await store.stop()
        assert current_trace.get() is None

    asyncio.run(run())


def test_concurrent_contexts_and_failed_writer_do_not_break_agent(runtime_app, monkeypatch):
    app, store, _, _ = runtime_app

    async def run():
        store.start()
        plans = await asyncio.gather(*(execute(app) for _ in range(3)))
        assert await store.flush()
        for plan, _ in plans:
            row = app.state.trace_repository.listing(plan["conversation_id"])["items"][0]
            events = app.state.trace_repository.events(row["trace_id"], plan["conversation_id"])[
                "items"
            ]
            assert row["status"] == "completed"
            assert all(event["task_id"] == plan["plan_id"] for event in events)
            assert all(event["conversation_id"] == plan["conversation_id"] for event in events)
        monkeypatch.setattr(
            store, "_write", lambda batch: (_ for _ in ()).throw(OSError("private path"))
        )
        _, reply = await execute(app)
        assert reply["content"] and await store.flush()
        assert store.metrics["write_failures"] > 0
        await store.stop()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "retry", "failure"])
def test_runtime_step_outcomes(runtime_app, monkeypatch, outcome):
    app, store, _, _ = runtime_app
    original = app.state.task_runtime.execute_call
    count = 0

    async def controlled(context, call, handle):
        async def operation():
            nonlocal count
            count += 1
            if outcome == "timeout":
                raise TimeoutError("private endpoint")
            if outcome == "cancel":
                raise asyncio.CancelledError()
            if outcome == "failure" or outcome == "retry" and count == 1:
                return {"verified": False, "error": "private failure"}
            return {"verified": True}

        app.state.work_state.emit(context.work_session_id, "tool_started", {"name": call["name"]})
        return await app.state.task_runtime.step(context, "运行测试", operation, handle)

    monkeypatch.setattr(app.state.task_runtime, "execute_call", controlled)

    async def run():
        store.start()
        if outcome == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await execute(app)
        else:
            await execute(app)
        assert await store.flush()
        with store.factory() as db:
            from app.models.agent_trace import AgentTrace

            trace = db.scalar(select(AgentTrace))
            trace_id, conversation = trace.id, trace.conversation_id
        rows = app.state.trace_repository.events(trace_id, conversation)["items"]
        names = {row["event_type"] for row in rows}
        assert (
            "TASK_CANCELLED"
            if outcome == "cancel"
            else "TASK_COMPLETED"
            if outcome == "retry"
            else "TASK_FAILED"
        ) in names
        if outcome == "retry":
            assert "RETRY_STARTED" in names and count == 2
            totals = app.state.trace_repository.summary(trace_id, conversation)
            assert 200 <= totals["recovery_ms"] < totals["task_total_ms"]
        if outcome == "timeout":
            assert any(row["error_code"] == "TIMEOUT" for row in rows)
        assert not app.state.runtime_registry.active
        await store.stop()

    asyncio.run(run())
    assert original


def test_additive_migration_preserves_old_work_and_messages(trace_database):
    factory, conversation, session = trace_database
    with factory() as db:
        db.add(
            Message(
                id=str(uuid4()),
                character_id="assistant",
                conversation_id=conversation,
                role="user",
                content="old message",
            )
        )
        db.add(WorkEvent(session_id=session, type="workflow_updated", detail_json="{}"))
        db.commit()
    migrate(factory.kw["bind"])
    migrate(factory.kw["bind"])
    with factory() as db:
        assert db.scalar(select(Message)).content == "old message"
        assert db.scalar(select(WorkEvent)).type == "workflow_updated"


def test_pause_resume_restart_and_stream_model_count(runtime_app):
    app, store, conversation, session = runtime_app

    async def run():
        store.start()
        context = app.state.trace_recorder.create(str(uuid4()), conversation)
        context.bind(conversation, session)
        with app.state.trace_recorder._execution(context):
            app.state.work_state.emit(session, "task_pause", {}, status="paused")
            await asyncio.sleep(0.02)
            app.state.work_state.emit(session, "task_resume", {}, status="running")
            app.state.llm_service.config.capabilities["streaming"] = False
            assert [part async for part in app.state.llm_service.stream_chat([])]
        assert await store.flush()
        rows = app.state.trace_repository.events(context.trace_id, conversation)["items"]
        assert {"TASK_PAUSED", "TASK_RESUMED"} <= {row["event_type"] for row in rows}
        totals = app.state.trace_repository.summary(context.trace_id, conversation)
        assert totals["model_requests"] == 1 and totals["recovery_ms"] > 0
        unfinished = app.state.trace_recorder.create(str(uuid4()), conversation)
        unfinished.bind(conversation, session)
        unfinished.send("state", status="running")
        assert await store.flush()
        await store.stop()
        restored = TraceStore(store.factory)
        restored.recover()
        repository = TraceRepository(restored)
        assert repository.detail(context.trace_id, conversation)["status"] == "completed"
        row = repository.detail(unfinished.trace_id, conversation)
        assert row["status"] == "interrupted" and row["incomplete"]
        assert row["duration_ms"] is None

    asyncio.run(run())
