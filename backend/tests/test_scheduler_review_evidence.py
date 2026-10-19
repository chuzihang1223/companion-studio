"""Shared real checks can serve several gates, but stale/failed evidence never can."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def scheduler_qa(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    module = importlib.import_module("visual_review_evidence")
    validation = importlib.import_module("scheduler_review_validation")
    review = module.Evidence()
    review.source, review.root = tmp_path, tmp_path / ".cache"
    review.root.mkdir()
    review.path = review.root / "review.json"
    review.required_gates = validation.GATES
    review.shared_gate_evidence = True
    review.identity = {"source_revision": "fixture", "build_id": "fixture", "dirty": False}
    review.current_artifact_key = lambda: "owned-artifact"
    labels = set().union(*validation.GATES.values())
    review.report = {
        "steps": [
            {
                "gate_id": "B",
                "label": label,
                "status": "PASS",
                "identity": dict(review.identity),
                "artifact_key": "owned-artifact",
            }
            for label in sorted(labels)
        ],
        "limitations": [],
    }
    return review, validation


def test_all_ten_gates_require_current_actual_labels(scheduler_qa):
    review, _ = scheduler_qa
    review.save()
    assert len(review.report["gates"]) == 10
    assert review.report["verdict"] == "READY_FOR_RELEASE_REVIEW"
    review.report["steps"] = [
        r for r in review.report["steps"] if r["label"] != "installed-scheduler"
    ]
    review.save()
    assert {g["gate_id"] for g in review.report["gates"] if g["status"] != "PASS"} == {"D", "G"}


@pytest.mark.parametrize("status", ["FAIL", "NOT_RUN", "BLOCKED"])
def test_newer_failure_is_retained_for_all_dependent_gates(scheduler_qa, status):
    review, _ = scheduler_qa
    old = next(r for r in review.report["steps"] if r["label"] == "full-check")
    review.report["steps"].append(old | {"status": status})
    review.save()
    assert review.report["verdict"] != "READY_FOR_RELEASE_REVIEW"
    assert all(
        g["status"] == status
        for g in review.report["gates"]
        if g["gate_id"] in {"B", "C", "D", "E", "F"}
    )


@pytest.mark.parametrize(
    "kind", ["trace", "evaluation", "recovery", "workflow", "knowledge", "computer", "extensions"]
)
def test_original_regression_report_contracts(scheduler_qa, tmp_path, kind):
    _, validation = scheduler_qa
    labels = [
        "Real SDK stdio discovers eight disabled tools",
        "Twenty MCP/Skills benchmark cases pass in the actual candidate worker",
        "Actual candidate HTTP fixture connects, discovers and reads a resource through Streamable HTTP",
        "UI loads 20 configured servers without starting any executable",
    ]
    report = {
        "checks": [{"label": label, "passed": True} for label in labels],
        "realModelRequests": 0,
        "realVoiceRequests": 0,
    }
    if kind in {"trace", "evaluation", "recovery"}:
        report["pass"] = True
    elif kind != "extensions":
        report["result"] = "PASS" if kind == "computer" else "pass"
    filename = tmp_path / "report.json"
    filename.write_text(json.dumps(report), encoding="utf-8")
    validation.regression_gui(tmp_path, kind)
    report["checks"][-1]["passed"] = False
    filename.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="assertions"):
        validation.regression_gui(tmp_path, kind)


@pytest.mark.parametrize(
    "fault",
    [
        "error",
        "failure",
        "cleanupError",
        "realModelRequests",
        "realVoiceRequests",
        "pass",
        "result",
        "checks",
    ],
)
def test_regression_report_never_hides_failure(scheduler_qa, tmp_path, fault):
    _, validation = scheduler_qa
    report = {
        "pass": True,
        "checks": [{"label": str(i), "passed": True} for i in range(3)],
        "realModelRequests": 0,
        "realVoiceRequests": 0,
    }
    report[fault] = [] if fault == "checks" else False if fault == "pass" else 1
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError):
        validation.regression_gui(tmp_path, "trace")


def test_extension_regression_requires_final_real_assertions(scheduler_qa, tmp_path):
    _, validation = scheduler_qa
    report = {
        "checks": [{"label": str(i), "passed": True} for i in range(4)],
        "realModelRequests": 0,
        "realVoiceRequests": 0,
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="extension"):
        validation.regression_gui(tmp_path, "extensions")


def test_artifact_change_invalidates_installed_scheduler(scheduler_qa):
    review, _ = scheduler_qa
    old = next(r for r in review.report["steps"] if r["label"] == "installed-scheduler")
    review.report["steps"].append(old | {"artifact_key": "unrelated"})
    review.save()
    assert next(g for g in review.report["gates"] if g["gate_id"] == "G")["status"] == "NOT_RUN"


def test_gui_validator_rejects_missing_real_wallclock_and_external_usage(scheduler_qa, tmp_path):
    _, validation = scheduler_qa
    report = {
        "result": "PASS",
        "checks": [{"label": label, "passed": True} for label in validation.GUI_CHECKS],
        "realModelRequests": 0,
        "realVoiceRequests": 0,
        "localModelRequests": 0,
        "occurrence_id": "synthetic",
        "workflow_run_id": "synthetic",
        "trigger_delay_ms": None,
        "native_notifications": [{}],
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="wall-clock"):
        validation.gui(tmp_path)
    report["trigger_delay_ms"] = 100
    report["realModelRequests"] = 1
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected model"):
        validation.gui(tmp_path)


@pytest.mark.parametrize(
    "fault",
    [
        "failed-check",
        "missing-check",
        "nan",
        "foreign-owner",
        "foreign-occurrence",
        "failed-notice",
        "missing-scope",
        "valid",
    ],
)
def test_forged_gui_evidence_is_rejected(scheduler_qa, tmp_path, fault):
    _, validation = scheduler_qa
    report = {
        "result": "PASS",
        "checks": [{"label": label, "passed": True} for label in validation.GUI_CHECKS],
        "realModelRequests": 0,
        "realVoiceRequests": 0,
        "localModelRequests": 0,
        "occurrence_id": "11111111-1111-4111-8111-111111111111",
        "workflow_run_id": "22222222-2222-4222-8222-222222222222",
        "schedulerConversation": "33333333-3333-4333-8333-333333333333",
        "schedule_id": "44444444-4444-4444-8444-444444444444",
        "trigger_delay_ms": 100,
        "native_notifications": [
            {
                "occurrence_id": "11111111-1111-4111-8111-111111111111",
                "conversation_id": "33333333-3333-4333-8333-333333333333",
                "schedule_id": "44444444-4444-4444-8444-444444444444",
                "kind": "COMPLETED",
                "delivery": "SHOWN",
            }
        ],
    }
    if fault == "failed-check":
        report["checks"][0]["passed"] = False
    if fault == "missing-check":
        report["checks"][0]["label"] = "Unrelated success"
    if fault == "nan":
        report["trigger_delay_ms"] = float("nan")
    if fault == "foreign-owner":
        report["native_notifications"][0]["conversation_id"] = "different-owner"
    if fault == "foreign-occurrence":
        report["native_notifications"][0]["occurrence_id"] = "different-occurrence"
    if fault == "failed-notice":
        report["native_notifications"][0]["delivery"] = "FAILED"
    if fault == "missing-scope":
        del report["schedulerConversation"], report["schedule_id"]
        del (
            report["native_notifications"][0]["conversation_id"],
            report["native_notifications"][0]["schedule_id"],
        )
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    if fault == "valid":
        validation.gui(tmp_path)
        return
    with pytest.raises(ValueError):
        validation.gui(tmp_path)


def test_independent_domain_rejects_unrelated_report_and_missing_coverage(scheduler_qa, tmp_path):
    _, validation = scheduler_qa
    source = tmp_path
    filename = source / "review.json"
    filename.write_text("unrelated success text", encoding="utf-8")
    item = {"report": {"path": "review.json", "sha256": validation.sha(filename)}}
    with pytest.raises(ValueError):
        validation.domain_report(source, "runtime", item, "revision")
    report = {
        "schema_version": 1,
        "domain": "runtime",
        "status": "PASS",
        "open_p1_p2": [],
        "reviewed_revision": "revision",
        "source_hashes": {},
        "commands": [],
    }
    filename.write_text(json.dumps(report), encoding="utf-8")
    item["report"]["sha256"] = validation.sha(filename)
    with pytest.raises(ValueError, match="coverage"):
        validation.domain_report(source, "runtime", item, "revision")
