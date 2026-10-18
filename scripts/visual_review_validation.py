"""Explicit, optimization-safe validation of measured QA evidence."""

import json
from pathlib import Path
import subprocess

from visual_review_evidence import read, require, safe_file, sha


def validate(args, review_class):
    if args.validate_independent_review:
        value = read(Path(args.validate_independent_review))
        require(value["status"] == "PASS" and value["open_p1_p2"] == [], "Open review findings")
        require(
            value.get("qa_integrity", {}).get("status") == "PASS", "QA integrity review missing"
        )
        source = Path(__file__).resolve().parents[1]
        hashes = value["qa_integrity"].get("source_hashes", {})
        required = {
            "scripts/review-visual-runner.py",
            "scripts/visual_review_evidence.py",
            "scripts/visual_review_validation.py",
            "backend/tests/test_visual_review_evidence.py",
            "docs/AUTOMATED_QUALITY_GATES.md",
            "docs/COMPUTER_USE_EVALUATION.md",
            "scripts/program_inventory.py",
            "scripts/test-installer.ps1",
            "scripts/build-companion-studio.ps1",
            "backend/tests/test_program_inventory.py",
        }
        require(required.issubset(hashes), "QA review does not cover current gate implementation")
        require(
            all(sha(safe_file(source, name)) == digest for name, digest in hashes.items()),
            "QA implementation changed after independent review",
        )
        followup = value.get("packaging_schema", {})
        product_hashes = followup.get("source_hashes", {})
        require(
            followup.get("status") == "PASS"
            and {
                "backend/app/services/mcp_ext/registry.py",
                "backend/app/services/mcp_ext/adapter.py",
                "backend/tests/test_mcp_security.py",
            }.issubset(product_hashes),
            "Offline schema followup review missing",
        )
        require(
            all(sha(safe_file(source, name)) == digest for name, digest in product_hashes.items()),
            "Product followup changed after review",
        )
        reviewed = value["runtime"]["reviewed_revision"]
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", reviewed, "HEAD"], cwd=source, check=True
        )
        later = subprocess.check_output(
            ["git", "diff", "--name-only", reviewed, "HEAD"], cwd=source, text=True
        ).splitlines()
        require(
            all(
                name.startswith(("scripts/", "tests/", "docs/", "backend/tests/"))
                or name == "README.md"
                or name in product_hashes
                for name in later
            ),
            "Product changed after independent review",
        )
        print(
            json.dumps(
                {"status": "PASS", "reviewed_revision": reviewed, "later_qa_or_docs_only": later}
            )
        )
        return True
    if args.validate_performance:
        value = read(Path(args.validate_performance))
        require(value["frames"] > 10 and value["frame_gap_p95_ms"] > 0, "Missing frame timing")
        require(value["memory_before"] and value["memory_after"], "Missing memory measurement")
        require(len(value["native_cases"]) == 24, "Missing native cases")
        require(
            any(row.get("metrics", {}).get("uia_ms", 0) > 0 for row in value["native_cases"]),
            "Missing actual UIA timing",
        )
        print(
            json.dumps(
                {"status": "PASS", "measured_frames": value["frames"], "stable_fps_claim": False}
            )
        )
        return True
    if args.validate_consistency:
        review = review_class(args)
        require(
            Path(args.validate_consistency).resolve() == review.delivery / "build-manifest.json",
            "Unexpected build manifest",
        )
        review.current_artifact_key()
        review.installed_program()
        binding = review.bind_profile()
        before, after = read(review.root / "profile-before.json"), review.snapshot()
        require(before == after, "Formal profile file hashes changed")
        (review.root / "profile-protection.json").write_text(
            json.dumps(
                {"status": "PASS", "files": len(after), "unchanged": True, "binding": binding}
            ),
            encoding="utf-8",
        )
        print(json.dumps({"status": "PASS", "profile_files": len(after)}))
        return True
    return False
