"""Original Workflow, tool permission, Trace, checkpoints and outbox integration."""

from app.services.evaluation.workflow_fixtures import linear, WorkflowMockModel
from app.services.workflows.contracts import WorkflowError
from uuid import uuid4
from app.services.scheduling.notifications import SchedulerNotifications


async def run(fx, index):
    state, scheduler, owner = fx.app.state, fx.scheduler, fx.owner
    if index == 26:
        state.llm_service.provider = WorkflowMockModel()
        graph = linear(
            "llm_agent", {"instruction": "Summarize synthetic input", "value": "fixture"}
        )
    elif index == 29:
        graph = linear(
            "tool", {"name": "computer.observe", "arguments": {"target_id": str(uuid4())}}
        )
    else:
        graph = linear("tool", {"name": "workspace.read", "arguments": {"path": "fixture.txt"}})
    definition = fx.definition(graph=graph)
    scheduler.save(definition)
    row = scheduler.run_now(definition.schedule_id, owner)
    await scheduler.tick()
    current = scheduler.store.occurrence(row["occurrence_id"], owner)
    if index == 26:
        scheduler.grant_once(row["occurrence_id"], owner)
        fx.clock.advance(301)
        await scheduler.tick()
        final = scheduler.store.occurrence(row["occurrence_id"], owner)
        return {
            "expired_consent": final["status"] == "WAITING_PERMISSION",
            "no_model_run": final["workflow_run_id"] is None,
        }
    if index == 29:
        return {
            "computer_requires_fresh_consent": current["status"] == "WAITING_PERMISSION",
            "not_observed_or_uploaded": not current["workflow_run_id"],
        }
    final = await fx.complete(current)
    run = state.workflows.repository.run(final["workflow_run_id"], owner)
    if index == 28:
        return {
            "original_workflow_completed": run["state"] == "COMPLETED",
            "original_tool_executed": run["tool_calls"] == 1,
            "fixture_read": run["output"]["finish"]["content"].startswith(
                "Public evaluation fixture"
            ),
            "scheduler_links_original": final["summary"]["task_id"] == run["task_id"],
        }
    outbox = SchedulerNotifications(scheduler.store)
    notifications = outbox.listing(owner)
    notification = next(n for n in notifications if n["occurrence_id"] == final["occurrence_id"])
    outbox.claim(notification["notification_id"])
    outbox.report(notification["notification_id"], "UNAVAILABLE")
    recovery_blocked = False
    try:
        await state.workflows.recovery.preview(run["run_id"], owner)
    except WorkflowError as error:
        recovery_blocked = error.code == "WORKFLOW_NOT_RESUMABLE"
    return {
        "state_consistent": final["status"] == run["state"] == "COMPLETED",
        "typed_verified": final["summary"]["verification_result"] is not None,
        "completed_not_resumable": recovery_blocked,
        "notification_exact_occurrence": notification["conversation_id"] == owner,
        "notification_not_faked": outbox.listing(owner)[0]["delivery"] == "UNAVAILABLE",
    }
