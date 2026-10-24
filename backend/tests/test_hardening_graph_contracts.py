"""Independent negative shapes: missing graph/native scope and idle-only load."""

import copy
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    return tuple(
        importlib.import_module(name)
        for name in ("sbom_evidence", "hardening_evidence", "hardening_e2e")
    )


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def graph_fixture(tmp_path):
    source, home = tmp_path / "source", tmp_path / "evidence"
    write(
        source / "desktop/package-lock.json",
        {
            "packages": {
                "": {"dependencies": {"a": "1"}},
                "node_modules/a": {"version": "1", "dependencies": {"b": "2"}},
                "node_modules/b": {"version": "2"},
            }
        },
    )
    write(
        source / "licenses/inventory.json",
        {
            "components": [
                {
                    "name": "native-codec",
                    "version": "3",
                    "license": "BSD-3-Clause",
                }
            ]
        },
    )
    a, b = (
        {"name": "a", "version": "1", "bom-ref": "a@1"},
        {"name": "b", "version": "2", "bom-ref": "b@2"},
    )
    npm = {
        "metadata": {"component": {"bom-ref": "app@1"}},
        "components": [a, b],
        "dependencies": [
            {"ref": "app@1", "dependsOn": ["a@1"]},
            {"ref": "a@1", "dependsOn": ["b@2"]},
            {"ref": "b@2", "dependsOn": []},
        ],
    }
    write(home / "npm-sbom.json", npm)
    combined = copy.deepcopy(npm)
    combined["components"].append(
        {
            "name": "native-codec",
            "version": "3",
            "bom-ref": "companion:vendor:native-codec:3",
            "properties": [{"name": "companion:declared-license", "value": "BSD-3-Clause"}],
            "externalReferences": [{"type": "website", "url": "https://example.invalid/public"}],
        }
    )
    combined["dependencies"].append({"ref": "companion:vendor:native-codec:3", "dependsOn": []})
    combined["dependencies"][0]["dependsOn"].append("companion:vendor:native-codec:3")
    return source, home, npm, combined


@pytest.mark.parametrize(
    "omission",
    [
        "npm_component",
        "npm_edge",
        "native_component",
        "original_edge",
        "peer_edge",
        "duplicate_ref",
    ],
)
def test_sbom_rejects_real_scope_omissions(modules, graph_fixture, omission):
    sbom, _, _ = modules
    source, home, npm, combined = graph_fixture
    sbom.validate_sbom(source, home, combined, {})
    if omission == "npm_component":
        combined["components"].pop(0)
    elif omission == "native_component":
        combined["components"].pop()
    elif omission == "npm_edge":
        combined["dependencies"][1]["dependsOn"] = []
    elif omission == "duplicate_ref":
        combined["dependencies"].insert(0, {"ref": "a@1", "dependsOn": []})
    else:
        if omission == "peer_edge":
            path = source / "desktop/package-lock.json"
            lock = json.loads(path.read_text())
            lock["packages"]["node_modules/a"].pop("dependencies")
            lock["packages"]["node_modules/a"]["peerDependencies"] = {"b": "2"}
            write(path, lock)
        npm["dependencies"][1]["dependsOn"] = []
        combined["dependencies"][1]["dependsOn"] = []
        write(home / "npm-sbom.json", npm)
    with pytest.raises(ValueError):
        sbom.validate_sbom(source, home, combined, {})


def test_elapsed_time_with_zero_work_is_rejected(modules, tmp_path):
    _, evidence, _ = modules
    path = tmp_path / "idle.json"
    write(
        path,
        {
            "status": "PASS",
            "requested_seconds": 3600,
            "actual_seconds": 3601,
            "cycles": 0,
            "mcp_children": [],
            "samples": [
                {"elapsed_seconds": 20 * (i + 1), "phase": "idle_only"} for i in range(180)
            ],
        },
    )
    with pytest.raises(ValueError, match="workload"):
        evidence.soak_status(path)


def test_e2e_identity_requires_exact_production_module_and_function(modules):
    _, _, e2e = modules
    for key, identity in e2e.E2E_TESTS.items():
        assert e2e.matching_tests([{"id": identity, "status": "PASS"}], key)
        function = identity.split("::")[1]
        assert not e2e.matching_tests(
            [{"id": "unrelated.module::" + function, "status": "PASS"}], key
        )
        assert not e2e.matching_tests([{"id": identity + "_imposter", "status": "PASS"}], key)


@pytest.mark.parametrize(
    "contradiction", ["failed_receipts", "reversed_phases", "opposite_terminal_states"]
)
def test_soak_rejects_contradictory_success_receipts(modules, tmp_path, contradiction):
    _, evidence, _ = modules
    receipts = [
        {
            "cycle": i,
            "phase": "core" if i < 90 else "mixed_cancel",
            "agent_plan_id": "agent-" + str(i),
            "workflow_run_id": "workflow-" + str(i),
            "voice_session_id": "voice-" + str(i),
            "workflow_state": "COMPLETED",
            "voice_final": "FINAL",
            "rag_results": 1,
            "rag_content_sha256": "a" * 64,
        }
        for i in range(180)
    ]
    report = {
        "status": "PASS",
        "requested_seconds": 3600,
        "actual_seconds": 3601,
        "cycles": 180,
        "workload_receipts": receipts,
        "synthetic_model_requests": 180,
        "samples": [
            {
                "cycle": i,
                "phase": row["phase"],
                "elapsed_seconds": i * 20 + 10,
                "owned_live_children": 0,
                "mcp_connections": 0,
                "workflow_tasks": 0,
                "trace_queue": 0,
                "trace_metrics": {"write_failures": 0},
            }
            for i, row in enumerate(receipts)
        ],
        "mcp_children": [{"pid": i + 1, "exit_code": 0} for i in range(30)],
        "schedule_receipts": [
            {
                "cycle": i,
                "occurrence_id": "occ-" + str(i),
                "workflow_run_id": "scheduled-" + str(i),
                "status": "COMPLETED",
            }
            for i in range(0, 180, 30)
        ],
        "cancel_receipts": [
            {"cycle": i, "workflow_run_id": "cancel-" + str(i), "state": "CANCELLED"}
            for i in range(90, 180, 3)
        ],
        "sqlite_errors": [],
        "unexpected_async_errors": [],
        "owned_live_children_at_close": 0,
    }
    path = tmp_path / "controlled-soak.json"
    write(path, report)
    assert evidence.soak_status(path) == "PASS"  # validator-only synthetic baseline
    if contradiction == "failed_receipts":
        report["schedule_receipts"] = [{"status": "FAILED"}] * 6
        report["cancel_receipts"] = [{"state": "COMPLETED"}] * 30
    elif contradiction == "opposite_terminal_states":
        report["cancel_receipts"][0]["workflow_run_id"] = receipts[90]["workflow_run_id"]
    else:
        for sample, receipt in zip(report["samples"], receipts):
            sample["phase"] = receipt["phase"] = (
                "mixed_cancel" if sample["elapsed_seconds"] < 1800 else "core"
            )
    write(path, report)
    with pytest.raises(ValueError):
        evidence.soak_status(path)
