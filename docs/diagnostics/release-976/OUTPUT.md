# 976 test-candidate output

Base: `50b0704fe280360b1d7f828fd601c6216959e55d`. This is a new test candidate, not a release or a successful full critical verification.

A checker-specific shared test fixture now asserts forward progress of continued claim searches. Real matching, checker coverage, refusals, production code, canonical mutation engine and timeout/scoring policy remain unchanged. New scans reset the oracle, including identical claim text supported by two sources. All 16 original selected files still run; canonical conservative selection additionally includes the new calibration file (17 total). No selector exclusions or `-x` were introduced.

## Actual acceptance

- Focused fixture/checker/calibration tests: **27 PASS, 4.87 s**. Same baseline accepted/refused results with oracle disabled/enabled. Exact legacy `scan`/`applied` b9 mutant: ordinary child AssertionError/exit 1. Explicit synthetic disabled-oracle control: child does not finish, then owned PID is killed/reaped, both pipes closed; wrong key/hash and invalid/missing receipt controls pass. No global oracle bypass exists.
- Unchanged canonical `copy_tree` and `run_tests`, timeout 120 s, outer cap 480 s, no `-x`: original 16 baseline **604 PASS, 14.65 s**; new 17 baseline **624 PASS, 19.90 s**; exact b9 new whole 17 **193 FAIL + 431 PASS, 22.54 s**, normal exit 1. Full stdout contains **192 progress AssertionError**, zero pytest timeout failures. All 624 collected cases finish. This is an uncapped ordinary failure, not an expected-timeout kill.
- Exact mutant source/hash, every selected file/hash, canonical argv check, full raw output hashes/lengths and elapsed times: `WHOLE-SELECTED-RECEIPT.json`. Raw logs stay outside the public repository because machine paths may appear in pytest warnings. Passive capture returns the unchanged subprocess result to canonical `run_tests`.
- Production `src/`, `scripts/`, `app/`: **362 files byte-identical** to base (Git blob plus SHA-256 inventory in `SOURCE-APPLICABILITY.json`). Shared fixture/test changes require new literal candidate and fresh full critical proof; old 50b failures are not relabelled.

Callsite check: existing selected files import the same `dossier_incremental` module; no direct `from dossier_incremental import _check_claims`, reload or detached checker alias was found. Internal direct/indirect calls resolve the module global wrapped by the fixture. The whole-suite exact-mutant run is the behavioral coverage evidence, beyond this static inspection.

## Honest harness history

The first focused expanded harness had 23 PASS / 1 FAIL: a preexisting isolation fixture creates two directories in `tmp_path`, so the negative control must compare its before/after inventory rather than assume empty. Fixed the harness assertion; 27 PASS above includes the correction. An initial whole harness stopped before tests on obsolete 16-file expectation; selection was reported and retained as 17. First whole execution itself produced 604 PASS / 624 PASS / normal mutant FAIL, but its final diagnostic assertion expected the error text inside canonical's last 30 lines; that truncated tail contained only the summary. `FIRST-WHOLE-RECEIPT.json` preserves those results. Added passive full-output receipt and repeated the bounded proof above. Layout guard rejected the diagnostic Python helper placed under documentation; moved it to its proper `tests/claim_progress_replay.py` location without any guard exception or production change.

## Repeat / remaining

Run `tests/claim_progress_replay.py` with the project interpreter through fresh quiet and shared heavy gates. Focused pytest targets are `tests/test_claim_progress_oracle.py tests/test_dossier_section_claim_coverage.py`; use a native temporary basetemp. Existing harmless pytest garbage-cleanup warnings were 8 in each whole run; they did not produce timeout/failure outcomes.

Layout map regenerated without policy changes; full import-boundary/layout target: **84 PASS, 68.56 s**. Pending: normal hooks, exact candidate commit/branch CI and independent output/final-risk review. Definition32 must rebind the new candidate, 17 selected files and shared fixture; no new 682/32 run, release PR update, tag, installation or restart has occurred. Installed release acceptance remains outside this test-only step.
