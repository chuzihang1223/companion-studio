"""Exact production test identities; similarly named tests cannot certify E2E."""

MODULE = "backend.tests.test_hardening_end_to_end::"
VOICE = "backend.tests.test_hardening_voice_reopen::"
E2E_TESTS = {
    "001": MODULE + "test_E2E_001_api_plan_routing_tool_goal_trace_and_no_reexecute",
    "002": MODULE + "test_E2E_002_mcp_failure_same_workflow_recovery_and_artifact_evaluation",
    "003": MODULE + "test_E2E_003_rag_output_citation_invalidated_by_source_change",
    "004": MODULE
    + "test_E2E_004_and_010_scheduled_report_notification_trace_independent_tamper_grading",
    "005": VOICE + "test_E2E_005_final_agent_tool_tts_manual_interrupt_rejects_late_generation",
    "007": MODULE + "test_E2E_007_provider_budget_failure_has_no_tool_side_effect",
    "008": VOICE + "test_E2E_008_owned_hard_exit_reopen_migration_and_sqlite_integrity",
    "009": "backend.tests.test_unified_skill_routing::test_INT_015_same_host_five_conversations_cancel_isolation",
    "010": MODULE
    + "test_E2E_004_and_010_scheduled_report_notification_trace_independent_tamper_grading",
}


def matching_tests(tests, key):
    return [row for row in tests if row["id"] == E2E_TESTS[key]]
