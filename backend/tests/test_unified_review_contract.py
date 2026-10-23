"""Adversarial tests for the QA aggregator, using synthetic evidence only."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def qa(monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts/review-all-skills.py"
    monkeypatch.syspath_prepend(str(script.parent))
    spec = importlib.util.spec_from_file_location("unified_qa_contract", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_junit_retains_failure_error_skip_and_sample_time(qa, tmp_path):
    path = tmp_path / "actual.xml"
    path.write_text(
        '<testsuite><testcase classname="test_one" name="a" time="1.2"/><testcase classname="test_one" name="b"><failure/></testcase><testcase classname="test_one" name="c"><error/></testcase><testcase classname="test_one" name="d"><skipped/></testcase></testsuite>',
        encoding="utf-8",
    )
    rows = qa.junit(path)
    assert [r["status"] for r in rows] == ["PASS", "FAIL", "FAIL", "BLOCKED"]
    assert rows[0]["seconds"] == 1.2 and qa.outcome(rows) == "FAIL"
    assert qa.outcome([]) == "NOT_RUN"
    assert qa.outcome([{"status": "NOT_RUN"}]) == "NOT_RUN"
    assert qa.outcome([{"status": "BLOCKED"}]) == "PARTIAL"


def test_missing_steps_cannot_sign_off_and_new_failure_wins(qa, tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "docs").mkdir(parents=True)
    (source / "docs/SKILL_01_10_FEATURES.json").write_text("[]", encoding="utf-8")
    review = object.__new__(qa.UnifiedReview)
    review.source, review.root, review.build_id, review.profile = (
        source,
        tmp_path,
        "synthetic",
        None,
    )
    review.identity = {"source_revision": "synthetic-commit", "version": "synthetic"}
    review.report = {
        "steps": [
            {"identity": review.identity, "label": "full-check", "status": "PASS"},
            {"identity": review.identity, "label": "full-check", "status": "FAIL"},
        ]
    }
    monkeypatch.setattr(qa, "source_identity", lambda *_: review.identity)
    monkeypatch.setattr(review, "current_artifact_key", lambda: "current")
    report = review.summarize()
    assert report["verdict"] == "BLOCKED"
    assert next(row for row in report["gates"] if row["gate"] == "03")["status"] == "FAIL"
    assert next(row for row in report["gates"] if row["gate"] == "09")["status"] == "NOT_RUN"
    assert report["tests"] == []
    assert (
        json.loads((tmp_path / "UNIFIED_ACCEPTANCE_01_10.json").read_text())["verdict"] == "BLOCKED"
    )


def test_artifact_change_invalidates_old_build_pass(qa, tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "docs").mkdir(parents=True)
    (source / "docs/SKILL_01_10_FEATURES.json").write_text("[]", encoding="utf-8")
    review = object.__new__(qa.UnifiedReview)
    review.source, review.root, review.build_id, review.profile = (
        source,
        tmp_path,
        "synthetic",
        None,
    )
    review.identity = {"source_revision": "synthetic-commit", "version": "synthetic"}
    review.report = {
        "steps": [
            {"identity": review.identity, "label": label, "status": "PASS", "artifact_key": "old"}
            for label in ("build", "installer-dryrun", "installer")
        ]
    }
    monkeypatch.setattr(qa, "source_identity", lambda *_: review.identity)
    monkeypatch.setattr(review, "current_artifact_key", lambda: "changed")
    report = review.summarize()
    assert report["verdict"] == "INCOMPLETE"
    assert next(row for row in report["gates"] if row["gate"] == "14")["status"] == "NOT_RUN"


def test_exact_int_case_set_cannot_be_replaced_by_sixteen_duplicates(qa):
    from unified_review_contracts import verify_integration

    fake = [
        {"id": "backend.tests.test_unified::test_INT_001_fake" + str(n), "status": "PASS"}
        for n in range(16)
    ]
    with pytest.raises(ValueError, match="exact unique"):
        verify_integration(fake)
    good = [
        {"id": f"backend.tests.test_unified::test_INT_{n:03}_actual", "status": "PASS"}
        for n in range(1, 17)
    ]
    verify_integration(good)
    good[4]["status"] = "BLOCKED"
    with pytest.raises(ValueError):
        verify_integration(good)


def test_module_pass_cannot_prove_an_unmapped_feature(qa):
    from unified_review_contracts import feature_result

    rows = [{"id": "backend.tests.test_trace::test_unique_id", "status": "PASS"}]
    assert feature_result({"name": "persistent replay"}, rows)[0] == "PARTIAL"
    assert (
        feature_result({"direct_test_ids": ["backend.tests.test_trace::test_persistence"]}, rows)[0]
        == "PARTIAL"
    )
    assert feature_result({"direct_test_ids": [rows[0]["id"]]}, rows)[0] == "PASS"


def test_environment_skip_allowlist_requires_the_exact_reason(qa):
    from unified_review_contracts import allowed_skip, ALLOWED_SKIPS

    key = next(iter(ALLOWED_SKIPS))
    assert allowed_skip(
        {"id": key, "status": "BLOCKED", "skip_reason": ALLOWED_SKIPS[key] + ": 1314"}
    )
    assert not allowed_skip(
        {"id": key, "status": "BLOCKED", "skip_reason": "unrelated core defect"}
    )


def test_router_contract_requires_actual_results_and_dataset_identity(qa, tmp_path):
    from unified_review_contracts import verify_router
    from visual_review_evidence import sha

    dataset = tmp_path / "backend/app/services/evaluation/datasets/router-core.json"
    dataset.parent.mkdir(parents=True)
    cases = [{"case_id": f"ROUTE-{n:03}"} for n in range(1, 49)]
    dataset.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    report = {
        "run": {
            "snapshot": {
                "dataset_id": "router-core",
                "dataset_sha256": sha(dataset),
                "git_commit": "test-commit",
            }
        },
        "results": [
            {
                "case_id": row["case_id"],
                "execution_status": "PASS",
                "grader_status": "GRADED",
                "result": {"passed": True},
            }
            for row in cases
        ],
    }
    path = tmp_path / "router-result.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verify_router(tmp_path, path, "test-commit")
    report["results"][1]["case_id"] = "ROUTE-001"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="case set"):
        verify_router(tmp_path, path, "test-commit")


def test_parameter_selector_cannot_be_proven_by_another_parameter(qa):
    from unified_review_contracts import feature_result

    rows = [{"id": "test::case[one]", "status": "PASS"}]
    assert feature_result({"direct_test_ids": ["test::case[two]"]}, rows)[0] != "PASS"


def test_actual_voice_and_workbench_report_contracts_and_error_rejection(qa, tmp_path):
    from unified_review_contracts import verify_gui

    data = {
        "checks": [{"label": str(i), "passed": True} for i in range(3)],
        "realModelRequests": 0,
        "realVoiceRequests": 0,
    }
    for kind, field in (("voice", "pass"), ("workbench", "success")):
        (tmp_path / "report.json").write_text(json.dumps({**data, field: True}))
        verify_gui(tmp_path, kind)
        (tmp_path / "report.json").write_text(
            json.dumps({**data, field: True, "cleanupError": "failed"})
        )
        with pytest.raises(ValueError):
            verify_gui(tmp_path, kind)


def test_missing_required_gui_never_waived_as_scope_limitation(qa, tmp_path):
    from types import SimpleNamespace
    from unified_feature_evidence import feature_matrix

    review = SimpleNamespace(source=tmp_path, report={})
    feature = {
        "skill_id": "SKILL-09",
        "name": "Playback",
        "direct_test_ids": ["test::a"],
        "gui_requirement": "REQUIRED",
        "gui_harness": "tests/ui/realtime-voice-e2e.cjs",
        "gui_labels": ["Actual playback"],
        "scope_limited": True,
    }
    row = feature_matrix(review, [feature], [{"id": "test::a", "status": "PASS"}], {}, None)[0]
    assert row["status"] == "NOT_RUN" and not row["accepted_environment_limit"]


def test_missing_exact_backend_case_cannot_be_waived_by_hardware_skip(qa, tmp_path):
    from types import SimpleNamespace
    from unified_feature_evidence import feature_matrix
    from unified_review_contracts import ALLOWED_SKIPS

    key = next(iter(ALLOWED_SKIPS))
    feature = {"direct_test_ids": [key, "test::missing"], "scope_limited": True}
    rows = [{"id": key, "status": "BLOCKED", "skip_reason": ALLOWED_SKIPS[key]}]
    result = feature_matrix(SimpleNamespace(source=tmp_path, report={}), [feature], rows, {}, None)[
        0
    ]
    assert result["status"] != "PASS" and not result["accepted_environment_limit"]


def test_edited_bound_gui_artifact_invalidates_success(qa, tmp_path, monkeypatch):
    from visual_review_evidence import sha

    source = tmp_path / "source"
    (source / "docs").mkdir(parents=True)
    (source / "docs/SKILL_01_10_FEATURES.json").write_text("[]")
    artifact = source / "report.json"
    artifact.write_text("{}")
    review = object.__new__(qa.UnifiedReview)
    review.source, review.root, review.build_id, review.profile = (
        source,
        tmp_path,
        "synthetic",
        None,
    )
    review.identity = {"source_revision": "synthetic", "version": "synthetic"}
    review.report = {
        "steps": [{"identity": review.identity, "label": "source-voice", "status": "PASS"}],
        "gui_bindings": {
            "source-voice": {"identity": review.identity, "files": {"report.json": sha(artifact)}}
        },
    }
    artifact.write_text('{"changed":true}')
    monkeypatch.setattr(qa, "source_identity", lambda *_: review.identity)
    monkeypatch.setattr(review, "current_artifact_key", lambda: "current")
    result = review.summarize()
    assert next(s for s in result["steps"] if s["label"] == "source-voice")["status"] == "FAIL"
