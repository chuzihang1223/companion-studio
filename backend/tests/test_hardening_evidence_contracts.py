"""Negative controls: a diagnostic or altered report cannot certify a release."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def contracts(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return importlib.import_module("hardening_evidence")


def test_short_diagnostic_and_failed_soak_cannot_be_passed(contracts, tmp_path):
    path = tmp_path / "soak.json"
    for value in (
        {"status": "DIAGNOSTIC_PASS_ONLY", "actual_seconds": 3601},
        {"status": "FAIL", "actual_seconds": 3601},
        {"status": "PASS", "actual_seconds": 35, "requested_seconds": 3600},
    ):
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(ValueError):
            contracts.soak_status(path)


def test_advisory_source_revision_is_enforced_before_acceptance(contracts, tmp_path):
    (tmp_path / "dependency-security.json").write_text(
        '{"source_revision":"old","registry_scope_status":"PASS"}', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="another revision"):
        contracts.dependency_status(tmp_path, tmp_path, "current")


def test_changed_dependency_lock_cannot_keep_old_advisory_pass(contracts, tmp_path):
    from datetime import datetime, timezone

    locks = {
        "backend/requirements.lock",
        "backend/requirements-dev.txt",
        "desktop/package-lock.json",
    }
    for name in locks:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("changed", encoding="utf-8")
    (tmp_path / "dependency-security.json").write_text(
        json.dumps(
            {
                "source_revision": "current",
                "queried_utc": datetime.now(timezone.utc).isoformat(),
                "lock_hashes": dict.fromkeys(locks, "old"),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="changed after audit"):
        contracts.dependency_status(tmp_path, tmp_path, "current")
