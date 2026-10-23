# Skill 10 baseline

The new local branch starts from `dd29d811f9c5fdf3dd7a56e7257b73de154d09cb` (Skill 09).
The original `scripts/check.ps1` finished with exit code 0 before tracked edits.
JUnit contains 575 tests: 573 passed, 2 skipped, 0 failed/errors (595.060 seconds).
Evidence is in the ignored local `.cache/skill10-baseline-01/` directory, including the
original check log, JUnit, source identity and protected-profile hash inventory (95 files).
These are baseline observations, not acceptance results for the new router.

Prior Skill 09 installer SHA-256:
`36e754919c1d2322a4328ada12dd80381b8214bd289446fedeed8ab6bfaf734d`.
The baseline source was clean; main and the previous installer are preserved.

Known limits carried into the new audit: unsigned Windows installer; physical microphone,
acoustic echo cancellation, Fish/IndexTTS live providers and automatic voice interruption
are not established by synthetic audio or loopback model tests. Unknown price/capability
must remain unknown. Existing knowledge, screenshot and scheduled-model permissions
must not be broadened by automatic model selection or fallback.
