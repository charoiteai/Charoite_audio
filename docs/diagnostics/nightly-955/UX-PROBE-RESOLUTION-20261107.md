# Advisory probe validation — Important resolution

Reviewed source: `fa9cba15a7d952056cf3e91405d2e6cd53b8db48`, base
`2106ed52c9b3f70f5d0c8b6363e9e3171ae767be`. Independent OUTPUT reviewers
gpt-6.1-terra and gpt-6-luna both reported Critical0/Important1/Minor0. Terra's
code finding is accepted: `defer*` admitted invalid successful output and omitted
UX despite the source contract's fail-open rule. Luna's delivery finding remains
open: new-SHA CI and actual private child/UX FD retention or orphan exclusion.

## Producer contract and minimal change

The actual `scripts/night_gate.py` emits `go`, `brief`, `skip` or
`f"defer battery {pct}%"`. `pct` comes from `int` after the nonnegative digit
capture in `parse_power`; there is one defer producer. The advisory reader now
accepts these fixed decisions and the canonical nonnegative-integer defer shape,
normalizing a valid defer to the internal decision `defer`. It does not repeat
the battery threshold or decide whether the night may run.

An appended sentinel preserves stdout's trailing newlines through shell command
substitution; only the final producer newline is removed. Extra lines, including
an extra empty line, remain invalid. The sentinel is appended only on process
success, preserving fail-open after a nonzero exit even if valid-looking output
was already printed. The original final night gate and resource/window/other
owner guards are unchanged.

## Actual before/after

The BEFORE run kept runtime at the reviewed SHA and added the new integration
controls. It reproduced10 subtest failures in UX counts for invalid successful
answers: malformed defer forms, garbage suffixes, multiline text, and a trailing
empty line after `skip`. It had no test errors; four unrelated stale pytest
temporary-directory cleanup warnings were not treated as a clean check.

The first AFTER run had62 passing test bodies but62 teardown errors: an explicitly
chosen `/tmp` basetemp is outside macOS's native temporary directory and correctly
failed the existing root-isolation guard. No guard was weakened. Repeating in a
fresh `tempfile.mkdtemp` directory passed62tests/27subtests in4.39seconds, exit0.
The matrix includes invalid-success fail-open, valid defer values obtained from
the actual gate function, nonzero probe status despite valid-looking stdout,
authoritative final decisions, preserved artifacts, and all three early guards.

All execution used installed fresh quiet_guard && heavy_run with
HEAVY_WAIT_MAX_S=60. The before/after window was coordinated with the other
executors; critical work waits while their model/runtime window owns the machine.
Targeted shell-string mutation controls, final critical evidence and the exact
CI publication route are recorded after execution. No install or real night is
implied by these source checks.

## Private projection and remaining delivery boundary

The allowlisted sealed private source copy matched its expected c88f5d6f hash.
A real gated staging call using only synthetic data/command inputs generated a
new private candidate with hash13c1822c and mode0600 in a mode0700 directory.
Both original and candidate passed native bash syntax checks; the source and
the entire UX function remained byte-identical (UX hash e06da3f1). The public
proof contains only hashes, modes and fixed results; private source/paths and
the manifest argv were not exported, and the private wrapper was never executed.

The structural UX projection identifies a background speech/kill/wait branch.
The current public runtime tests replace UX with a synthetic action; they do
not exercise that actual OS child/service lifetime. Executing a faithful private
UX branch can invoke real user-facing operations, while substituting stubs would
remove the very descendant boundary that needs attestation. No independently
proved orphan exclusion exists for a non-retaining branch. This is the concrete
remaining delivery limitation, not a missing-resource-gate claim; source staging,
synthetic FD-retaining/close_fds counterexamples and syntax checks do not close it.

## Final addressed verification

Code commit: `a8bd701da249446752816b39ef3c7ea6598da7f2`, normal hooks passed.
Three shell-string mutations were each killed by their expected integration
assertions after a clean baseline: admit an arbitrary defer prefix; discard
extra record newlines; mask a failed probe's process status. No generic error,
skip or timeout is counted as an addressed kill. The helper and JSON receipt
are retained for repetition in isolated tracked-source clones.

The standard final critical run on base2106ed52..a8bd701d used `--only-critical
--max all --jobs 4` under fresh installed quiet/heavy gates. All four shards
judged29/29 and returned0:116/116 critical judged, survivors0/skipped0/unread0/
capped0, unchanged zones hash5cee37846e66. All116 journal keys are unique.
The11 noncritical identities of the127-total plan were not selected. Public
facts and journals are `probe-final-critical-shard-*-20261107.*`; the combined
official summary and source/test/environment binding are
`probe-final-critical-20261107.md` and `probe-final-proof-20261107.json`.

The historical original-identity replay now explicitly freezes compatible
candidate tests at ebceb986. Its focused consumer repeat passed with both
original formatting mutations still labelled survived; later probe expectations
do not contaminate the old baseline. No new independent review round is claimed:
the Important code finding was resolved with addressed evidence as requested.
Fresh same-SHA CI and actual private UX/child lifetime acceptance remain open.
