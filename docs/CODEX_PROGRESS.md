# Codex progress

## Current scope

- Skill: Final Production Release, resource cleanup and release gates for v0.3.0.
- Working version: **0.3.0-rc.4**. No final release, tag, upload, or remote push is authorized in this stage.
- Branch: `release/0.3.0-final-hardening`.
- Last tested source commit: `a598d20115c7d1f9551e2bfc911278dbdbe419d1`.
- This file records the continuation; newer documentation commits do not turn old test results into new-source passes.

## Completed

- Fixed SSE subscription/Session lifetime and prepared Agent handle ownership, including cancellation and pending-plan expiry.
- Serialized Trace deletion accounting with its SQLite transaction.
- Fixed deletion of owned long-path evaluation archives; rejected junction aliases and waited for actual evaluation task completion in tests.
- Repaired public source export closure: 992 tracked files, including required feature metadata and linked documentation.
- Added real lifecycle/resource counters and the required 3600-second mixed-load plus idle observation harness. Short diagnostic runs are explicitly not final stability evidence.
- Preserved every failed or interrupted check and its source identity in local evidence.

## Evidence and remaining failures

- Targeted resource/Trace suite: 93 passed on its recorded earlier source; final cancellation test passed. These results cover only the files and revisions recorded with them.
- Evaluation fixes: 15 passed; boundary checks: 3 passed; portable short-root long-path checks: 3 passed.
- Latest original complete check (`final-production-release-05`): **847 total, 842 passed, 3 failed, 2 skipped**. The two skips require Windows symbolic-link privileges.
- Failures: Recovery Trace flush (`WriteGateTimeout`, 20 accepted / 19 committed, one durable retry-pending command); 1000-event admission at 5151.97 ms versus the unchanged 3000 ms limit; crash-recovery child exceeding its unchanged 12-second limit.
- The failed recovery source remains durable. No event loss or successful recovery is inferred from that fact.
- The complete source check stopped the dependent build. No rc.4 installer or final one-hour run has been produced.
- One bounded diagnostic run of those same three failing tests passed (24.61 s). Admission took 2498.54 ms; measured journal transaction exits accounted for 2198.16 ms. The sampled Recovery run had no gate wait over 50 ms. These observations do **not** explain or supersede the failed complete run; the timing/reliability blocker remains open.

## Key files

- `backend/app/services/work_state.py`
- `backend/app/services/agent_service.py`
- `backend/app/services/desktop/runtime.py`
- `backend/app/services/runtime_registry.py`
- `backend/app/services/trace/repository.py`
- `backend/app/services/evaluation/environment.py`
- `backend/tests/test_work_stream_resources.py`
- `backend/tests/test_agent_prepared_resources.py`
- `backend/tests/test_trace_deletion_accounting.py`
- `backend/tests/test_evaluation_system.py`
- `backend/tests/hardening_soak_workload.py`
- `backend/tests/production_resources_workload.py`

## Blockers and next action

1. Diagnose the three latest failures using their retained logs and owned test databases; run affected tests with timing/lock diagnostics before changing production behavior. Do not relax durability, retries, timeouts, performance limits, or assertions.
2. After a justified correction, freeze the source, run the required original checks/build, then the real 3600-second mixed workload, lifecycle/idle checks, native source/installed GUI and isolated install/uninstall verification. Reuse unaffected evidence only with explicit file/revision binding.
3. Complete allowed independent engineering evidence reviews and artifact/privacy/signature checks.
4. GATE-07 and the required Security portion of GATE-13 remain **BLOCKED**: the independent Security continuation was denied by automatic review for possible cybersecurity risk, with no specific rule supplied. The denied dependency-audit installation is also not retried. Ordinary local audits cannot substitute for independent Security signoff.
5. Keep delivery local and truthfully blocked until every mandatory gate passes. Do not change formal user profiles or the separate private edition.

Local raw evidence is intentionally excluded from public source. The continuation index is `.cache/final-production-01/CONTINUATION_STATE.json`; failed full-check evidence is retained under that directory.
