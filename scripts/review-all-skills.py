"""Skill01–10 evidence orchestration; original checks/build/installer remain authoritative."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

from visual_review_evidence import read, require, sha, source_identity, needs_artifact
from collect_benchmark_evidence import collect as collect_benchmarks
from unified_feature_evidence import feature_matrix
from unified_review_contracts import (
    verify_gui,
    verify_router,
    verify_integration,
    verify_independent,
    allowed_skip,
)

spec = importlib.util.spec_from_file_location(
    "original_visual_qa", Path(__file__).with_name("review-visual-runner.py")
)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)

GUI = {
    "trace": "trace-viewer-e2e.cjs",
    "evaluation": "evaluation-e2e.cjs",
    "recovery": "recovery-e2e.cjs",
    "extensions": "extensions-e2e.cjs",
    "workflow": "workflow-e2e.cjs",
    "knowledge": "knowledge-e2e.cjs",
    "scheduler": "scheduler-e2e.cjs",
    "voice": "realtime-voice-e2e.cjs",
    "router": "model-router-e2e.cjs",
    "workbench": "installed-studio-smoke.cjs",
}
DOMAINS = {
    "01": ("trace",),
    "02": ("evaluation",),
    "03": ("recovery",),
    "04": ("mcp", "skills", "extensions"),
    "05": ("workflow",),
    "06": ("knowledge", "memory"),
    "07": ("computer_use", "visual_review"),
    "08": ("scheduler", "scheduling"),
    "09": ("realtime_voice", "voice_agent", "voice_completion"),
    "10": ("model_router",),
}


def junit(path):
    root = ET.parse(path).getroot()
    return [
        {
            "id": row.get("classname", "") + "::" + row.get("name", ""),
            "status": "FAIL"
            if row.find("failure") is not None or row.find("error") is not None
            else "BLOCKED"
            if row.find("skipped") is not None
            else "PASS",
            "seconds": float(row.get("time", 0)),
            "skip_reason": row.find("skipped").get("message", "")
            if row.find("skipped") is not None
            else None,
        }
        for row in root.iter("testcase")
    ]


def group(rows, keys):
    return [row for row in rows if any(key in row["id"].split("::")[0] for key in keys)]


def outcome(rows):
    return (
        "NOT_RUN"
        if not rows
        else "FAIL"
        if any(r["status"] == "FAIL" for r in rows)
        else "NOT_RUN"
        if any(r["status"] in {"NOT_RUN", "PENDING"} for r in rows)
        else "PARTIAL"
        if any(r["status"] != "PASS" for r in rows)
        else "PASS"
    )


class UnifiedReview(original.Review):
    def fresh(self, label):
        home = self.root / label
        index = 1
        while home.exists():
            index += 1
            home = self.root / (label + "-" + str(index))
        return home

    def check(self):
        before = self.root / "profile-before.json"
        current = self.snapshot()
        if before.exists():
            require(read(before) == current, "Protected profile changed; baseline retained")
        else:
            before.write_text(json.dumps(current), encoding="utf-8")
        self.bind_profile()
        attempt = self.fresh("source-check")
        attempt.mkdir()

        def retain():
            src = self.source / ".cache/checks"
            for name in ("pytest.xml", "source-audit.json", "history-audit.json"):
                shutil.copyfile(src / name, attempt / name)
            rows = junit(attempt / "pytest.xml")
            require(
                rows and not any(row["status"] == "FAIL" for row in rows),
                "Current regression has failures",
            )
            collect_benchmarks(
                self.source,
                src / "pytest-temp",
                attempt / "benchmarks",
                self.identity["source_revision"],
            )
            router_reports = list((src / "pytest-temp").rglob("router-result.json"))
            require(len(router_reports) == 1, "Current original worker report missing or ambiguous")
            verify_router(self.source, router_reports[0], self.identity["source_revision"])
            shutil.copyfile(router_reports[0], attempt / "router-result.json")
            self.report["current_test_evidence"] = str(attempt.relative_to(self.source))
            self.report["current_test_binding"] = {
                "identity": self.identity,
                "files": {
                    str(path.relative_to(self.source)): sha(path)
                    for path in attempt.rglob("*")
                    if path.is_file()
                },
            }

        self.step("B", "full-check", self.ps("scripts/check.ps1"), verify=retain)
        self.step(
            "A",
            "source-history-audit",
            [
                sys.executable,
                "scripts/audit-release.py",
                "--source",
                str(self.source),
                "--report",
                str(self.root / "source-history-audit.json"),
            ],
        )
        self.step("U", "dependency-consistency", [sys.executable, "-m", "pip", "check"])

    def gui(self, installed=False):
        # The original computer harness owns native UIA/OCR/permission tests and
        # its performance artifact. Never replace those with a simulated report.
        super().gui(installed)
        native_home = (
            self.source / self.report["gui_evidence"]["installed" if installed else "source"]
        )
        label = "installed-gui" if installed else "source-gui"
        self.report.setdefault("gui_bindings", {})[label] = {
            "identity": self.identity,
            "files": {
                str(path.relative_to(self.source)): sha(path)
                for path in (
                    native_home / "report.json",
                    native_home / "computer-benchmark.json",
                    native_home / "performance.json",
                )
            },
        }
        exe = (
            self.installed_program() / "CompanionStudio.exe"
            if installed
            else self.source / "desktop/node_modules/electron/dist/electron.exe"
        )
        voice = self.root / "native-voice"
        if not voice.exists():
            self.step(
                "U",
                "native-voice",
                [sys.executable, "scripts/voice_native_probe.py", "--output", str(voice)],
            )
        for name, filename in GUI.items():
            label = ("installed-" if installed else "source-") + name
            home = self.fresh(label)

            def verify(home=home, name=name):
                verify_gui(home, name)
                self.report.setdefault("gui_bindings", {})[label] = {
                    "identity": self.identity,
                    "files": {
                        str((home / "report.json").relative_to(self.source)): sha(
                            home / "report.json"
                        )
                    },
                }

            self.report.setdefault("unified_gui", {})[label] = str(home.relative_to(self.source))
            self.step(
                "U",
                label,
                [self.node, "tests/ui/" + filename],
                env={
                    "COMPANION_TEST_SOURCE": "" if installed else "1",
                    "COMPANION_TEST_EXE": str(exe),
                    "COMPANION_TEST_HOME": str(home),
                    "COMPANION_VOICE_FIXTURE": str(voice / "public-synthetic-1.wav"),
                },
                verify=verify,
            )

    def installer(self):
        token = hashlib.sha256(self.build_id.encode()).hexdigest()[:12]
        test = self.source / ".cache/installer-tests" / token
        self.step(
            "F",
            "installer-dryrun",
            self.ps(
                "scripts/test-installer.ps1",
                "-Delivery",
                self.delivery,
                "-TestRoot",
                str(test) + "-dry",
                "-DryRun",
            ),
        )
        self.step(
            "F",
            "installer",
            self.ps(
                "scripts/test-installer.ps1",
                "-Delivery",
                self.delivery,
                "-TestRoot",
                test,
                "-RunUI",
                "-PreserveInstalledSnapshot",
            ),
        )
        shutil.copyfile(test / "installer-test-report.json", self.root / "installer-result.json")
        self.report["installer_binding"] = {
            "identity": self.identity,
            "artifact_key": self.current_artifact_key(),
        }
        self.save()
        self.gui(installed=True)

    def independent(self):
        return verify_independent(
            self.source,
            self.root / "independent-review.json",
            self.identity["source_revision"],
            self.build_id,
        )

    def final(self):
        self.independent()
        self.step(
            "U",
            "independent-current-source",
            [
                sys.executable,
                "-c",
                "print('Current independent review binding validated by parent runner')",
            ],
            verify=self.independent,
        )
        self.step(
            "G",
            "release-audit",
            [
                sys.executable,
                "scripts/audit-release.py",
                "--source",
                str(self.source),
                "--delivery",
                str(self.delivery),
                "--report",
                str(self.root / "release-audit.json"),
            ],
        )
        self.step(
            "G",
            "manifest-profile",
            [
                sys.executable,
                "scripts/review-visual-runner.py",
                "--build-id",
                self.build_id,
                "--build-root",
                str(self.delivery.parent.parent),
                "--validate-consistency",
                str(self.delivery / "build-manifest.json"),
                "--evidence",
                str(self.root),
                "--protected-profile",
                str(self.profile),
            ],
        )

    def summarize(self):
        # Select the latest attempt of each label, retaining a newer failure.
        latest = {}
        try:
            artifact = self.current_artifact_key()
        except (OSError, KeyError, ValueError):
            artifact = None
        current = source_identity(self.source, self.build_id, self.profile)
        for row in self.report["steps"]:
            if row.get("identity") == self.identity:
                selected = dict(row)
                if (
                    current != self.identity
                    or needs_artifact(row["label"])
                    and (not artifact or row.get("artifact_key") != artifact)
                ):
                    selected["status"] = "NOT_RUN"
                latest[row["label"]] = selected
        for label, binding in self.report.get("gui_bindings", {}).items():
            if label in latest and latest[label]["status"] == "PASS":
                try:
                    require(
                        binding["identity"] == self.identity
                        and all(
                            sha(self.source / relative) == digest
                            for relative, digest in binding["files"].items()
                        ),
                        "GUI evidence changed",
                    )
                except (OSError, ValueError):
                    latest[label]["status"] = "FAIL"
        test_root = self.report.get("current_test_evidence")
        if latest.get("full-check", {}).get("status") == "PASS":
            binding = self.report.get("current_test_binding", {})
            try:
                require(
                    binding.get("identity") == self.identity
                    and binding.get("files")
                    and all(
                        sha(self.source / relative) == digest
                        for relative, digest in binding["files"].items()
                    ),
                    "Full-check evidence missing or changed",
                )
            except (OSError, ValueError):
                latest["full-check"]["status"] = "FAIL"
        rows = junit(self.source / test_root / "pytest.xml") if test_root else []
        if latest.get("full-check", {}).get("status") != "PASS":
            rows = []  # stale or failed full-check evidence is never feature proof
        features = read(self.source / "docs/SKILL_01_10_FEATURES.json")
        matrix = feature_matrix(self, features, rows, latest, test_root)

        def labels(*names):
            selected = [latest[name] for name in names if name in latest]
            return "NOT_RUN" if len(selected) != len(names) else outcome(selected)

        integration = [row for row in rows if "::test_INT_" in row["id"]]
        try:
            verify_integration(integration)
            integration_status = "PASS"
        except ValueError:
            integration_status = "FAIL" if integration else "NOT_RUN"
        try:
            require(
                bool(test_root) and latest.get("full-check", {}).get("status") == "PASS",
                "No current full-check",
            )
            verify_router(
                self.source,
                self.source / test_root / "router-result.json",
                self.identity["source_revision"],
            )
            router_status = "PASS"
        except (OSError, KeyError, ValueError):
            router_status = "NOT_RUN"

        gates = [
            ("01", "Repository/history", labels("source-history-audit")),
            (
                "02",
                "Dependency/license/advisory",
                "PARTIAL"
                if labels("dependency-consistency", "build") == "PASS"
                else labels("dependency-consistency", "build"),
            ),
            ("03", "Python", labels("full-check")),
            ("04", "Frontend/Electron", labels("full-check")),
            (
                "05",
                "Skills01–03",
                outcome(group(rows, sum((DOMAINS[s] for s in ("01", "02", "03")), ()))),
            ),
            (
                "06",
                "Skills04–06",
                outcome(group(rows, sum((DOMAINS[s] for s in ("04", "05", "06")), ()))),
            ),
            (
                "07",
                "Skills07–09",
                outcome(group(rows, sum((DOMAINS[s] for s in ("07", "08", "09")), ()))),
            ),
            ("08", "Router", router_status),
            (
                "09",
                "Cross-skill INT001–016",
                integration_status,
            ),
            ("10", "Security/privacy", labels("full-check", "independent-current-source")),
            ("11", "Database upgrade", outcome(group(rows, ("migration", "scheduler_storage")))),
            (
                "12",
                "Source+installed GUI",
                labels(
                    "source-gui",
                    "installed-gui",
                    *[prefix + name for prefix in ("source-", "installed-") for name in GUI],
                ),
            ),
            (
                "13",
                "Performance",
                labels("source-performance", "installed-performance", "native-voice"),
            ),
            ("14", "Build/install/uninstall", labels("build", "installer-dryrun", "installer")),
            (
                "15",
                "Source/resources/profile consistency",
                labels("release-audit", "manifest-profile"),
            ),
            ("16", "Independent review", labels("independent-current-source")),
        ]
        unexpected_skips = [
            row
            for row in rows
            if row["status"] != "PASS" and row["status"] != "FAIL" and not allowed_skip(row)
        ]
        feature_gaps = [
            row
            for row in matrix
            if row["status"] != "PASS" and not row["accepted_environment_limit"]
        ]
        allowed_partial_gates = {"02", "06"} if not unexpected_skips else {"02"}
        verdict = (
            "BLOCKED"
            if any(state == "FAIL" for _, _, state in gates)
            else "INCOMPLETE"
            if feature_gaps
            or unexpected_skips
            or any(
                state == "NOT_RUN" or state == "PARTIAL" and key not in allowed_partial_gates
                for key, _, state in gates
            )
            else "VERIFIED_WITH_LIMITATIONS"
        )
        gates.append(
            (
                "17",
                "Final sign-off",
                "PASS"
                if verdict == "VERIFIED_WITH_LIMITATIONS"
                else "FAIL"
                if verdict == "BLOCKED"
                else "NOT_RUN",
            )
        )
        now = datetime.now(timezone.utc).isoformat()
        report = {
            "audit_version": 1,
            "source_commit": self.identity["source_revision"],
            "build_id": self.build_id,
            "version": self.identity["version"],
            "created_at": now,
            "verdict": verdict,
            "gates": [
                {
                    "gate": key,
                    "gate_id": "GATE-" + key,
                    "skill_id": "ALL",
                    "test_id": "GATE-" + key,
                    "test": name,
                    "status": status,
                    "evidence": "automated-review.json",
                    "time": now,
                    "error": None
                    if status == "PASS"
                    else "See current step evidence and limitations",
                    "severity": "P1" if status == "FAIL" else None,
                    "reviewer": "unified-runner",
                }
                for key, name, status in gates
            ],
            "datasets": [
                {
                    "dataset_id": path.stem,
                    "case_count": len(read(path)["cases"]),
                    "sha256": sha(path),
                }
                for path in sorted(
                    (self.source / "backend/app/services/evaluation/datasets").glob("*-core.json")
                )
            ],
            "tests": rows,
            "features": matrix,
            "limitations": [
                "Installer unsigned",
                "No authenticated advisory database scan: dependency consistency is not a CVE scan",
                "No physical microphone/AEC/automatic barge-in acceptance",
                "No real external provider billing/quality or Fish/IndexTTS acceptance",
                "GUI mock boundaries and native SAPI recognition failures remain recorded",
            ],
            "steps": [
                {
                    k: row.get(k)
                    for k in (
                        "label",
                        "status",
                        "started_at",
                        "completed_at",
                        "duration_ms",
                        "exit_code",
                        "findings",
                    )
                }
                for row in latest.values()
            ],
        }
        (self.root / "UNIFIED_ACCEPTANCE_01_10.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# Skill 01–10 current source matrix",
            "",
            "Module test counts overlap and must not be added as unique tests. Boundaries are retained in each feature note.",
            "",
            "| Skill | Feature | Status | Module tests passed/executed |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {row['skill']} | {row['name']} | {row['status']} | {row['tests_passed']}/{row['tests_executed']} |"
            for row in matrix
        ]
        (self.root / "SKILL_01_10_MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("all", "check", "source-gui", "build", "installer", "final"),
        default="all",
    )
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--protected-profile", required=True)
    args = parser.parse_args()
    require(sys.flags.optimize == 0, "Unset PYTHONOPTIMIZE for QA")
    review = UnifiedReview(args)
    require(
        not review.identity["dirty"], "Commit reviewed source before identity-bound candidate QA"
    )
    try:
        for phase, method in (
            ("check", review.check),
            ("source-gui", review.gui),
            ("build", review.build),
            ("installer", review.installer),
            ("final", review.final),
        ):
            if args.phase in {"all", phase}:
                method()
    finally:
        result = review.summarize()
        print(
            json.dumps(
                {
                    "verdict": result["verdict"],
                    "report": str(review.root / "UNIFIED_ACCEPTANCE_01_10.json"),
                }
            )
        )
    if args.phase in {"all", "final"} and result["verdict"] not in {
        "FULLY_VERIFIED",
        "VERIFIED_WITH_LIMITATIONS",
    }:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
