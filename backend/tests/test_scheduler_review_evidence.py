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
        "checks": [{}] * 12,
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
