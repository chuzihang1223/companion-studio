"""Identity and content checks for scheduler-specific evidence. No optimized asserts."""

from pathlib import Path
import math
from uuid import UUID
import subprocess
import xml.etree.ElementTree as ET

from visual_review_evidence import read, require, safe_file, sha
from scheduler_review_contracts import GUI_CHECKS, DOMAIN_FILES, domain_report


GATES = {
    "A": {
        "installed-" + key
        for key in (
            "trace",
            "evaluation",
            "recovery",
            "extensions",
            "workflow",
            "knowledge",
            "computer",
        )
    },
    "B": {"full-check", "source-history-audit"},
    "C": {"full-check"},
    "D": {"full-check", "source-scheduler", "installed-scheduler"},
    "E": {"full-check", "independent-review"},
    "F": {"full-check"},
    "G": {"installed-scheduler"},
    "H": {"performance"},
    "I": {"build", "installer-dryrun", "installer", "release-audit", "manifest-profile"},
    "J": {"independent-review"},
}


def full_results(source, root):
    import shutil

    evidence = source / ".cache/checks"
    xml = ET.parse(evidence / "pytest.xml").getroot()
    suites = xml.findall("testsuite")
    require(
        suites and sum(int(s.get("tests", 0)) for s in suites) >= 500,
        "Incomplete backend regression",
    )
    require(
        all(int(s.get("failures", 0)) == int(s.get("errors", 0)) == 0 for s in suites),
        "Backend failures",
    )
    shutil.copyfile(evidence / "pytest.xml", root / "backend-pytest.xml")
    records = list((evidence / "pytest-temp").rglob("scheduler-result.json"))
    require(len(records) == 1, "Ambiguous/missing 30-case report")
    report = read(records[0])
    rows = report["results"]
    require(
        len(rows) == 30 and {r["case_id"] for r in rows} == {f"SCH-{i:03}" for i in range(1, 31)},
        "Incomplete scheduler cases",
    )
    require(all(r["execution_status"] == "PASS" for r in rows), "Failed scheduler benchmark")
    shutil.copyfile(records[0], root / "scheduler-benchmark.json")
    performance = list((evidence / "pytest-temp").rglob("scheduler-performance.json"))
    require(len(performance) == 1, "Ambiguous/missing performance report")
    shutil.copyfile(performance[0], root / "scheduler-performance.json")


def regression_gui(home, kind):
    """Respect the original seven harness contracts after the command exits zero."""
    report = read(home / "report.json")
    markers = {
        "trace": ("pass", True),
        "evaluation": ("pass", True),
        "recovery": ("pass", True),
        "workflow": ("result", "pass"),
        "knowledge": ("result", "pass"),
        "computer": ("result", "PASS"),
    }
    require(kind in {*markers, "extensions"}, "Unknown regression harness")
    require(
        not any(report.get(key) for key in ("error", "failure", "cleanupError")),
        "Regression or cleanup error",
    )
    require(
        ("pass" not in report or report["pass"] is True)
        and ("result" not in report or report["result"] in {"pass", "PASS"}),
        "Contradictory regression completion marker",
    )
    checks = report.get("checks", [])
    require(
        len(checks) >= 3 and all(row.get("passed") is True and row.get("label") for row in checks),
        "Incomplete or failed regression assertions",
    )
    require(
        all(
            type(report.get(key)) is int and report[key] == 0
            for key in ("realModelRequests", "realVoiceRequests")
        ),
        "Unexpected external regression request",
    )
    if kind == "extensions":
        # This original harness records completion in stdout and exits zero;
        # its JSON deliberately has no top-level pass/result field. Require its
        # final load assertion plus actual stdio, HTTP and benchmark assertions.
        required = {
            "Real SDK stdio discovers eight disabled tools",
            "Twenty MCP/Skills benchmark cases pass in the actual candidate worker",
            "Actual candidate HTTP fixture connects, discovers and reads a resource through Streamable HTTP",
            "UI loads 1000 configured servers without starting any executable",
        }
        require(
            required.issubset({row["label"] for row in checks}), "Incomplete extension regression"
        )
    else:
        key, expected = markers[kind]
        require(
            report.get(key) == expected and type(report.get(key)) is type(expected),
            "Regression completion marker missing",
        )


def gui(home):
    report = read(home / "report.json")
    require(
        report.get("result") == "PASS" and len(report.get("checks", [])) >= 12,
        "Incomplete scheduler GUI",
    )
    checks = report["checks"]
    require(
        GUI_CHECKS.issubset({r.get("label") for r in checks})
        and all(r.get("passed") is True for r in checks),
        "Required GUI assertions missing or failed",
    )
    require(
        report["realModelRequests"]
        == report["realVoiceRequests"]
        == report["localModelRequests"]
        == 0,
        "Unexpected model request",
    )
    require(
        report.get("occurrence_id") and report.get("workflow_run_id"), "Missing original run links"
    )
    require(
        type(report.get("trigger_delay_ms")) in {int, float}
        and math.isfinite(report["trigger_delay_ms"])
        and report["trigger_delay_ms"] >= 0,
        "Missing finite wall-clock delay",
    )
    for key in ("schedulerConversation", "schedule_id", "occurrence_id", "workflow_run_id"):
        value = report.get(key)
        try:
            valid = isinstance(value, str) and str(UUID(value)) == value
        except ValueError:
            valid = False
        require(valid, "Missing or invalid GUI scope UUID: " + key)
    notices = [
        n
        for n in report.get("native_notifications", [])
        if n.get("occurrence_id") == report["occurrence_id"]
        and n.get("conversation_id") == report.get("schedulerConversation")
        and n.get("schedule_id") == report.get("schedule_id")
        and n.get("kind") == "COMPLETED"
    ]
    require(bool(notices), "Missing exact owner/schedule/occurrence notification")
    require(
        any(n.get("delivery") == "SHOWN" for n in notices),
        "Native notification not shown; record delivery as blocked or failed",
    )


def performance(path):
    value = read(path)
    require(
        value["schedule_count"] == 100 and value["synthetic_history_count"] == 1000,
        "Incomplete load fixture",
    )
    require(
        value["resources_released"] and value["active_not_masked"],
        "Performance fixture leaked resources",
    )
    require(
        value["idle_window_ms"] >= 9000 and value["idle_process_cpu_ms"] >= 0,
        "Missing measured idle CPU",
    )
    require(
        value["manual_dispatch_samples"] == 10 and value["manual_dispatch_p95_ms"] > 0,
        "Missing dispatch samples",
    )


def independent(source, path):
    value = read(path)
    require(value["status"] == "PASS" and value["open_p1_p2"] == [], "Open independent findings")
    require(
        set(value["reviews"]) >= {"runtime", "security", "qa"}, "Missing independent review domain"
    )
    required = {
        "scripts/review-scheduler-runner.py",
        "scripts/scheduler_review_validation.py",
        "scripts/scheduler_review_contracts.py",
        "scripts/visual_review_evidence.py",
        "backend/tests/test_scheduler_review_evidence.py",
        "docs/SCHEDULER_AUTOMATED_QA.md",
        "tests/ui/scheduler-e2e.cjs",
        "scripts/program_inventory.py",
        "scripts/test-installer.ps1",
        "scripts/build-companion-studio.ps1",
    }
    hashes = value["source_hashes"]
    require(required.issubset(hashes), "Independent QA implementation coverage missing")
    for name, digest in hashes.items():
        require(sha(safe_file(source, name)) == digest, "Reviewed content changed: " + name)
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", value["reviewed_revision"], "HEAD"],
        cwd=source,
        check=True,
    )
    later = subprocess.check_output(
        ["git", "diff", "--name-only", value["reviewed_revision"], "HEAD"], cwd=source, text=True
    ).splitlines()
    require(all(name in hashes for name in later), "Later source outside reviewed content")
    for review in value["reviews"].values():
        require(review["status"] == "PASS" and review["evidence"], "Review lacks actual evidence")
        for item in review["evidence"]:
            require(
                sha(safe_file(source, item["path"])) == item["sha256"], "Review evidence mismatch"
            )
    for domain in DOMAIN_FILES:
        domain_report(source, domain, value["reviews"][domain], value["reviewed_revision"])


def checked_profile(review):
    review.current_artifact_key()
    review.installed_program()
    require(
        read(review.root / "profile-before.json") == review.snapshot(), "Protected profile changed"
    )
    binding = review.bind_profile()
    (review.root / "profile-protection.json").write_text(
        __import__("json").dumps(
            {
                "status": "PASS",
                "binding": binding,
                "files": len(review.snapshot()),
                "unchanged": True,
            }
        ),
        encoding="utf-8",
    )
