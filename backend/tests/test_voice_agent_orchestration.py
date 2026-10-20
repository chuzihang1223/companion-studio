"""Original Agent permissions/coordinators; synthetic workflow, no native apps."""

import asyncio
import json
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from scheduler_fixture import scheduler_host, scheduled
from workflow_fixture import settle

from app.services.agent_orchestration import execute, planning_context, validate_discovered_plan
from app.services.agent_service import AgentContext
from app.services.agent_tools import ToolRegistry
from app.services.permission_policy import PermissionPolicyEngine
from app.services.trusted_actions import action_for, risk_for
from app.services.workflows.contracts import Node, WorkflowError
from app.services.scheduling.contracts import ScheduleError
from app.core.config import settings
from app.services.character.loader import CharacterLoader
from app.services.llm.configuration import LLMConfig


def test_explicit_orchestration_queries_route_to_tools_without_forcing_casual_chat():
    from app.services.agent_router import ConversationRouter

    router = ConversationRouter()
    for text in ("查询定时任务", "查看工作流状态", "scheduler status"):
        assert router.route(text, "agent") == "agent"
        assert router.route(text, "auto") == "agent"
        assert router.route(text, "chat") == "chat"
    assert router.route("聊聊工作流", "agent") == "chat"


def call(name, **arguments):
    return ToolRegistry().parse_calls(
        {
            "tool_calls": [
                {
                    "id": str(uuid4()),
                    "function": {
                        "name": name.replace(".", "_"),
                        "arguments": json.dumps(arguments),
                    },
                }
            ]
        }
    )[0]


def test_voice_cannot_grant_high_risk_or_replay_unknown_side_effects():
    first = call("workflow.run", workflow_id=str(uuid4()), version_id=str(uuid4()), inputs={})
    second = {**first, "arguments": {**first["arguments"], "version_id": str(uuid4())}}
    assert first["requires_confirmation"]
    assert action_for(first) != action_for(second)
    assert risk_for(action_for(first)) == "high"
    assert PermissionPolicyEngine().decide(action_for(first), "high", task_allowed=True) == "ask"
    from app.services.recovery.policy import side_effect_class

    assert side_effect_class("workflow.run") == "NON_IDEMPOTENT"
    assert side_effect_class("scheduler.pause") == "NON_IDEMPOTENT"
    validate_discovered_plan([first])
    with pytest.raises(ValueError, match="仅允许编排工具"):
        validate_discovered_plan([call("app.launch", app="notepad")])
    assert {r["function"]["name"] for r in ToolRegistry().schemas_for("运行工作流读取文件")} == {
        "workflow_list",
        "workflow_run",
        "scheduler_list",
        "scheduler_status",
        "scheduler_pause",
    }


def test_original_runtime_queues_exact_workflow_only_after_approval_and_keeps_owner(tmp_path):
    async def scenario():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            entry = scheduler.save(definition)
            metadata = planning_context(app, owner, "查看工作流和定时任务")
            assert metadata["schedules"][0]["schedule_id"] == definition.schedule_id
            assert '"inputs"' not in json.dumps(metadata) and '"nodes"' not in json.dumps(metadata)
            assert planning_context(app, str(uuid4()), "查看定时任务")["schedules"] == []
            tool = call(
                "workflow.run",
                workflow_id=definition.workflow_id,
                version_id=definition.workflow_version_id,
                inputs={},
            )

            async def run(approved):
                session = app.state.work_state.create("固定工作流测试", owner, [tool], None)
                context = AgentContext(
                    "assistant",
                    owner,
                    "运行固定工作流",
                    [],
                    {},
                    [tool],
                    time.monotonic(),
                    session,
                    executor=app.state.agent_service.executor,
                    renderer_id=str(uuid4()),
                    task_id=str(uuid4()),
                )
                app.state.task_runtime.prepare(context)
                return await app.state.task_runtime.run(context, approved)

            _, denied, _ = await run(set())
            assert denied[0]["result"]["code"] == "PERMISSION_DENIED"
            assert not app.state.workflows.tasks
            _, admitted, _ = await run({tool["id"]})
            result = admitted[0]["result"]
            assert result["status"] == "queued" and result["completed"] is False
            final = await settle(app, owner, result["run_id"])
            assert final["state"] == "COMPLETED"
            context = SimpleNamespace(conversation_id=owner, renderer_id=str(uuid4()))
            listing = await execute(app, context, call("scheduler.list"))
            assert listing["items"] == [
                {
                    "schedule_id": definition.schedule_id,
                    "revision": entry["revision"],
                    "state": "ENABLED",
                }
            ]
            alien = SimpleNamespace(conversation_id=str(uuid4()))
            assert (await execute(app, alien, call("scheduler.list")))["items"] == []
            paused = await execute(
                app,
                context,
                call(
                    "scheduler.pause",
                    schedule_id=definition.schedule_id,
                    revision=entry["revision"],
                ),
            )
            assert paused["state"] == "DISABLED" and not paused["running_occurrences_cancelled"]
            with pytest.raises(ScheduleError):
                await execute(
                    app,
                    context,
                    call(
                        "scheduler.pause",
                        schedule_id=definition.schedule_id,
                        revision=entry["revision"],
                    ),
                )
            context.workflow_run_id = result["run_id"]
            assert (await execute(app, context, tool))["code"] == "ORCHESTRATION_NESTING_DENIED"
            with pytest.raises(WorkflowError):
                app.state.workflows.catalog.validate_node(
                    Node(
                        node_id="nested",
                        type="tool",
                        config={"name": "workflow.run", "arguments": tool["arguments"]},
                    )
                )

    asyncio.run(scenario())


def test_named_workflow_reaches_original_planner_and_rejects_injected_other_tools(tmp_path):
    async def scenario():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            app.state.characters = CharacterLoader(settings.project_root / "characters")
            app.state.characters.load()
            observed = []

            class Planner:
                config = LLMConfig(
                    model="synthetic-planner",
                    base_url="http://127.0.0.1:9/v1",
                    capabilities={"tool_calling": True},
                )
                malicious = False

                async def plan_tools(self, messages, tools):
                    observed.append((messages, tools))
                    selected = (
                        call("app.launch", app="notepad")
                        if self.malicious
                        else call(
                            "workflow.run",
                            workflow_id=definition.workflow_id,
                            version_id=definition.workflow_version_id,
                            inputs={},
                        )
                    )
                    return {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": selected["id"],
                                "function": {
                                    "name": selected["name"].replace(".", "_"),
                                    "arguments": json.dumps(selected["arguments"]),
                                },
                            }
                        ],
                    }

            planner = Planner()
            app.state.model_roles.configure({"planning_model": planner})
            result = await app.state.agent_service.plan(
                app, "assistant", owner, "请运行固定工作流", renderer_id=str(uuid4())
            )
            assert result["calls"][0]["name"] == "workflow.run"
            assert len(observed) == 1 and not app.state.workflows.tasks
            assert all(
                "ORCHESTRATION_METADATA" not in message["content"]
                for message in observed[0][0]
                if message["role"] == "system"
            )
            assert any(
                "ORCHESTRATION_METADATA" in message["content"]
                for message in observed[0][0]
                if message["role"] == "user"
            )
            planner.malicious = True
            with pytest.raises(ValueError, match="仅允许编排工具"):
                await app.state.agent_service.plan(
                    app, "assistant", owner, "请运行固定工作流", renderer_id=str(uuid4())
                )
            assert not app.state.workflows.tasks

    asyncio.run(scenario())
