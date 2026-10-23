"""Fail-closed contracts for current-source GUI, benchmark and independent evidence."""

import re
import subprocess
from visual_review_evidence import read, require, sha, safe_file
from scheduler_review_validation import regression_gui, gui as scheduler_gui

ALLOWED_SKIPS = {
    "backend.tests.test_skill_security::test_import_symlink_rejected_if_windows_supports_links": "Windows account cannot create symlinks",
    "backend.tests.test_v81_workspace_permissions::test_symlinks_cannot_escape_or_alias_secret_files": "Windows account cannot create symlinks",
}


def allowed_skip(row):
    return (
        row.get("status") == "BLOCKED"
        and row.get("id") in ALLOWED_SKIPS
        and row.get("skip_reason", "").startswith(ALLOWED_SKIPS[row["id"]])
    )


def verify_gui(home, kind):
    if kind in {"trace", "evaluation", "recovery", "extensions", "workflow", "knowledge"}:
        return regression_gui(home, kind)
    if kind == "scheduler":
        return scheduler_gui(home)
    value = read(home / "report.json")
    marker = (
        value.get("pass") is True
        if kind == "voice"
        else value.get("success") is True
        if kind == "workbench"
        else value.get("result") == "PASS"
    )
    require(
        marker and not any(value.get(k) for k in ("error", "failure", "cleanupError")),
        "GUI completion/error mismatch",
    )
    checks = value.get("checks", [])
    require(
        len(checks) >= 3 and all(row.get("passed") is True and row.get("label") for row in checks),
        "GUI lacks actual assertions",
    )
    require(
        all(
            type(value.get(k)) is int and value[k] == 0
            for k in ("realModelRequests", "realVoiceRequests")
        ),
        "Unexpected or unknown external usage",
    )


def verify_router(source, path, revision):
    value = read(path)
    snapshot = value["run"]["snapshot"]
    dataset = source / "backend/app/services/evaluation/datasets/router-core.json"
    expected = {row["case_id"] for row in read(dataset)["cases"]}
    require(
        snapshot["dataset_id"] == "router-core"
        and snapshot["dataset_sha256"] == sha(dataset)
        and snapshot["git_commit"] == revision,
        "Router evaluation identity mismatch",
    )
    require(
        expected == {f"ROUTE-{n:03}" for n in range(1, 49)},
        "Router dataset must contain the specified 48 cases",
    )
    rows = value["results"]
    require(
        len(rows) == len(expected) and {r["case_id"] for r in rows} == expected,
        "Actual worker result case set mismatch",
    )
    require(
        all(
            r["execution_status"] == "PASS"
            and r["grader_status"] == "GRADED"
            and r["result"].get("passed") is True
            for r in rows
        ),
        "Router evaluation includes non-passed case",
    )
    return value


def verify_integration(rows):
    ids = [re.search(r"::test_INT_(\d{3})_", row["id"]) for row in rows]
    actual = [match.group(1) for match in ids if match]
    require(
        len(actual) == 16 and set(actual) == {f"{n:03}" for n in range(1, 17)},
        "Expected exact unique INT001..016",
    )
    require(
        all(row["status"] == "PASS" for row, match in zip(rows, ids) if match),
        "Integration assertion not executed or failed",
    )


def verify_independent(source, path, revision, build_id):
    value = read(path)
    require(
        value.get("source_revision") == revision and value.get("build_id") == build_id,
        "Independent identity mismatch",
    )
    require(
        value.get("status") == "PASS" and value.get("unresolved_findings") == [],
        "Independent review requires changes",
    )
    tracked = set(
        subprocess.check_output(["git", "ls-files", "-z"], cwd=source).decode("utf-8").split("\0")
    ) - {""}
    hashes = value.get("source_hashes", {})
    require(set(hashes) == tracked, "Independent source inventory is incomplete")
    for relative, digest in hashes.items():
        require(sha(safe_file(source, relative)) == digest, "Reviewed source changed: " + relative)
    required = {
        "code": {
            p
            for p in tracked
            if p.startswith(
                (
                    "backend/app/services/llm/",
                    "backend/app/api/model_routing",
                    "desktop/frontend/src/ModelRouting",
                )
            )
        },
        "runtime": {
            p
            for p in tracked
            if p.startswith(
                (
                    "backend/app/services/agent_service",
                    "backend/app/services/workflows/",
                    "backend/app/services/realtime_voice/",
                    "backend/app/services/scheduling/",
                )
            )
        },
        "security": {
            p
            for p in tracked
            if p.startswith(
                (
                    "backend/app/services/llm/routing/",
                    "backend/app/api/model_routing",
                    "desktop/electron/api-config",
                    "backend/app/services/attachment_service",
                )
            )
        },
        "qa": {
            p
            for p in tracked
            if p.startswith(
                (
                    "scripts/review-all-skills",
                    "scripts/unified_review_contracts",
                    "scripts/unified_feature_evidence",
                    "scripts/collect_benchmark_evidence",
                    "backend/tests/test_unified",
                    "tests/ui/model-router",
                    "docs/SKILL_01_10_FEATURES",
                )
            )
        }
        | {
            "scripts/visual_review_evidence.py",
            "scripts/build-companion-studio.ps1",
            "scripts/test-installer.ps1",
        },
    }
    reviews = value.get("reviews", {})
    require(set(reviews) >= set(required), "Four independent review domains required")
    for domain, coverage in required.items():
        item = reviews[domain]
        report_path = safe_file(source, item["report"]["path"])
        require(sha(report_path) == item["report"]["sha256"], "Independent domain report changed")
        report = read(report_path)
        require(
            report.get("domain") == domain
            and report.get("status") == "PASS"
            and report.get("source_revision") == revision
            and report.get("build_id") == build_id
            and report.get("unresolved_findings") == [],
            "Domain status/identity mismatch",
        )
        reviewed = report.get("source_hashes", {})
        require(
            coverage <= reviewed.keys()
            and all(hashes.get(p) == digest for p, digest in reviewed.items()),
            "Required domain source coverage missing",
        )
        commands = report.get("commands", [])
        require(commands, "Independent domain lacks executed commands")
        for command in commands:
            require(
                command.get("command")
                and command.get("exit_code") == 0
                and command.get("evidence"),
                "Independent command lacks successful evidence",
            )
            for proof in command["evidence"]:
                require(
                    sha(safe_file(source, proof["path"])) == proof["sha256"],
                    "Independent command evidence mismatch",
                )
    return value


def feature_result(item, rows):
    selectors = item.get("direct_test_ids", [])
    matched = [row for row in rows if row["id"] in selectors]
    covered = {row["id"] for row in matched}
    if not selectors or covered != set(selectors):
        return "PARTIAL" if rows else "NOT_RUN", matched
    if any(row["status"] == "FAIL" for row in matched):
        return "FAIL", matched
    if any(row["status"] != "PASS" for row in matched):
        return "PARTIAL", matched
    return "PASS", matched
