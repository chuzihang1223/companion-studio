"""Reuse original audited build/install/evidence machinery for Skill08 Gates A–J."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

from visual_review_evidence import read, require
from scheduler_review_validation import (
    GATES,
    full_results,
    gui,
    performance,
    independent,
    checked_profile,
)

# Import the hyphenated original runner without changing its implementation or
# making a second installer/resource auditor.
import importlib.util

spec = importlib.util.spec_from_file_location(
    "studio_visual_review", Path(__file__).with_name("review-visual-runner.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SchedulerReview(module.Review):
    required_gates = GATES
    shared_gate_evidence = True

    def check(self):
        before = self.root / "profile-before.json"
        require(self.profile is not None, "Protected public UserData is required")
        current = self.snapshot()
        if before.exists():
            require(read(before) == current, "Protected baseline changed")
        else:
            before.write_text(json.dumps(current), encoding="utf-8")
        self.bind_profile()
        self.step(
            "B",
            "full-check",
            self.ps("scripts/check.ps1"),
            verify=lambda: full_results(self.source, self.root),
        )
        self.step(
            "B",
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
            "H",
            "performance",
            [
                sys.executable,
                "scripts/review-scheduler-runner.py",
                "--validate-performance",
                str(self.root / "scheduler-performance.json"),
            ],
        )

    def gui(self, installed=False):
        home = self.root / ("installed-scheduler" if installed else "source-scheduler")
        base, attempt = home, 1
        while home.exists():
            attempt += 1
            home = base.with_name(base.name + f"-{attempt}")
        executable = (
            self.installed_program() / "CompanionStudio.exe"
            if installed
            else self.source / "desktop/node_modules/electron/dist/electron.exe"
        )
        label = "installed-scheduler" if installed else "source-scheduler"
        self.report.setdefault("gui_evidence", {})[label] = str(home.relative_to(self.source))
        self.step(
            "G" if installed else "D",
            label,
            [self.node, "tests/ui/scheduler-e2e.cjs"],
            env={
                "COMPANION_TEST_SOURCE": "" if installed else "1",
                "COMPANION_TEST_EXE": str(executable),
                "COMPANION_TEST_HOME": str(home),
                "COMPANION_SCHEDULER_TEST_PORT": "8796",
            },
            verify=lambda: gui(home),
        )

    def installer(self):
        token = hashlib.sha256(self.build_id.encode()).hexdigest()[:12]
        test = self.source / ".cache/installer-tests" / token
        self.step(
            "I",
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
            "I",
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
        executable = self.installed_program() / "CompanionStudio.exe"
        for key, filename in [
            ("trace", "trace-viewer-e2e.cjs"),
            ("evaluation", "evaluation-e2e.cjs"),
            ("recovery", "recovery-e2e.cjs"),
            ("extensions", "extensions-e2e.cjs"),
            ("workflow", "workflow-e2e.cjs"),
            ("knowledge", "knowledge-e2e.cjs"),
            ("computer", "computer-use-e2e.cjs"),
        ]:
            home = self.root / ("regression-" + key)
            base, attempt = home, 1
            while home.exists():
                attempt += 1
                home = base.with_name(base.name + f"-{attempt}")
            self.report.setdefault("regression_evidence", {})[key] = str(
                home.relative_to(self.source)
            )

            def verify(folder=home):
                report = read(folder / "report.json")
                require(
                    report.get("result") == "PASS"
                    and report["realModelRequests"] == report["realVoiceRequests"] == 0,
                    "Installed regression failed",
                )

            self.step(
                "A",
                "installed-" + key,
                [self.node, "tests/ui/" + filename],
                env={
                    "COMPANION_TEST_SOURCE": "",
                    "COMPANION_TEST_EXE": str(executable),
                    "COMPANION_TEST_HOME": str(home),
                },
                verify=verify,
            )

    def final(self):
        self.step(
            "J",
            "independent-review",
            [
                sys.executable,
                "scripts/review-scheduler-runner.py",
                "--validate-independent-review",
                str(self.root / "independent-review.json"),
            ],
        )
        self.step(
            "I",
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
            "I",
            "manifest-profile",
            [
                sys.executable,
                "scripts/review-scheduler-runner.py",
                "--phase",
                "verify-profile",
                "--evidence",
                str(self.root),
                "--build-root",
                str(self.delivery.parent.parent),
                "--build-id",
                self.build_id,
                "--protected-profile",
                str(self.profile),
            ],
        )


def main():
    require(sys.flags.optimize == 0, "Unset PYTHONOPTIMIZE; QA requires nonoptimized checks")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=["all", "check", "source-gui", "build", "installer", "final", "verify-profile"],
        default="all",
    )
    parser.add_argument("--evidence", default=".cache/scheduler-review")
    parser.add_argument("--build-root", default="build/scheduler-review")
    parser.add_argument("--build-id", default="studio-0.2.0-scheduler.1-review")
    parser.add_argument("--protected-profile")
    parser.add_argument("--validate-performance")
    parser.add_argument("--validate-independent-review")
    args = parser.parse_args()
    if args.validate_performance:
        performance(Path(args.validate_performance))
        return
    if args.validate_independent_review:
        independent(Path(__file__).resolve().parents[1], Path(args.validate_independent_review))
        return
    review = SchedulerReview(args)
    if args.phase == "verify-profile":
        checked_profile(review)
        return
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
