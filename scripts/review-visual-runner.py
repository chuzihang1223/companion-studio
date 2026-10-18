"""Run original checks, build and installed GUI with identity-bound evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from visual_review_evidence import Evidence, read, require, sha
from visual_review_validation import validate


class Review(Evidence):
    def __init__(self, args):
        self.source = Path(__file__).resolve().parents[1]
        self.root = Path(args.evidence).resolve()
        require(
            self.root.is_relative_to(self.source / ".cache"),
            "Evidence must be inside this source .cache",
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.delivery = Path(args.build_root).resolve() / args.build_id / "delivery"
        self.build_id = args.build_id
        self.profile = Path(args.protected_profile).resolve() if args.protected_profile else None
        self.node = shutil.which("node.exe")
        require(bool(self.node), "Node.js is required")
        self.initialize_evidence()

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
            or any(
                part.is_symlink() or part.is_junction()
                for part in (self.profile, *self.profile.parents)
            )
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
                if read(before) != self.snapshot():
                    raise ValueError("Protected profile changed; retained original baseline")
            else:
                before.write_text(json.dumps(self.snapshot()), encoding="utf-8")
            self.bind_profile()
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
                "backend/tests/test_visual_review_evidence.py",
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
        base = home
        attempt = 1
        while home.exists():
            attempt += 1
            home = base.with_name(base.name + f"-{attempt}")
        self.report.setdefault("gui_evidence", {})["installed" if installed else "source"] = str(
            home.relative_to(self.source)
        )
        exe = (
            self.delivery / "win-unpacked/CompanionStudio.exe"
            if installed
            else self.source / "desktop/node_modules/electron/dist/electron.exe"
        )
        # After installer testing, exercise its hash-verified retained copy.
        if installed:
            exe = self.installed_program() / "CompanionStudio.exe"
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
        # Keep nested NSIS/runtime resources below classic Windows path limits.
        # Identity remains in the report and manifest, independent of folder names.
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
        exe = self.installed_program() / "CompanionStudio.exe"
        for name, filename in [
            ("trace", "trace-viewer-e2e.cjs"),
            ("evaluation", "evaluation-e2e.cjs"),
            ("recovery", "recovery-e2e.cjs"),
            ("extensions", "extensions-e2e.cjs"),
            ("workflow", "workflow-e2e.cjs"),
            ("knowledge", "knowledge-e2e.cjs"),
        ]:
            home = self.root / ("regression-" + name)
            base, attempt = home, 1
            while home.exists():
                attempt += 1
                home = base.with_name(base.name + f"-{attempt}")
            self.report.setdefault("regression_evidence", {})[name] = str(
                home.relative_to(self.source)
            )
            self.step(
                "B",
                "installed-" + name,
                [self.node, "tests/ui/" + filename],
                env={
                    "COMPANION_TEST_SOURCE": "",
                    "COMPANION_TEST_EXE": str(exe),
                    "COMPANION_TEST_HOME": str(home),
                },
            )

    def final(self):
        self.step(
            "A",
            "independent-review",
            [
                sys.executable,
                "scripts/review-visual-runner.py",
                "--validate-independent-review",
                str(self.root / "independent-review.json"),
            ],
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
                str(self.profile or ""),
            ],
        )


def main():
    require(
        sys.flags.optimize == 0, "Optimized Python is not supported for QA; unset PYTHONOPTIMIZE"
    )
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
    parser.add_argument("--validate-independent-review")
    parser.add_argument("--validate-consistency")
    args = parser.parse_args()
    if validate(args, Review):
        return
    require(
        args.phase not in {"all", "final"} or bool(args.protected_profile),
        "--protected-profile is required for full/final review",
    )
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
