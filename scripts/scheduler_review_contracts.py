"""Explicit evidence coverage; a successful log from an unrelated domain is insufficient."""

from visual_review_evidence import read, require, safe_file, sha

GUI_CHECKS = {
    "Exact scheduler candidate identity",
    "Default close exits; background not silently enabled",
    "UI creates real one-minute schedule and UTC preview",
    "Actual close event hides window to live native tray",
    "Real wall-clock task finishes while window hidden",
    "Original Workflow executed typed synthetic target",
    "Native outbox state retained without fabricated delivery",
    "Real tray recent handler navigates exact persisted occurrence",
    "History opens original read-only Trace",
    "Pause, enable and archive preserve completed history",
    "Tray Quit stops owned backend",
    "Pure scheduled target makes zero model requests",
}
DOMAIN_FILES = {
    "runtime": {
        "backend/app/services/scheduling/" + name + ".py"
        for name in (
            "admission",
            "coordinator",
            "store",
            "occurrences",
            "monitor",
            "recovery",
            "definitions",
        )
    }
    | {
        "backend/app/services/workflows/" + name + ".py"
        for name in ("coordinator", "node_runner", "runtime_bridge", "repository", "controls")
    }
    | {
        "backend/app/main.py",
        "backend/app/services/runtime_registry.py",
        "backend/app/core/profile_lock.py",
        "backend/tests/test_scheduler_adversarial.py",
    },
    "security": {
        "backend/app/services/scheduling/" + name + ".py"
        for name in ("permissions", "conditions", "directory_pin", "definitions", "admission")
    }
    | {
        "backend/app/services/desktop/runtime.py",
        "backend/app/services/knowledge/context.py",
        "backend/app/services/workflows/node_runner.py",
        "backend/app/services/workflows/runtime_bridge.py",
        "backend/app/api/schedules.py",
        "desktop/electron/scheduler-ipc.cjs",
        "desktop/electron/scheduler-background.cjs",
        "backend/tests/test_scheduler_security.py",
    },
    "qa": {
        "scripts/review-scheduler-runner.py",
        "scripts/scheduler_review_validation.py",
        "scripts/scheduler_review_contracts.py",
        "scripts/visual_review_evidence.py",
        "scripts/program_inventory.py",
        "scripts/test-installer.ps1",
        "scripts/build-companion-studio.ps1",
        "tests/ui/scheduler-e2e.cjs",
        "backend/tests/test_scheduler_review_evidence.py",
        "desktop/frontend/src/scheduling/ScheduledTasks.tsx",
        "desktop/frontend/src/scheduling/ScheduleList.tsx",
        "desktop/frontend/src/scheduling/ScheduleHistory.tsx",
        "desktop/frontend/src/scheduling/ScheduleForm.tsx",
        "desktop/electron/main.cjs",
        "desktop/electron/scheduler-ipc.cjs",
        "desktop/electron/scheduler-background.cjs",
    },
}


def domain_report(source, domain, item, revision):
    filename = safe_file(source, item["report"]["path"])
    require(sha(filename) == item["report"]["sha256"], "Domain report digest mismatch")
    report = read(filename)
    require(
        report.get("schema_version") == 1
        and report.get("domain") == domain
        and report.get("status") == "PASS"
        and report.get("open_p1_p2") == [],
        "Invalid independent domain report",
    )
    require(report.get("reviewed_revision") == revision, "Domain revision mismatch")
    hashes = report.get("source_hashes", {})
    require(
        DOMAIN_FILES[domain].issubset(hashes), "Domain production/QA coverage missing: " + domain
    )
    for name, digest in hashes.items():
        require(sha(safe_file(source, name)) == digest, "Domain reviewed source changed: " + name)
    commands = report.get("commands", [])
    require(bool(commands), "Domain lacks executed commands")
    for command in commands:
        require(
            command.get("command") and command.get("exit_code") == 0 and command.get("evidence"),
            "Domain command not passed",
        )
        for evidence in command["evidence"]:
            require(
                sha(safe_file(source, evidence["path"])) == evidence["sha256"],
                "Domain command evidence mismatch",
            )
