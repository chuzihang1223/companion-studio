"""Merge exact unit, original worker and real GUI evidence without erasing boundaries."""

from pathlib import Path
from visual_review_evidence import read, sha
from unified_review_contracts import feature_result, allowed_skip

HARNESSES = {
    "trace-viewer-e2e.cjs": "trace",
    "evaluation-e2e.cjs": "evaluation",
    "recovery-e2e.cjs": "recovery",
    "extensions-e2e.cjs": "extensions",
    "workflow-e2e.cjs": "workflow",
    "knowledge-e2e.cjs": "knowledge",
    "computer-use-e2e.cjs": "gui",
    "scheduler-e2e.cjs": "scheduler",
    "realtime-voice-e2e.cjs": "voice",
    "model-router-e2e.cjs": "router",
}
HARDWARE_CODES = {
    "CU-013": "PHYSICAL_DPI_ENVIRONMENT_UNAVAILABLE",
    "CU-014": "PHYSICAL_DPI_ENVIRONMENT_UNAVAILABLE",
    "CU-015": "PHYSICAL_DPI_ENVIRONMENT_UNAVAILABLE",
    "CU-016": "DISPLAY_CHANGE_ENVIRONMENT_UNAVAILABLE",
}


def proof(source, path):
    return {"path": str(path.relative_to(source)).replace("\\", "/"), "sha256": sha(path)}


def feature_matrix(review, features, rows, latest, test_root):
    source = review.source
    benchmarks = {}
    if test_root:
        for path in (source / test_root / "benchmarks").glob("*.json"):
            value = read(path)
            for row in value["results"]:
                benchmarks[row["case_id"]] = {**row, "proof": proof(source, path)}
    native = {}
    for variant in ("source", "installed"):
        home = review.report.get("gui_evidence", {}).get(variant)
        if home and latest.get(variant + "-gui", {}).get("status") == "PASS":
            path = source / home / "computer-benchmark.json"
            if path.is_file():
                value = read(path)
                native[variant] = {
                    r["case_id"]: {**r, "proof": proof(source, path)} for r in value["results"]
                }
    matrix = []
    for item in features:
        _, tests = feature_result(item, rows)
        proofs = [proof(source, source / test_root / "pytest.xml")] if tests and test_root else []
        gui = []
        kind = HARNESSES.get(Path(item.get("gui_harness", "")).name)
        for variant in ("source", "installed"):
            label = variant + "-" + str(kind)
            relative = (
                review.report.get("gui_evidence", {}).get(variant)
                if kind == "gui"
                else review.report.get("unified_gui", {}).get(label)
            )
            check_labels = item.get("gui_labels", [])
            state, detail = "NOT_RUN", []
            if relative and latest.get(label, {}).get("status") == "PASS":
                path = source / relative / "report.json"
                if path.is_file():
                    checks = read(path).get("checks", [])
                    selected = [c for c in checks if c.get("label") in check_labels]
                    detail = [
                        {
                            "label": c["label"],
                            "status": "PASS" if c.get("passed") is True else "FAIL",
                        }
                        for c in selected
                    ]
                    state = (
                        "PASS"
                        if check_labels
                        and len(selected) == len(set(check_labels))
                        and all(c["status"] == "PASS" for c in detail)
                        else "FAIL"
                        if any(c["status"] == "FAIL" for c in detail)
                        else "NOT_RUN"
                    )
                    if selected:
                        proofs.append(proof(source, path))
            gui.append({"variant": variant, "status": state, "checks": detail})
        required_gui_ok = all(g["status"] == "PASS" for g in gui)
        cases = []
        for case_id in item.get("benchmark_case_ids", []):
            candidates = (
                [native[v].get(case_id) for v in ("source", "installed") if v in native]
                if case_id.startswith("CU-")
                else [benchmarks.get(case_id)]
            )
            if case_id.startswith("CU-") and len(candidates) != 2:
                candidates = [None]
            for case in candidates:
                case_status = case.get("execution_status", "NOT_RUN") if case else "NOT_RUN"
                limitation = False
                if case_status == "BLOCKED" and case:
                    code = case.get("error_code") or case.get("result", {}).get("error_code")
                    limitation = case_id in HARDWARE_CODES and code == HARDWARE_CODES[case_id]
                    # Original Voice synthetic layer deliberately leaves native/acoustic
                    # work to the separately required real GUI. Retain BLOCKED; never
                    # rewrite it to PASS or imply physical mic / cloud quality.
                    if (
                        case_id.startswith("VOICE-")
                        and code == "VOICE_NATIVE_OR_INTEGRATION_GATE_REQUIRED"
                    ):
                        limitation = all(
                            latest.get(v + "-voice", {}).get("status") == "PASS"
                            for v in ("source", "installed")
                        )
                cases.append(
                    {
                        "case_id": case_id,
                        "status": case_status,
                        "accepted_environment_limit": limitation,
                        "error_code": case.get("error_code") if case else None,
                    }
                )
                if case:
                    proofs.append(case["proof"])
        selectors = set(item.get("direct_test_ids", []))
        backend_ok = (
            (
                bool(selectors)
                and {r["id"] for r in tests} == selectors
                and all(r["status"] == "PASS" or allowed_skip(r) for r in tests)
            )
            if item.get("backend_applicable", True)
            else bool(item.get("gui_requirement") == "REQUIRED" and required_gui_ok)
        )
        gui_ok = item.get("gui_requirement") != "REQUIRED" or required_gui_ok
        cases_ok = all(c["status"] == "PASS" or c["accepted_environment_limit"] for c in cases)
        failed = (
            any(r["status"] == "FAIL" for r in tests)
            or any(c["status"] == "FAIL" for c in cases)
            or (
                item.get("gui_requirement") == "REQUIRED"
                and any(g["status"] == "FAIL" for g in gui)
            )
        )
        limited = (
            bool(item.get("scope_limited"))
            or any(allowed_skip(r) for r in tests)
            or any(c["accepted_environment_limit"] for c in cases)
        )
        complete = backend_ok and gui_ok and cases_ok
        status = (
            "FAIL" if failed else "NOT_RUN" if not complete else "PARTIAL" if limited else "PASS"
        )
        accepted = complete and limited
        unique = {p["path"]: p for p in proofs}
        matrix.append(
            {
                **item,
                "status": status,
                "tests_executed": len(tests),
                "tests_passed": sum(r["status"] == "PASS" for r in tests),
                "backend_tests": tests,
                "gui": gui,
                "benchmark_cases": cases,
                "proofs": list(unique.values()),
                "accepted_environment_limit": status == "PARTIAL" and accepted,
                "coverage_basis": "Exact assertions, source-bound report hashes and original worker outcomes; declared limits retained",
            }
        )
    return matrix
