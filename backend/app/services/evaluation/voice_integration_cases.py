"""Synthetic final-turn integration through original permission/runtime owners."""

import time
from uuid import uuid4

from app.services.agent_service import AgentContext
from app.services.agent_orchestration import execute
from app.services.evaluation.scheduler_support import SchedulerFixture
from app.services.permission_policy import PermissionPolicyEngine
from app.services.trusted_actions import action_for, risk_for


async def run(index, app, workspace, manager, scope, options):
    fixture = SchedulerFixture(app, workspace)
    await fixture.begin()
    try:
        definition = fixture.definition(owner=str(options.conversation_id))
        entry = fixture.scheduler.save(definition)
        tool = {
            "id": str(uuid4()),
            "name": "workflow.run",
            "arguments": {
                "workflow_id": definition.workflow_id,
                "version_id": definition.workflow_version_id,
                "inputs": {},
            },
            "risk_level": "high",
            "requires_confirmation": True,
        }
        if index == 33:
            return {
                "voice_confirmation_not_a_grant": PermissionPolicyEngine().decide(
                    action_for(tool), risk_for(action_for(tool)), task_allowed=True
                )
                == "ask"
            }
        if index == 35:
            before = manager.owned(scope).identity()
            await fixture.scheduler.tick()
            after = manager.owned(scope).identity()
            return {
                "scheduler_preserves_existing_turn": before == after,
                "scheduler_does_not_invoke_asr": manager.owned(scope).turn.provider.calls == 0,
                "no_synthetic_mic_autostart": not hasattr(fixture.scheduler, "open_microphone"),
            }
        await manager.finalize(scope)
        manager.claim(scope, str(options.conversation_id), options.character_id)

        async def run_tool(approved):
            owner = str(options.conversation_id)
            session = app.state.work_state.create("Synthetic voice workflow", owner, [tool], None)
            context = AgentContext(
                "assistant",
                owner,
                "Start fixed workflow",
                [],
                {},
                [tool],
                time.monotonic(),
                session,
                executor=app.state.agent_service.executor,
                renderer_id=str(options.owner_id),
                task_id=str(uuid4()),
            )
            app.state.task_runtime.prepare(context)
            return await app.state.task_runtime.run(context, approved)

        _, denied, _ = await run_tool(set())
        _, admitted, _ = await run_tool({tool["id"]})
        result = admitted[0]["result"]
        for _ in range(300):
            row = app.state.workflows.repository.run(result["run_id"], str(options.conversation_id))
            if row["state"] == "COMPLETED":
                break
            import asyncio

            await asyncio.sleep(0.01)
        context = type("Scope", (), {"conversation_id": str(options.conversation_id)})()
        paused = await execute(
            app,
            context,
            {
                "name": "scheduler.pause",
                "arguments": {"schedule_id": definition.schedule_id, "revision": entry["revision"]},
            },
        )
        return {
            "permission_before_dispatch": denied[0]["result"]["code"] == "PERMISSION_DENIED",
            "exact_workflow_completes": row["state"] == "COMPLETED",
            "queued_not_premature_success": result["completed"] is False,
            "pause_same_owner": paused["state"] == "DISABLED",
        }
    finally:
        await fixture.close()
