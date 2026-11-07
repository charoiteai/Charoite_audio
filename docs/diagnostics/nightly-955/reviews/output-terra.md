# №955 — independent OUTPUT/source-risk review

Reviewer: gpt-6.1-terra. Scope: neutral staged source candidate at `/tmp/n955-output-neutral-20261106`, stated base `2106ed52c9b3f70f5d0c8b6363e9e3171ae767be`. Static review only; no candidate, test, mutation, or installed-runtime execution. The packet identifies manifest SHA256 `63e2a6a9b5a9532b99a9343a337dee86c2860f3ff337678032e26a713941b5e5`; I did not independently verify it. Private wrapper and actual command chain were not inspected.

## Critical

None identified in this limited source review. This is not an installation or task-completion verdict: FD retention/orphan exclusion, gated execution, mutations and private wrapper projection remain unproved.

## Important

1. `tests/test_retry_adapter_contract.py:23-32`, `docs/diagnostics/nightly-955/fake_nightly.py:13-20`: the test creates a started marker but calls the fake nightly directly through the lock. The fake never reads the marker or runs either the pre-change wrapper or transformed wrapper, so it cannot demonstrate the required same-input before/after suppression and retry; it would pass even if the real transformation did not eliminate the suppressor. The claimed interrupted/crash case also writes a `running` status with `finished` instead of actually interrupting before final status. Fix: execute synthetic before/after wrapper fixtures matching the relevant control flow with identical marker/status/artifact and counters; include a controlled failure before final status, verify retry and unchanged artifact. Keep real wrapper and data out of the fixture.

2. `tests/test_stage_retry_wrapper.py:32-58`, `scripts/stage_retry_wrapper.py:57-95`: tests call `transform` and `outside_git` individually but never exercise `stage` or its CLI, so they do not establish the actual staging refusal boundary, exclusive output behavior, private file permissions, manifest/argv hashes, source immutability or redacted refusal output. In particular, the advertised targeted mutation of the private-output guard is not shown to be detected through the write path. Fix: use a synthetic source with injectable expected hash or a narrowly scoped test seam and temporary directories to test valid staging plus mismatched hash, Git-marked parent, existing/symlink output and error refusal, verifying no private output outside the permitted new directory and no private paths/text on stdout.

## Minor

None identified.

The lock's explicit descriptor-lifetime limitation is correctly stated; an inheriting fake does not establish protection for real descendants. Static reading is not an execution result. No private bytes or paths were included in this verdict.
