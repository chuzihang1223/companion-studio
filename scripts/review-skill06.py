"""Verify supplied Skill 06 artifacts against current source and freshly run checks.

This program reads evidence; it does not turn old reports into new test executions.
The caller must run check.ps1 and the packaged Knowledge GUI first.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import xml.etree.ElementTree as ET


BASE = "327caf2437bb462745ff0ae63c17a9cea8d4b93d"
EXPECTED_SETUP = "a2b8427801a7c7217576b7569e33b7d75259b364f9cc5c8d724316fc47f5d4f3"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(source, review, evidence):
    source, review, evidence = source.resolve(), review.resolve(), evidence.resolve()
    manifest = read(review / "Installer/build-manifest.json")
    claims = []
    report = (review / "Documentation/DELIVERY_REPORT_58_FIELDS.md").read_text(encoding="utf-8")
    for line in report.splitlines():
        match = re.match(r"^\| (\d+) \| ([^|]+) \| (.*) \|$", line)
        if match:
            claims.append({"number": int(match[1]), "label": match[2], "claim": match[3]})
    setup = review / "Installer/Companion-Studio-0.2.0-knowledge.1-Setup.exe"
    ancestor = (
        subprocess.run(
            ["git", "-C", str(source), "merge-base", "--is-ancestor", BASE, "HEAD"],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )
    changed = []
    for name, digest in manifest["source_hashes"].items():
        relative = PurePosixPath(name)
        candidate = source / relative
        path = candidate.resolve()
        # A manifest is data, not permission to hash arbitrary private files.
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or ":" in name
            or not path.is_relative_to(source)
        ):
            raise ValueError("Invalid manifest source path")
        if name.startswith(("UserData/", ".runtime/", ".cache/")):
            raise ValueError("Private manifest source path")
        if not name.startswith(
            ("backend/", "desktop/", "characters/", "examples/agent-skills/", "scripts/")
        ):
            raise ValueError("Unsupported manifest source path")
        if path.suffix not in {
            ".py",
            ".json",
            ".cjs",
            ".ps1",
            ".cs",
            ".ts",
            ".tsx",
            ".css",
            ".svg",
            ".md",
            ".ico",
            ".nsh",
            ".lock",
            ".txt",
        }:
            raise ValueError("Unsupported manifest source type")
        if any(
            node.is_symlink() or node.is_junction()
            for node in (candidate, *candidate.parents)
            if node != source and node.is_relative_to(source)
        ):
            raise ValueError("Linked manifest source path")
        if not path.is_file() or path.is_symlink() or sha(path) != digest:
            changed.append(name)
    junit = ET.parse(evidence / "pytest.xml").getroot()
    cases = list(junit.iter("testcase"))
    failures = [
        row.get("name")
        for row in cases
        if row.find("failure") is not None or row.find("error") is not None
    ]
    skipped = sum(row.find("skipped") is not None for row in cases)
    benchmark = read(evidence / "knowledge-benchmark.json")
    results = benchmark["results"]
    gui = read(evidence / "installed-gui.json")
    checks = {
        "baseline_ancestor": ancestor,
        "original_58_claims_present": len(claims) == 58,
        "installer_hash": sha(setup) == EXPECTED_SETUP,
        "manifest_hash": manifest["artifacts"][setup.name]["sha256"] == EXPECTED_SETUP,
        "baseline_product_hashes": not changed,
        "fresh_full_check": "Automated checks passed"
        in (evidence / "full-check.log").read_text(encoding="utf-8-sig", errors="replace")
        and not failures,
        "fresh_knowledge_28": len(results) == 28
        and all(row["execution_status"] == "PASS" for row in results),
        "fresh_packaged_gui": gui.get("result") == "pass"
        and not gui["sourceMode"]
        and all(row["passed"] for row in gui["checks"]),
        "no_real_model": gui["realModelRequests"] == gui["realVoiceRequests"] == 0
        and benchmark["run"]["metrics"]["real_task_success_rate"] is None,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "baseline_commit": BASE,
        "source_build_commit": manifest["build"]["source_revision"],
        "checks": checks,
        "original_delivery_claims": claims,
        "changed_baseline_product_files": changed,
        "tests": {
            "passed": len(cases) - skipped - len(failures),
            "skipped": skipped,
            "failures": failures,
            "knowledge_cases": len(results),
            "packaged_gui_checks": len(gui["checks"]),
        },
        "evidence": {
            "pytest": "pytest.xml",
            "benchmark": "knowledge-benchmark.json",
            "gui": "installed-gui.json",
            "source_code": [
                "backend/app/services/knowledge/",
                "backend/app/database/migrations.py",
                "desktop/electron/knowledge.cjs",
                "desktop/frontend/src/knowledge/",
            ],
        },
        "not_rerun": [
            "Skill06 NSIS installer execution (hash and packaged runtime reverified)",
            "real embedding/model/voice quality",
            "all physical display/DPI configurations",
        ],
        "limitations": [
            "Keyword retrieval is not semantic quality.",
            "Existing vectors have no ANN acceleration.",
            "Unsigned installer is not publisher certification.",
            "Original native dialog choices were controlled test inputs, not human approval.",
        ],
        "critical_blockers": [name for name, passed in checks.items() if not passed],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.source, args.review, args.evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "tests": report["tests"],
                "blockers": report["critical_blockers"],
            }
        )
    )
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
