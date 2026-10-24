"""Fail-closed additions to the original unified release evidence contracts."""

from datetime import datetime, timezone
import json

from visual_review_evidence import read, require, sha


def dependency_status(source, home, revision):
    report = read(home / "dependency-security.json")
    require(report["source_revision"] == revision, "Advisory evidence belongs to another revision")
    age = (
        datetime.now(timezone.utc) - datetime.fromisoformat(report["queried_utc"])
    ).total_seconds()
    require(0 <= age <= 72 * 3600, "Advisory evidence is stale or future-dated")
    required = {
        "backend/requirements.lock",
        "backend/requirements-dev.txt",
        "desktop/package-lock.json",
    }
    require(set(report["lock_hashes"]) == required, "Advisory lock scope is incomplete")
    require(
        all(sha(source / path) == digest for path, digest in report["lock_hashes"].items()),
        "Dependencies changed after audit",
    )
    require(
        report.get("extra_audit_tool_installed") is False
        and report.get("private_source_or_profile_uploaded") is False,
        "Audit transmission boundary changed",
    )
    rows = report["python"]
    require(
        rows and len({row["name"] for row in rows}) == len(rows),
        "Python advisory scope is empty or duplicated",
    )
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    expected = {}
    for path in sorted(required - {"desktop/package-lock.json"}):
        for line in (source / path).read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith(("#", "-r")):
                continue
            requirement = Requirement(line)
            if not requirement.marker or requirement.marker.evaluate():
                expected[canonicalize_name(requirement.name)] = next(
                    item.version for item in requirement.specifier if item.operator == "=="
                )
    require(
        {row["name"]: row["version"] for row in rows if row["name"] != "pip"} == expected,
        "Not all active Python pins were scanned",
    )
    unavailable = any(row["status"] != "AVAILABLE" for row in rows)
    active = any(
        not advisory.get("withdrawn") for row in rows for advisory in row.get("advisories", [])
    )
    for label in ("npm-audit", "npm-sbom"):
        require(
            report[label].get("evidence_sha256") == sha(home / (label + ".json")),
            "npm evidence missing or changed",
        )
    audit = read(home / "npm-audit.json")
    require(
        "error" not in audit
        and audit.get("metadata", {}).get("dependencies", {}).get("total", 0) > 0,
        "npm audit did not scan a dependency graph",
    )
    require(sha(home / "sbom.cdx.json") == report["sbom_sha256"], "SBOM changed after audit")
    sbom = read(home / "sbom.cdx.json")
    require(
        sbom["bomFormat"] == "CycloneDX" and sbom.get("components") and sbom.get("dependencies"),
        "Combined SBOM is incomplete",
    )
    require(
        {(row["name"], row.get("version")) for row in sbom["components"]} >= set(expected.items()),
        "SBOM omits Python components",
    )
    license_inventory = read(source / "licenses/inventory.json")
    require(
        license_inventory.get("components")
        and all(
            item.get("license") and item.get("upstream") and item.get("files")
            for item in license_inventory["components"]
        ),
        "License/source inventory incomplete",
    )
    require(
        all(
            sha(source / file["path"]) == file["sha256"]
            for item in license_inventory["components"]
            for file in item["files"]
        ),
        "Third-party notice/source bytes changed",
    )
    failed = active or audit["metadata"]["vulnerabilities"]["total"] > 0
    if failed:
        return "FAIL"
    if unavailable or any(
        report[label].get("exit_code") != 0 for label in ("npm-audit", "npm-sbom")
    ):
        return "PARTIAL"
    return "PASS"


def soak_status(path, source=None):
    report = read(path)
    require(report.get("status") == "PASS", "Soak was interrupted, diagnostic-only or failed")
    require(
        report["requested_seconds"] >= 3600 and report["actual_seconds"] >= 3600,
        "Actual 60-minute duration not observed",
    )
    samples = report["samples"]
    require(
        len(samples) >= 180 and samples[-1]["elapsed_seconds"] >= 3590,
        "Soak samples are incomplete",
    )
    require(
        all(a["elapsed_seconds"] < b["elapsed_seconds"] for a, b in zip(samples, samples[1:])),
        "Soak clock moved backwards",
    )
    require(
        not report["sqlite_errors"]
        and not report["unexpected_async_errors"]
        and report["owned_live_children_at_close"] == 0,
        "Soak retained errors or owned children",
    )
    require(
        all(row["exit_code"] is not None for row in report["mcp_children"]),
        "Owned MCP exit was not observed",
    )
    require(
        all(
            not sample["owned_live_children"]
            and not sample["mcp_connections"]
            and not sample["workflow_tasks"]
            and not sample["trace_queue"]
            and not sample["trace_metrics"]["write_failures"]
            for sample in samples
        ),
        "Resources failed to drain at a quiescent boundary",
    )
    if source:
        expected = {
            path.relative_to(source).as_posix() for path in (source / "backend/app").rglob("*.py")
        } | {
            "backend/tests/" + name
            for name in (
                "hardening_soak_workload.py",
                "unified_skill_fixture.py",
                "unified_skill_scenarios.py",
                "workflow_fixture.py",
                "scheduler_fixture.py",
            )
        }
        require(
            report.get("source_files")
            and set(report["source_files"]) == expected
            and all(
                sha(source / path) == digest for path, digest in report["source_files"].items()
            ),
            "Soak exercised different source bytes",
        )
    return "PASS"


def public_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
