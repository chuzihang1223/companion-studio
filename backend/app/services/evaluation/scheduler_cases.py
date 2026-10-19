"""Scheduler adapter in the original isolated Evaluation worker, not a second runner."""

import time

from app.services.evaluation.scheduler_support import SchedulerFixture
from app.services.evaluation import scheduler_time_cases, scheduler_condition_cases
from app.services.evaluation import (
    scheduler_runtime_cases,
    scheduler_extension_cases,
    scheduler_integration_cases,
)


async def run_case(app, trace_store, case, payload, workspace):
    from app.services.evaluation.grader import EvaluationGrader

    fixture = SchedulerFixture(app, workspace)
    started = time.perf_counter()
    await fixture.begin()
    try:
        index = int(case.case_id[-3:])
        module = (
            scheduler_time_cases
            if index <= 12
            else scheduler_condition_cases
            if index <= 18
            else scheduler_runtime_cases
            if index <= 24
            else scheduler_extension_cases
            if index in {25, 27}
            else scheduler_integration_cases
        )
        checks = await module.run(fixture, index)
    finally:
        await fixture.close()
        if getattr(app.state, "mcp_connections", None):
            await app.state.mcp_connections.close()
    await trace_store.flush()
    traces = app.state.trace_repository.listing(fixture.owner)["items"]
    evidence = {
        "checks": checks,
        "condition_verified": bool(checks) and all(v is True for v in checks.values()),
        "boundary": "real_sqlite_original_runtime_fake_clock_synthetic_inputs",
        "real_ai_quality": "not_measured",
        "native_notification": "not_measured_use_windows_gui_gate",
        "trace_consistent": bool(traces)
        and all(t["conversation_id"] == fixture.owner for t in traces),
        "resources_released": not app.state.workflows.tasks
        and not app.state.workflows.approvals
        and not app.state.runtime_registry.active
        and not app.state.task_runtime.handles,
        "external_requests": 0,
        "duplicate_count": sum(not v for k, v in checks.items() if "duplicate" in k),
    }
    observation = {"evidence": evidence, "duration_ms": (time.perf_counter() - started) * 1000}
    result = EvaluationGrader().grade(case, observation, traces)
    result["trace_scopes"] = [
        {k: t[k] for k in ("trace_id", "task_id", "conversation_id", "work_session_id")}
        for t in traces
    ]
    return {
        "result": result,
        "links": traces[0] if traces else {},
        "budget": {"requests": 0, "total_tokens": None},
    }
