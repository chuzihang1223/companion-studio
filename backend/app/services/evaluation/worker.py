"""独立进程入口。环境由父进程在 import 配置/数据库之前确定。

每次 Attempt 只加载内置合成用例和空白档案，真实 AgentService / Runtime /
Registry / permissions / Trace 完整运行。Mock 仅替换模型和桌面边界。
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4


class GuardedExecutor:
    """保留生产执行器语义，另限制评测可操作的固定 Fixture 和测试脚本。"""

    def __init__(self, executor, case):
        self.inner, self.case = executor, case

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def execute(self, call):
        from app.services.evaluation.fixtures import CORRECT_CODE, MARKDOWN

        name, args = call["name"], call["arguments"]
        path = args.get("path", "")
        if name not in self.case.allowed_tools or path not in self.case.allowed_paths:
            if not (self.case.case_id == "EVAL-008" and path == "../outside.txt"):
                return {
                    "verified": False,
                    "error": "fixture scope denied",
                    "code": "EVALUATION_SCOPE_DENIED",
                }
        if "content" in args and args["content"] not in {CORRECT_CODE, MARKDOWN}:
            return {"verified": False, "error": "fixture content denied"}
        return self.inner.execute(call)

    def run_script(self, relative, *args):
        from app.services.evaluation.fixtures import TEST_CODE

        target = self.inner._path(relative)
        if (
            self.case.case_id != "EVAL-007"
            or relative != "check_calc.py"
            or target.read_text(encoding="utf-8") != TEST_CODE
        ):
            return {"exit_code": None, "verified": False, "error": "fixture executable denied"}
        return self.inner.run_script(relative, *args)


async def evaluate(payload):
    # These imports must remain inside the worker: parent SessionLocal is never
    # swapped or patched while a real conversation is running.
    from fastapi import FastAPI
    from app.core.config import settings
    from app.database.session import engine, SessionLocal
    from app.database.migrations import migrate
    from app.models import Conversation
    from app.services.agent_service import AgentService
    from app.services.agent_router import ConversationRouter
    from app.services.character.loader import CharacterLoader
    from app.services.desktop.runtime import AgentTaskRuntime
    from app.services.runtime_registry import RuntimeTaskRegistry
    from app.services.work_state import WorkState
    from app.services.llm.configuration import LLMConfig
    from app.services.llm.model_roles import ModelRoleRouter
    from app.services.llm.service import LLMService, create_llm_service
    from app.services.trace.recorder import TraceRecorder
    from app.services.trace.repository import TraceRepository
    from app.services.trace.store import TraceStore
    from app.services.evaluation.registry import BenchmarkRegistry
    from app.services.evaluation.fixtures import (
        CORRECT_CODE,
        FIXTURE_TEXT,
        MARKDOWN,
        MockDesktopBridge,
        MockModel,
        digest,
        initialize,
    )
    from app.services.evaluation.grader import EvaluationGrader
    from app.services.llm.usage import UsageBudget, usage_scope

    registry = BenchmarkRegistry()
    case = registry.by_id[payload["case_id"]]
    home, data = settings.data_root.parent, settings.data_root
    workspace = home / "workspace"
    initialize(data, workspace)
    before = {
        p.name: digest(p.read_text(encoding="utf-8")) for p in workspace.iterdir() if p.is_file()
    }
    migrate(engine)
    app = FastAPI()
    state = app.state
    state.characters = CharacterLoader(settings.project_root / "characters")
    state.characters.load()
    state.agent_service = AgentService()
    state.work_state = WorkState()
    state.runtime_registry = RuntimeTaskRegistry()
    state.work_state.registry = state.runtime_registry
    state.task_runtime = AgentTaskRuntime(app)
    state.desktop_bridge = MockDesktopBridge(case.case_id)
    state.llm_service = (
        create_llm_service(LLMConfig.model_validate(payload["model_config"]))
        if payload["mode"] == "live_model"
        else LLMService(
            MockModel(case.case_id),
            LLMConfig(
                base_url="http://127.0.0.1:9",
                model="fixture-mock",
                capabilities={"tool_calling": True},
            ),
        )
    )
    state.model_roles = ModelRoleRouter(app)
    store = TraceStore()
    store.on_commit = state.work_state.notify_trace
    state.trace_recorder = TraceRecorder(store)
    state.trace_repository = TraceRepository(store)
    state.work_state.trace_recorder = state.trace_recorder
    state.runtime_registry.trace_recorder = state.trace_recorder
    store.start()
    plans, replies = [], []
    cancelled = False
    started = time.perf_counter()
    usage = UsageBudget(
        payload.get("max_requests", 0),
        payload.get("token_budget", 0),
        payload.get("request_tokens", 2048),
    )
    evidence = {
        "route": ConversationRouter().route(case.input),
        "unauthorized_actions": 0,
        "boundary": "simulated_desktop" if payload["mode"] == "mock" else "real_sandbox_files",
    }

    async def task():
        with SessionLocal() as db:
            conversation = Conversation(
                character_id="assistant", conversation_type="work", preferred_mode="work"
            )
            db.add(conversation)
            db.commit()
            conversation_id = conversation.id
        session_id = state.work_state.ensure(conversation_id)
        state.work_state.set_workspace(session_id, str(workspace))
        if case.case_id == "EVAL-001":
            context = state.trace_recorder.create(str(uuid4()), conversation_id)
            context.bind(conversation_id, session_id)
            with state.trace_recorder._execution(context):
                await state.llm_service.chat([{"role": "user", "content": case.input}])
            plans.append(
                {
                    "plan_id": context.task_id,
                    "conversation_id": conversation_id,
                    "work_session_id": session_id,
                    "calls": [],
                }
            )
            return
        plan = await state.agent_service.plan(app, "assistant", conversation_id, case.input)
        plans.append(plan)
        context = state.agent_service.pending[plan["plan_id"]]
        context.executor = GuardedExecutor(context.executor, case)
        if len(context.calls) > case.max_steps or any(
            call["name"] not in case.allowed_tools for call in context.calls
        ):
            # Still execute the real permission-denial path, never grant a model's
            # unexpected action. It will fail tool correctness during grading.
            approved = set()
        else:
            approved = {call["id"] for call in context.calls if case.case_id != "EVAL-018"}
        run = asyncio.create_task(
            state.agent_service.execute(app, plan["plan_id"], approved, "natural", False, 1.0)
        )
        if case.case_id == "EVAL-019":
            await asyncio.wait_for(state.desktop_bridge.started.wait(), 3)
            state.task_runtime.control(plan["work_session_id"], "cancel")
        try:
            replies.append(await run)
        except asyncio.CancelledError:
            nonlocal cancelled
            cancelled = True

    try:
        with usage_scope(usage if payload["mode"] == "live_model" else None):
            if case.case_id == "EVAL-020":
                await asyncio.gather(task(), task())
            else:
                await task()
        duration = (time.perf_counter() - started) * 1000
        await state.runtime_registry.close()
        await store.flush()
        traces, summaries, events = [], [], []
        for plan in plans:
            trace = state.trace_repository.listing(
                plan["conversation_id"], task_id=plan["plan_id"]
            )["items"][0]
            traces.append(trace)
            summaries.append(
                state.trace_repository.summary(trace["trace_id"], plan["conversation_id"])
            )
            events.extend(
                state.trace_repository.events(
                    trace["trace_id"], plan["conversation_id"], limit=250
                )["items"]
            )
        details = [detail for reply in replies for detail in reply.get("tools", [])]
        results = [detail["result"] for detail in details]
        selected = [call for plan in plans for call in plan["calls"]]
        first = results[0] if results else {}
        evidence.update(
            desktop_calls=len(state.desktop_bridge.calls),
            tool_executions=sum(row["tools_executed"] for row in summaries),
            agent_verified=all(reply.get("goal_verified") is True for reply in replies)
            if replies
            else False,
            resources_released=not state.runtime_registry.active
            and not state.desktop_bridge.waiting,
            planned_app=selected[0]["arguments"].get("app") if selected else None,
            safe_plan=not selected and bool(plans[0].get("local_resolution")),
            candidate_count=len((plans[0].get("local_resolution") or {}).get("candidates", [])),
            read_hash_matches=digest(first.get("content", "")) == digest(FIXTURE_TEXT),
            read_path_matches=bool(
                selected and selected[0]["arguments"].get("path") == "fixture.txt"
            ),
            actual_file_exists=(workspace / "result.md").is_file(),
            created_hash_matches=(workspace / "result.md").is_file()
            and digest((workspace / "result.md").read_text(encoding="utf-8")) == digest(MARKDOWN),
            code_hash_matches=digest((workspace / "calc.py").read_text(encoding="utf-8"))
            == digest(CORRECT_CODE),
            exit_code=next((r["exit_code"] for r in results if "exit_code" in r), None),
            modification_scope_matches=all(
                digest((workspace / name).read_text(encoding="utf-8")) == value
                for name, value in before.items()
                if name != "calc.py"
            ),
            outside_access_blocked=bool(first.get("error")) and "content" not in first,
            outside_unchanged=(workspace.parent / "outside.txt").read_text(encoding="utf-8")
            == "OUTSIDE\n",
            protected_unchanged=(workspace / "protected.txt").read_text(encoding="utf-8")
            == "KEEP\n",
            permission_denied=any(row.get("error_code") == "PERMISSION_DENIED" for row in events),
            timeout_classified=any(row.get("error_code") == "TIMEOUT" for row in events),
            task_cancelled=cancelled,
            trace_cancelled=any(row["event_type"] == "TASK_CANCELLED" for row in events),
            page_loaded=first.get("loaded") is True,
            page_identity_matches=first.get("url") == "https://fixture.invalid/bench",
            dom_keyword_matches="orchid" in first.get("records", []),
            query_matches=first.get("query") == "orchid",
            locator_missing=first.get("code") == "LOCATOR_MISSING",
            application_identity_matches=first.get("app") == "notepad",
            installed=first.get("installed") is True,
            launch_requested=first.get("action") == "launch_requested",
            window_ready=bool(first.get("window")),
            app_exited=first.get("code") == "APP_EXITED_BEFORE_READY",
            session_count=len({plan["work_session_id"] for plan in plans}),
            ownership_matches=all(
                row["conversation_id"] == trace["conversation_id"]
                and row["task_id"] == trace["task_id"]
                for trace in traces
                for row in events
                if row["trace_id"] == trace["trace_id"]
            )
            and len({trace["conversation_id"] for trace in traces}) == len(traces),
        )
        observation = {
            "evidence": evidence,
            "selected_tools": [call["name"] for call in selected],
            "duration_ms": duration,
            "summary": {
                key: sum(row.get(key, 0) for row in summaries)
                for key in ("tools_executed", "model_requests", "retries")
            },
            "model_usage": usage.public()
            if payload["mode"] == "live_model"
            else {"status": "Not applicable (simulated)"},
        }
        graded = EvaluationGrader().grade(case, observation, traces)
        return {"result": graded, "links": traces[0] if traces else {}, "budget": usage.public()}
    finally:
        await state.runtime_registry.close()
        await store.stop()
        # File fixtures and any subprocess outputs are discarded by the parent;
        # only bounded read-only Trace DB evidence remains for Replay.
        engine.dispose()


def main():
    if os.environ.get("COMPANION_EVALUATION_WORKER") != "1":
        raise RuntimeError("evaluation worker requires an isolated launcher")
    payload = json.loads(sys.stdin.buffer.read(12000))
    try:
        result = asyncio.run(evaluate(payload))
    except Exception as exc:
        # Do not echo exception messages: providers and paths can contain secrets.
        result = {"error": "WORKER_ERROR", "error_type": type(exc).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
