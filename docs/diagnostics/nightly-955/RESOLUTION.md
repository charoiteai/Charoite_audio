# Source review resolution (partial task)

Base2106ed52c9b3f70f5d0c8b6363e9e3171ae767be. Independent models were
gpt-6.1-terra/high and gpt-6-luna/high, without execution history or private
wrapper source. Raw permitted verdicts are in reviews/.

INPUTv1 manifest07604bc6d8c74f94c5a48beee3181d3d21f2b889c796dc3a7dc0f01b1d3c1226:
Terra C1, Luna C0. An inheritable descriptor does not protect arbitrary close_fds
descendants after the last holder dies. INPUTv2 manifest950f6b337be8c497e1ab1926a20d344e6a741c0879845c1e2bf5784696058fe3:
both C0 for a narrower SOURCE CANDIDATE. The original runtime boundary remains an
installation blocker, not a fixed/closed task. Tests explicitly exhibit both FD
retention and the unprotected close_fds/immediate-close cases.

OUTPUT source packet63e2a6a9b5a9532b99a9343a337dee86c2860f3ff337678032e26a713941b5e5:
both C0, Terra I2, Luna I1/M1. Important findings accepted: the first tests called
the gate directly and did not execute wrapper before/after; staging/CLI refusals
were not integrated. Replaced with an executable public synthetic wrapper through
the exact transform and real outer lock, counters, artifacts and refusal tests.
Private wrapper bytes are still absent from this public repository.

One bounded INPUT amendment by Terra (manifest2f8a9d0bc36e607fd0a9000915f6d60f7135ca769a7a8355c1c017d769f20b28)
found no Critical for staging and raised telemetry/fail-open/brief/environment/
second-observation questions. The shorter implementation makes the early gate
advisory ONLY for UX and always delegates to the original nightly source with
SLOT=1. Its final gate, diagnostic record, cwd/environment and brief are retained.
Tests cover probe failure and a changed early/final decision. Partial private
staging output on refusal is documented and never treated as installed.

The kernel-lock implementation moved unchanged into process_lock_exec (runtime),
staging into retry_wrapper_stage (runtime), with thin manual CLIs. This places the
critical logic in the existing mutation area instead of claiming a manual script
received default product mutation coverage. Source-only layout regeneration passed.

Local behavioral/mutation runs did NOT execute: the quiet gate returned3 twice
while recording. All new test assertions and Important fixes await same-SHA CI;
source review/static AST are not experiments. No new reviewer loop is claimed
for those addressed Important findings. No real night, model request, installation
write, launchd update, owner graph read, or installed child-FD proof occurred.

## Follow-up after first CI

CI42204830475 on358efecc failed only the private-entry umask inventory test
(Linux11126passed, macOS11138passed). Both new CLIs now call the existing
harden_umask before main; no exemption was added. Under a fresh quiet/heavy
gate, the narrow regression and new synthetic cases passed55tests/5subtests
in4.82seconds. A real child started from mask022 produced a600file. Runtime
modules are imported directly by their tests so mutation selection remains
addressed. Source hashes and command are in targeted-result.json. Full CI on
the new commit and PR-event critical mutations remain pending; branch-push CI
skips mutation jobs. Installation/private full-wrapper/FD-chain proof is still
not complete. These new results supersede the earlier local-NOTRUN statement
for the public synthetic tests only.
