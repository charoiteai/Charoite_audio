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
