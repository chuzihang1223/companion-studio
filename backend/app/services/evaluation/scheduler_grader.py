"""Grade observed invariants, never success prose or simulated AI goal quality."""


def grade_scheduler(case, observation, traces):
    evidence = observation["evidence"]
    passed = (
        all(
            evidence.get(k) is True
            for k in ("condition_verified", "trace_consistent", "resources_released")
        )
        and evidence.get("external_requests") == 0
    )
    return {
        "case_id": case.case_id,
        "completion_status": "PASS" if passed else "FAIL",
        "passed": passed,
        "score": int(passed),
        "grader_type": "scheduler",
        "error_code": None if passed else "SCHEDULER_EVIDENCE_FAILED",
        "duration_ms": observation["duration_ms"],
        "tool_calls": 0,
        "model_requests": 0,
        "retry_count": 0,
        "recovery_succeeded": passed if case.case_id == "SCH-022" else False,
        "model_usage": {"status": "Not applicable (synthetic)"},
        "evidence": evidence,
        "evidence_refs": ["evidence:" + k for k in sorted(evidence)],
        "trace_count": len(traces),
        "expected_failure": case.case_id
        in {"SCH-018", "SCH-020", "SCH-021", "SCH-025", "SCH-026", "SCH-027", "SCH-029"},
        "dimensions": {
            "task_success": passed,
            "tool_correctness": None,
            "verification_quality": evidence["trace_consistent"],
            "safety_compliance": evidence["external_requests"] == 0,
            "recovery_quality": passed if case.case_id == "SCH-022" else None,
        },
        "explanation": "真实 SQLite、原运行时与受控时钟断言；不是实际 AI 目标质量、系统睡眠或原生通知验收。",
    }
