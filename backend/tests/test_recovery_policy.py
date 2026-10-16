"""Safety policy contracts use synthetic IDs and never execute desktop actions."""

import asyncio
from uuid import uuid4

import pytest

from app.services.recovery.classifier import FailureClassifier
from app.services.recovery.planner import RecoveryPlanner
from app.services.recovery.policy import RecoveryPolicyEngine, side_effect_class
from app.services.recovery.types import FailureCode, MESSAGES, RecoveryBudget, TRANSITIONS


def failure(error=None, result=None, tool="code.read_file"):
    return FailureClassifier().classify(
        error, result=result, tool_name=tool, task_id=uuid4(), step_id=uuid4()
    )


@pytest.mark.parametrize("code", list(FailureCode))
def test_all_failure_codes_are_structured_and_do_not_echo_private_text(code):
    value = failure(RuntimeError("password=private https://private.invalid " + code))
    assert value.error_code == code
    assert value.message == MESSAGES[code]
    assert "private" not in value.model_dump_json()


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError(), "TOOL_TIMEOUT"),
        (ValueError(), "TOOL_ARGUMENT_INVALID"),
        (asyncio.CancelledError(), "TASK_CANCELLED"),
        (RuntimeError("LOCATOR_MISSING"), "ELEMENT_NOT_FOUND"),
    ],
)
def test_known_boundary_errors(error, code):
    assert failure(error).error_code == code


def test_policy_never_replays_side_effects_or_model_requests():
    policy, budget = RecoveryPolicyEngine(), RecoveryBudget()
    for name in (
        "code.edit_file",
        "code.create_file",
        "code.run",
        "coding.task",
        "app.launch",
        "app.close",
        "media.next",
    ):
        decision = policy.decide(failure(TimeoutError(), tool=name), name, budget=budget)
        assert not decision.retry and decision.approval
    for code in ("PERMISSION_DENIED", "MODEL_TIMEOUT", "MODEL_UNAVAILABLE", "TASK_CANCELLED"):
        assert policy.decide(failure(result={"code": code}), "code.read_file", budget=budget).stop


def test_read_retry_budget_and_unknown_failures_require_review():
    policy, budget = RecoveryPolicyEngine(), RecoveryBudget()
    assert policy.decide(failure(TimeoutError()), "code.read_file", budget=budget).retry
    assert not policy.decide(
        failure(TimeoutError()), "code.read_file", retry_count=2, budget=budget
    ).retry
    assert not policy.decide(
        failure(TimeoutError()), "code.read_file", recovery_count=4, budget=budget
    ).retry
    assert policy.decide(failure(RuntimeError("unknown")), "code.read_file", budget=budget).approval
    assert side_effect_class("code.run") == "NON_IDEMPOTENT"
    assert side_effect_class("workspace.write") == "RECONCILABLE"
    assert side_effect_class("browser.observe") == "READ_ONLY"
    assert all(
        "COMPLETED" not in targets
        for state, targets in TRANSITIONS.items()
        if state not in {"RESUMING", "RECOVERING"}
    )


def test_replanning_requires_new_evidence_and_honors_time_cycles_and_permissions():
    call = {"id": "focus-test", "name": "app.focus", "arguments": {"app": "notepad"}}
    error = failure(result={"code": "VERIFICATION_FAILED"}, tool="app.focus")
    planner = RecoveryPlanner()
    observation = {"running": True, "has_window": True, "verified": True}
    assert planner.plan(error, call, retry_count=0, observation=observation).retry
    repeated = planner.plan(error, call, retry_count=1, observation=observation)
    assert not repeated.retry and repeated.strategy == "unchanged_observation"
    planner = RecoveryPlanner(cycles=4)
    assert not planner.plan(error, call, retry_count=0, observation=observation).retry
    planner = RecoveryPlanner(started=-1801)
    assert planner.plan(error, call, retry_count=0, observation=observation).stop
    planner = RecoveryPlanner()
    assert planner.plan(error, call, retry_count=0, permission=False).stop
    assert planner.cycles == 0
