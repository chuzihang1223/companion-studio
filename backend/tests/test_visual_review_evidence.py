"""A QA gate must fail closed when its evidence identity or attempt changes."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def qa(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("visual_review_evidence")
    review = module.Evidence()
    review.source = tmp_path
    review.root = tmp_path / ".cache"
    review.root.mkdir()
    review.path = review.root / "automated-review.json"
    review.profile = tmp_path / "first" / "UserData"
    review.build_id = "first-build"
    review.identity = {"source_revision": "first", "build_id": review.build_id, "dirty": False}
    review.current_artifact_key = lambda: "verified-artifact"
    review.report = {"steps": [], "limitations": []}
    monkeypatch.setattr(module, "source_identity", lambda *_: review.identity)
    for gate, labels in module.REQUIRED.items():
        for label in sorted(labels):
            review.report["steps"].append(
                {
                    "gate_id": gate,
                    "label": label,
                    "status": "PASS",
                    "identity": dict(review.identity),
                    "artifact_key": "verified-artifact",
                }
            )
    review.save()
    return SimpleNamespace(review=review, module=module)


@pytest.mark.parametrize("status", ["NOT_RUN", "BLOCKED", "FAIL", "UNKNOWN"])
def test_required_nonpass_never_ready(qa, status):
    review = qa.review
    require_pass = review.report["steps"][-1]
    review.report["steps"].append({**require_pass, "status": status})
    review.save()
    assert review.report["verdict"] != "READY_FOR_RELEASE_REVIEW"
    assert review.report["gates"][-1]["status"] != "PASS"


def test_revision_or_build_change_invalidates_old_results(qa):
    review = qa.review
    assert review.report["verdict"] == "READY_FOR_RELEASE_REVIEW"
    review.identity = {**review.identity, "source_revision": "second", "build_id": "second-build"}
    review.save()
    assert all(gate["status"] == "NOT_RUN" for gate in review.report["gates"])
    assert len(review.report["excluded_historical_steps"]) == len(review.report["steps"])


@pytest.mark.parametrize("status", ["NOT_RUN", "FAIL", "BLOCKED", "PASS"])
def test_new_artifact_mismatch_does_not_resurrect_previous_pass(qa, status):
    review = qa.review
    old = next(row for row in review.report["steps"] if row["label"] == "installed-gui")
    review.report["steps"].append({**old, "status": status, "artifact_key": None})
    review.save()
    assert next(g for g in review.report["gates"] if g["gate_id"] == "D")["status"] != "PASS"
    assert review.report["verdict"] != "READY_FOR_RELEASE_REVIEW"


@pytest.mark.parametrize("error", [FileNotFoundError("missing command"), KeyboardInterrupt()])
def test_launch_or_interrupt_records_attempt_before_launch(qa, monkeypatch, error):
    review = qa.review

    def fail_launch(*_, **kwargs):
        saved = qa.module.read(review.path)
        assert saved["steps"][-1]["label"] == "installed-gui"
        assert saved["steps"][-1]["status"] == "NOT_RUN"
        assert saved["verdict"] != "READY_FOR_RELEASE_REVIEW"
        assert kwargs["env"]["PYTHONOPTIMIZE"] == ""
        raise error

    monkeypatch.setattr(qa.module.subprocess, "run", fail_launch)
    with pytest.raises(type(error)):
        review.step("D", "installed-gui", ["missing-command"])
    saved = qa.module.read(review.path)
    assert saved["steps"][-1]["status"] == "FAIL"
    assert saved["steps"][-1]["completed_at"]


def test_profile_identity_cannot_switch_even_with_equal_contents(qa):
    review = qa.review
    before = review.root / "profile-before.json"
    before.write_text(json.dumps({"sample": "digest"}), encoding="utf-8")
    review.snapshot = lambda: {"sample": "digest"}
    first = review.bind_profile()
    assert first["baseline_sha256"] == qa.module.sha(before)
    review.profile = review.source / "second" / "UserData"
    with pytest.raises(ValueError, match="identity/baseline changed"):
        review.bind_profile()


def test_artifact_digest_and_build_identity_are_checked(qa):
    source = qa.review.source
    delivery = source / "delivery"
    delivery.mkdir()
    (delivery / "win-unpacked").mkdir()
    artifacts = {}
    for name in (
        "Setup.exe",
        "win-unpacked/app.asar",
        "win-unpacked/backend.exe",
        "win-unpacked/app.exe",
    ):
        target = delivery / name
        target.write_bytes(b"fixture-only")
        artifacts[name] = {"sha256": qa.module.sha(target)}
    manifest = {
        "checks_skipped": False,
        "build": {"source_revision": "one", "build_id": "one", "version": "one"},
        "artifacts": artifacts,
        "source_hashes": {},
        "program_files": qa.module.inventory(delivery / "win-unpacked"),
    }
    (delivery / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert qa.module.artifact_key(source, delivery, manifest["build"])
    with pytest.raises(ValueError, match="Build does not belong"):
        qa.module.artifact_key(source, delivery, {**manifest["build"], "build_id": "another"})
    (delivery / "win-unpacked/app.asar").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Artifact digest mismatch"):
        qa.module.artifact_key(source, delivery, manifest["build"])


def test_optimized_python_cannot_print_validation_pass(tmp_path):
    source = Path(__file__).resolve().parents[2]
    invalid = tmp_path / "performance.json"
    invalid.write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(source / "scripts/review-visual-runner.py"),
            "--validate-performance",
            str(invalid),
        ],
        env={**os.environ, "PYTHONOPTIMIZE": "1"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode != 0
    assert "Optimized Python is not supported" in result.stderr
    assert '"status": "PASS"' not in result.stdout
