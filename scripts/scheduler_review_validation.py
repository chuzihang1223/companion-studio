"""Identity and content checks for scheduler-specific evidence. No optimized asserts."""

from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from visual_review_evidence import read, require, safe_file, sha


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


def gui(home):
    report = read(home / "report.json")
    require(
        report.get("result") == "PASS" and len(report.get("checks", [])) >= 12,
        "Incomplete scheduler GUI",
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
    require(isinstance(report.get("trigger_delay_ms"), (int, float)), "Missing wall-clock delay")
    require(report.get("native_notifications"), "Missing real native notification observation")


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
