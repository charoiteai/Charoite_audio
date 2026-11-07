# Addressed survivor replay, 2026-11-07

Original source: `fdbf9166a7224e5fa36772fcf32fd1dfd51e45a4`.
Original PR base: `2106ed52c9b3f70f5d0c8b6363e9e3171ae767be`.
Scope: source candidate and synthetic evidence only. No installation, real night,
model, private wrapper execution or owner data access is claimed.

## Preserved work and baseline

The previous executor's uncommitted test patch was read without modifying that
worktree. Both file hashes matched before and after reading. The original patch
and its attribution/hashes are retained as `prior-controls.patch.json` and
`prior-controls-attribution.json`. The current executor reviewed the patch and
verified it through real filesystem, kernel-lock and subprocess controls.

The immutable source baseline passed 54 tests and 5 subtests in 3.11 seconds.
With the preserved patch, the same four files passed 73 tests and 5 subtests in
3.22 seconds. Those commands ran through fresh installed quiet/heavy gates.
Two invalid test selections returned pytest code4 without executing tests; they
are not passing checks. The historical missing adapter-test filename was not
recreated merely to satisfy the old review text.

## Exact replay

`original-controls-replay.json` records the 18 original AST identities, mutation
keys, mutated-source hashes, original/candidate test hashes, four passing
unmutated suite baselines, outcomes and exact failing test names. The script
uses the existing mutator's scan/application functions in an isolated clone of
the immutable original SHA, overlays only the recorded candidate tests, disables
bytecode and requires pytest rc1, no errors/skips, and the expected behavioral
assertion before accepting a kill. Logs/XML remain in the local experiment
directory; the public JSON receipt contains no private source.

All 18 original survivors reproduced with original tests. Candidate controls
kill identities1–16 through the expected assertions. Identities6/7 deliberately
exercise the existing defensive returning-exec seam; real successful OS exec
does not return. This is explicitly mock-branch coverage, not real exec success.

Identities17/18 (JSON key ordering and indentation) still survive. They are not
reported as killed. `format-consumer-replay.json` separately records a passing
real consumer baseline and both original formatting mutations against a control
that reads the actual JSON bytes, checks receipt and bound-file hashes, executes
the parsed literal argv through the real process-lock helper and transformed
public shell fixture, and observes the same held inode in the final command.
The consumer does not assert a golden whitespace/key-order representation.

The sole runtime change removes the two optional formatting kwargs from JSON
serialization. Semantic fields, argv, permissions and actual-byte hash binding
remain unchanged. This removes unnecessary configuration choices, not critical
coverage or a required wire/signature contract. No zone, threshold or mutation
exemption changed. Both OUTPUT reviewers must inspect this disposition and the
remaining source risk; these observations alone do not approve an equivalent.

## Repeat

Use the installed quiet_guard and heavy_run with `HEAVY_WAIT_MAX_S=60`; keep the
gate and heavy command in one `&&` chain. Do not override gate data roots.
Run `replay_survivors.py --output NEW_PRIVATE_TEMP_DIRECTORY` with the project's
Python for all original identities and both test snapshots. For the focused
consumer experiment also pass `--mode candidate --identities 17,18 --consumer-only`.
The replay intentionally uses original runtime bytes so original identities
remain reproducible after removing the unnecessary formatting arguments.
Its candidate test ref is also frozen at `ebceb986944ae7706def43894dc1426242b2fb32`
by default (`--candidate-tests-ref`): later advisory-probe changes legitimately
change the transform expectation and must not contaminate an original-identity
baseline. Each new replay records the resolved test SHA and actual test hashes.

## Acceptance boundaries

The synthetic wrapper executes before/after the same transform with unchanged
marker, artifact and fake commands. Actual process tests demonstrate contention,
exec failure, retained-descriptor orphan exclusion, immediate FD closure and the
unprotected close_fds counterexample. These are real synthetic observations.
They do not attest the actual private wrapper's UX/child boundaries. The existing
INPUT installation blocker remains: prove every actual boundary retains the FD
or has independent orphan-work exclusion before installation/task closure.

Full current-source critical mutation verdict, exact-snapshot independent
Terra/Luna OUTPUT/final-risk reviews and applicable CI are recorded separately
when executed. Prior CI/reviews are not reused as current acceptance.

## Current-source execution result

On code snapshot `ebceb986944ae7706def43894dc1426242b2fb32`, the post-change
regression passed188tests/5subtests in79.30seconds. The standard project mutator
then judged all116/116 current critical mutations on the exact range from the
original PR base: survivors0, skipped0, unreadable0, capped0, exit0. Its facts,
full per-identity journal and summary are retained as
`critical-mutation-20261107.json`, `.jsonl` and `.md`. The plan has127 total
mutations; this run selects every critical identity and does not claim the
11 noncritical identities were judged. The original118 critical points became
116 solely because the two optional JSON-format arguments were removed after
the separate original-identity consumer experiment. The zones hash is unchanged.

```sh
~/.config/charoite/quiet_guard.sh && HEAVY_WAIT_MAX_S=60 \
  ~/.config/charoite/heavy_run.sh env CHAROITE_ROOT="$N955_TEST_DATA" \
  "$CHAROITE_PYTHON" scripts/mutate_check.py \
  --range 2106ed52c9b3f70f5d0c8b6363e9e3171ae767be..ebceb986944ae7706def43894dc1426242b2fb32 \
  --only-critical --max all --report "$N955_REPORT"
```

Use a new synthetic data directory and report path. No gate environment is
overridden: CHAROITE_ROOT above belongs only to the command already admitted by
the installed gates. `execution-proof-20261107.json` records the environment,
gate hashes and tested source/test hashes. The following evidence-only commit
does not change the tested runtime, tests or their dependencies; no new test
execution is claimed for those unchanged bytes.

Independent current OUTPUT/final-risk reviews and new-SHA CI are still pending.
This local critical verdict does not remove the private installed-chain blocker.
