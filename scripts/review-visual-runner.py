"""Run existing checks/build/installer without a second release system.

This runner only owns new QA profiles/fixtures. No remote Git operations or
model credentials. Report command history, not success inferred from prose.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


class Review:
    def __init__(self, args):
        self.source = Path(__file__).resolve().parents[1]
        self.root = Path(args.evidence).resolve()
        if not self.root.is_relative_to(self.source / ".cache"):
            raise ValueError("Evidence must be inside this source .cache")
        self.root.mkdir(parents=True, exist_ok=True)
        self.delivery = Path(args.build_root).resolve() / args.build_id / "delivery"
        self.build_id = args.build_id
        self.profile = Path(args.protected_profile).resolve() if args.protected_profile else None
        self.node = shutil.which("node.exe")
        if not self.node:
            raise ValueError("Node.js is required")
        self.path = self.root / "automated-review.json"
        self.report = (
            read(self.path)
            if self.path.exists()
            else {"schema_version": 1, "steps": [], "limitations": []}
        )

    def save(self):
        required = {
            "A": {"source-history-audit"},
            "B": {"full-check"},
            "C": {"computer-security-tests", "computer-main-security"},
            "D": {"source-gui", "installed-gui"},
            "E": {"source-performance", "installed-performance"},
            "F": {"build", "installer-dryrun", "installer"},
            "G": {"release-audit", "manifest-profile"},
        }
        gates = []
        for gate in "ABCDEFG":
            latest = {
                row.get("label", row["evidence_path"]): row
                for row in self.report["steps"]
                if row["gate_id"] == gate
            }
            steps = list(latest.values())
            gates.append(
                {
                    "gate_id": gate,
                    "status": "FAIL"
                    if any(row["status"] == "FAIL" for row in steps)
                    else "PASS"
                    if required[gate].issubset(latest)
                    else "NOT_RUN",
                }
            )
        self.report.update(gates=gates, updated_at=stamp(), verdict="INCOMPLETE")
        if any(row["status"] == "FAIL" for row in gates):
            self.report["verdict"] = "BLOCKED"
        elif all(row["status"] == "PASS" for row in gates):
            self.report["verdict"] = "READY_FOR_RELEASE_REVIEW"
        self.path.write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def step(self, gate, label, command, *, env=None, verify=None):
        name = f"{len(self.report['steps']) + 1:02d}-{label}"
        log = self.root / (name + ".log")
        row = {
            "gate_id": gate,
            "label": label,
            "status": "NOT_RUN",
            "command": command,
            "started_at": stamp(),
            "completed_at": None,
            "exit_code": None,
            "evidence_path": str(log.relative_to(self.source)),
            "findings": [],
        }
        print(f"Starting {gate}: {label}", flush=True)
        started = time.perf_counter()
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                command,
                cwd=self.source,
                env={**os.environ, **(env or {})},
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
        row.update(
            completed_at=stamp(),
            exit_code=result.returncode,
            duration_ms=(time.perf_counter() - started) * 1000,
            status="PASS" if result.returncode == 0 else "FAIL",
        )
        if result.returncode == 0 and verify:
            try:
                verify()
            except Exception as error:
                row["status"] = "FAIL"
                row["findings"].append(str(error))
        self.report["steps"].append(row)
        self.save()
        print(f"{row['status']} {gate}: {label}", flush=True)
        if row["status"] != "PASS":
            raise RuntimeError(f"Gate {gate} failed; {log.name}")

    def ps(self, script, *args):
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
            *map(str, args),
        ]

    def snapshot(self):
        # Hash only: never open SQLite or import the formal profile into QA.
        if not self.profile:
            raise ValueError("Provide the public product's protected profile for final data proof")
        if (
            self.profile.name != "UserData"
            or not self.profile.is_dir()
            or self.profile.is_symlink()
            or self.profile.is_junction()
        ):
            raise ValueError("Protected profile must be an existing UserData directory")
        output = {}
        for path in self.profile.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise ValueError("Protected profile contains a filesystem link")
            if path.is_file():
                output[path.relative_to(self.profile).as_posix()] = sha(path)
        return output

    def check(self):
        if self.profile:
            before = self.root / "profile-before.json"
            if before.exists():
                raise ValueError("Use new evidence root; do not replace profile baseline")
            before.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        self.step("B", "full-check", self.ps("scripts/check.ps1"))
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
        self.step(
            "C",
            "computer-security-tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "backend/tests/test_computer_use_contracts.py",
                "backend/tests/test_computer_use_runtime.py",
                "backend/tests/test_computer_use_api_evaluation.py",
                "backend/tests/test_computer_use_lifecycle.py",
                "-q",
                "--basetemp",
                str(self.root / "security-pytest"),
                "--junitxml",
                str(self.root / "security-pytest.xml"),
            ],
        )
        self.step("C", "computer-main-security", [self.node, "tests/ui/computer-use-unit.cjs"])

    def gui(self, installed=False):
        home = self.root / ("installed-gui" if installed else "source-gui")
        exe = (
            self.delivery / "win-unpacked/CompanionStudio.exe"
            if installed
            else self.source / "desktop/node_modules/electron/dist/electron.exe"
        )
        # After installer testing, exercise its hash-verified retained copy.
        if installed:
            report = read(self.root / "installer-result.json")
            exe = Path(report["installed_snapshot"]["program"]) / "CompanionStudio.exe"
        self.step(
            "D",
            "installed-gui" if installed else "source-gui",
            [self.node, "tests/ui/computer-use-e2e.cjs"],
            env={
                "COMPANION_TEST_SOURCE": "" if installed else "1",
                "COMPANION_TEST_EXE": str(exe),
                "COMPANION_TEST_HOME": str(home),
            },
            verify=lambda: self.verify_gui(home),
        )
        self.step(
            "E",
            "installed-performance" if installed else "source-performance",
            [
                sys.executable,
                "scripts/review-visual-runner.py",
                "--validate-performance",
                str(home / "performance.json"),
            ],
        )

    def verify_gui(self, home):
        report, benchmark = read(home / "report.json"), read(home / "computer-benchmark.json")
        if (
            report.get("result") != "PASS"
            or report["realModelRequests"]
            or report["realVoiceRequests"]
        ):
            raise ValueError("GUI evidence is not a local-only success")
        blocked = [
            {"case_id": row["case_id"], "code": row["error_code"]}
            for row in benchmark["results"]
            if row["execution_status"] == "BLOCKED"
        ]
        self.report["limitations"] = blocked + [
            {
                "test": "real-vision",
                "status": "NOT_RUN",
                "reason": "no paid/private-image authorization",
            },
            {"test": "code-signing", "status": "BLOCKED", "reason": "no signing certificate"},
        ]

    def build(self):
        self.step(
            "F",
            "build",
            self.ps(
                "scripts/build-companion-studio.ps1",
                "-BuildId",
                self.build_id,
                "-OutputRoot",
                self.delivery.parent.parent,
            ),
        )

    def installer(self):
        test = self.source / ".cache/installer-tests" / (self.build_id + "-qa")
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
        self.gui(installed=True)

    def final(self):
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
                "--validate-consistency",
                str(self.delivery / "build-manifest.json"),
                "--evidence",
                str(self.root),
                "--protected-profile",
                str(self.profile or ""),
            ],
        )


def validate(args):
    if args.validate_performance:
        value = read(Path(args.validate_performance))
        assert value["frames"] > 10 and value["frame_gap_p95_ms"] > 0
        assert value["memory_before"] and value["memory_after"]
        assert len(value["native_cases"]) == 24
        assert any(row.get("metrics", {}).get("uia_ms", 0) > 0 for row in value["native_cases"])
        print(
            json.dumps(
                {"status": "PASS", "measured_frames": value["frames"], "stable_fps_claim": False}
            )
        )
        return True
    if args.validate_consistency:
        source = Path(__file__).resolve().parents[1]
        manifest = read(Path(args.validate_consistency))
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        assert manifest["build"]["source_revision"] == commit
        for name, digest in manifest["source_hashes"].items():
            path = (source / name).resolve()
            assert path.is_relative_to(source) and sha(path) == digest
        review = Review(args)
        before = read(review.root / "profile-before.json")
        after = review.snapshot()
        assert before == after, "Formal profile file hashes changed"
        (review.root / "profile-protection.json").write_text(
            json.dumps({"status": "PASS", "files": len(after), "unchanged": True}), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "source_hashes": len(manifest["source_hashes"]),
                    "profile_files": len(after),
                }
            )
        )
        return True
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=["all", "check", "source-gui", "build", "installer", "final"],
        default="all",
    )
    parser.add_argument("--evidence", default=".cache/visual-qa")
    parser.add_argument("--build-root", default="build/visual-qa")
    parser.add_argument("--build-id", default="studio-0.2.0-visual.1-review")
    parser.add_argument("--protected-profile")
    parser.add_argument("--validate-performance")
    parser.add_argument("--validate-consistency")
    args = parser.parse_args()
    if validate(args):
        return
    review = Review(args)
    for phase, method in [
        ("check", review.check),
        ("source-gui", review.gui),
        ("build", review.build),
        ("installer", review.installer),
        ("final", review.final),
    ]:
        if args.phase in {"all", phase}:
            method()
    review.save()
    print(json.dumps({"status": review.report["verdict"], "report": str(review.path)}))


if __name__ == "__main__":
    main()
