"""Run original Skill01–10 gates plus bounded advisory/soak evidence; never publish."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys

from hardening_evidence import public_json, soak_status
from visual_review_evidence import read, require, sha

spec = importlib.util.spec_from_file_location(
    "original_unified_review", Path(__file__).with_name("review-all-skills.py")
)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)


class FinalHardening(original.UnifiedReview):
    def stress(self):
        report = self.root / "soak.json"
        self.step(
            "H",
            "real-duration-soak",
            [
                sys.executable,
                "-m",
                "pytest",
                "backend/tests/hardening_soak_workload.py",
                "-q",
                "--basetemp",
                str(self.fresh("soak-temp")),
                "--junitxml",
                str(self.root / "soak.xml"),
            ],
            env={"HARDENING_SOAK_SECONDS": "3600", "HARDENING_SOAK_REPORT": str(report)},
            verify=lambda: soak_status(report, self.source),
        )
        self.report["hardening_soak_binding"] = {"identity": self.identity, "sha256": sha(report)}
        self.save()

    def release_audit(self):
        # The independent-review gate stays required. Its absence must not stop
        # read-only artifact/profile validation or be silently converted to PASS.
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

    def hardening_summary(self):
        base = self.summarize()
        states = {row["gate"]: row["status"] for row in base["gates"]}

        def combined(*keys):
            values = [states.get(key, "NOT_RUN") for key in keys]
            return (
                "FAIL"
                if "FAIL" in values
                else "NOT_RUN"
                if "NOT_RUN" in values
                else "PARTIAL"
                if any(value != "PASS" for value in values)
                else "PASS"
            )

        stress = "NOT_RUN"
        try:
            binding = self.report["hardening_soak_binding"]
            require(
                binding["identity"] == self.identity
                and binding["sha256"] == sha(self.root / "soak.json"),
                "Soak belongs to another source",
            )
            stress = soak_status(self.root / "soak.json", self.source)
        except (OSError, KeyError, ValueError):
            latest = [
                row
                for row in self.report["steps"]
                if row["label"] == "real-duration-soak" and row.get("identity") == self.identity
            ]
            if latest:
                stress = "FAIL" if latest[-1]["status"] == "FAIL" else "NOT_RUN"
        tests = base.get("tests", [])
        e2e = []
        selectors = {
            "001": "::test_E2E_001_",
            "002": "::test_E2E_002_",
            "003": "::test_E2E_003_",
            "004": "::test_E2E_004_and_010_",
            "005": "::test_E2E_005_",
            "007": "::test_E2E_007_",
            "008": "::test_E2E_008_",
            "009": "::test_INT_015_same_host_five_",
            "010": "::test_E2E_004_and_010_",
        }
        for key, selector in selectors.items():
            rows = [row for row in tests if selector in row["id"]]
            e2e.append(
                {
                    "test_id": "E2E-" + key,
                    "status": "PASS"
                    if len(rows) == 1 and rows[0]["status"] == "PASS"
                    else "FAIL"
                    if any(row["status"] == "FAIL" for row in rows)
                    else "NOT_RUN",
                    "production_tests": rows,
                    "boundary": "Original services and SQLite; synthetic model/ASR/TTS boundaries. E2E002 checks terminal recovery refusal, not replay of an uncertain MCP effect.",
                }
            )
        native = "PASS" if states.get("12") == "PASS" else states.get("12", "NOT_RUN")
        e2e.append(
            {
                "test_id": "E2E-006",
                "status": native,
                "boundary": "Separate native source and installed UIA/OCR/action/verification harness; coordinate unit tests cannot replace it.",
            }
        )
        e2e_state = (
            "FAIL"
            if any(row["status"] == "FAIL" for row in e2e)
            else "PASS"
            if all(row["status"] == "PASS" for row in e2e)
            else "NOT_RUN"
        )
        gates = [
            ("01", "Repository", states.get("01", "NOT_RUN")),
            ("02", "Dependency/license/advisory/SBOM", states.get("02", "NOT_RUN")),
            ("03", "Python", states.get("03", "NOT_RUN")),
            ("04", "Frontend", states.get("04", "NOT_RUN")),
            ("05", "Skill01–10", combined("05", "06", "07", "08")),
            (
                "06",
                "INT001–016 + E2E001–010",
                "FAIL"
                if e2e_state == "FAIL" or states.get("09") == "FAIL"
                else "PASS"
                if e2e_state == states.get("09") == "PASS"
                else "NOT_RUN",
            ),
            ("07", "Security", states.get("10", "NOT_RUN")),
            ("08", "Database", states.get("11", "NOT_RUN")),
            ("09", "Actual-duration stress/recovery", stress),
            ("10", "Windows GUI/performance", combined("12", "13")),
            ("11", "Installer", states.get("14", "NOT_RUN")),
            ("12", "Artifact/profile consistency", states.get("15", "NOT_RUN")),
            ("13", "Independent review", states.get("16", "NOT_RUN")),
        ]
        verdict = (
            "BLOCKED"
            if any(state == "FAIL" for _, _, state in gates)
            else "INCOMPLETE"
            if any(state in {"NOT_RUN", "BLOCKED"} for _, _, state in gates)
            else "RELEASE_CANDIDATE_WITH_LIMITATIONS"
        )
        # Hardware/cloud limits remain; no path here turns synthetic proof into
        # FULL_RELEASE_READY or substitutes for the human publishing decision.
        gates.append(
            (
                "14",
                "Final sign-off",
                "PASS"
                if verdict == "RELEASE_CANDIDATE_WITH_LIMITATIONS"
                else "FAIL"
                if verdict == "BLOCKED"
                else "NOT_RUN",
            )
        )
        result = {
            "schema_version": 1,
            "verdict": verdict,
            "identity": self.identity,
            "original_gates_report": {
                "path": str((self.root / "UNIFIED_ACCEPTANCE_01_10.json").relative_to(self.source)),
                "sha256": sha(self.root / "UNIFIED_ACCEPTANCE_01_10.json"),
            },
            "gates": [
                {"gate": "GATE-" + key, "name": name, "status": state} for key, name, state in gates
            ],
            "e2e": sorted(e2e, key=lambda row: row["test_id"]),
            "tests": tests,
            "github_mutations": 0,
            "real_paid_requests": 0,
        }
        public_json(self.root / "UNIFIED_TEST_RESULTS.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("all", "check", "source-gui", "stress", "build", "installer", "final", "report"),
        default="all",
    )
    for name in ("evidence", "build-root", "build-id", "protected-profile"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    require(sys.flags.optimize == 0, "Do not optimize away QA assertions")
    review = FinalHardening(args)
    require(not review.identity["dirty"], "Commit reviewed source before bound release QA")
    try:
        for phase, method in (
            ("check", review.check),
            ("stress", review.stress),
            ("source-gui", review.gui),
            ("build", review.build),
            ("installer", review.installer),
            ("final", review.release_audit),
        ):
            if args.phase in {"all", phase}:
                method()
        if args.phase in {"all", "final"} and (review.root / "independent-review.json").is_file():
            review.final()
    finally:
        result = review.hardening_summary()
        print(
            json.dumps(
                {
                    "verdict": result["verdict"],
                    "report": str(review.root / "UNIFIED_TEST_RESULTS.json"),
                }
            )
        )
    return (
        0
        if args.phase not in {"all", "final"}
        or result["verdict"] in {"FULL_RELEASE_READY", "RELEASE_CANDIDATE_WITH_LIMITATIONS"}
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
