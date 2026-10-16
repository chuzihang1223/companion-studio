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

    registry = BenchmarkRegistry(payload.get("dataset_id", "studio-core"))
    case = registry.by_id[payload["case_id"]]
    if (
        case.case_id == "EVAL-007"
        and getattr(sys, "frozen", False)
        and not Path(os.environ.get("AGENT_PYTHON", "")).is_file()
    ):
        from app.services.evaluation.repository import empty_result

        return {
            "result": empty_result("BLOCKED", "PYTHON_RUNNER_UNAVAILABLE"),
            "links": {},
            "budget": {"requests": 0, "total_tokens": None},
        }
    home, data = settings.data_root.parent, settings.data_root
    workspace = home / "workspace"
    if not (case.case_id == "REC-012" and payload.get("restart_stage") == "reopen"):
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
    from app.services.recovery.checkpoints import CheckpointManager
    from app.services.recovery.coordinator import ResumeCoordinator

    state.checkpoint_manager = CheckpointManager()
    state.checkpoint_manager.recover()
    state.recovery_required = True
    state.recovery = ResumeCoordinator(app, state.checkpoint_manager)
    state.work_state.registry = state.runtime_registry
    state.task_runtime = AgentTaskRuntime(app)
    state.desktop_bridge = MockDesktopBridge(case.case_id)
    if case.case_id.startswith("REC-"):
        from app.services.evaluation.recovery_fixtures import RecoveryDesktopBridge

        state.desktop_bridge = RecoveryDesktopBridge(case.case_id)
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
    if case.case_id.startswith("REC-"):
        from app.services.evaluation.recovery_fixtures import RecoveryModel

        state.llm_service.provider = RecoveryModel(case.case_id)
    if case.case_id == "REC-012" and payload.get("restart_stage") == "reopen":
        state.work_state.recover_interrupted()
    store = TraceStore()
    if case.case_id == "REC-012" and payload.get("restart_stage") == "reopen":
        store.recover()
    store.on_commit = state.work_state.notify_trace
    state.trace_recorder = TraceRecorder(store)
    state.trace_repository = TraceRepository(store)
    state.work_state.trace_recorder = state.trace_recorder
    state.runtime_registry.trace_recorder = state.trace_recorder
    store.start()
    if case.case_id.startswith("REC-"):
        from app.services.evaluation.recovery_cases import run_case

        try:
            return await run_case(app, store, case, payload, workspace, GuardedExecutor)
        finally:
            await state.recovery.close()
            await state.runtime_registry.close()
            await store.stop()
            engine.dispose()
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
        if len(context.calls) > case.max_steps:
            evidence["step_limit_exceeded"] = True
            context.calls = context.calls[: case.max_steps]
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
            recorded = state.trace_repository.listing(
                plan["conversation_id"], task_id=plan["plan_id"]
            )["items"]
            if not recorded:
                # A missing projection is failed evidence, never a passed task or
                # an IndexError that discards the other measured observations.
                evidence["trace_missing"] = True
                continue
            trace = recorded[0]
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
            unauthorized_actions=sum(row["tools_executed"] for row in summaries)
            if case.case_id == "EVAL-018"
            else 0,
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
            modification_scope_matches=(
                {p.name for p in workspace.iterdir() if p.is_file()}
                == set(before) | {"calc.py.agent-backup"}
                and (workspace / "calc.py.agent-backup").is_file()
                and digest((workspace / "calc.py.agent-backup").read_text(encoding="utf-8"))
                == before["calc.py"]
                and all(
                    digest((workspace / name).read_text(encoding="utf-8")) == value
                    for name, value in before.items()
                    if name != "calc.py"
                )
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
        graded["trace_scopes"] = [
            {key: row[key] for key in ("trace_id", "task_id", "conversation_id", "work_session_id")}
            for row in traces
        ]
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
    import socket

    port = int(os.environ["COMPANION_EVAL_PORT"])
    nonce = os.environ.pop("COMPANION_EVAL_NONCE")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.settimeout(70)
        with connection.makefile("rwb") as channel:
            channel.write(nonce.encode() + b"\n")
            channel.flush()
            payload = json.loads(channel.readline(12000))
            try:
                result = asyncio.run(evaluate(payload))
            except Exception as exc:
                # Do not echo messages: providers/paths can contain secrets.
                result = {"error": "WORKER_ERROR", "error_type": type(exc).__name__}
            channel.write(json.dumps(result, ensure_ascii=True, allow_nan=False).encode() + b"\n")
            channel.flush()


if __name__ == "__main__":
    main()
