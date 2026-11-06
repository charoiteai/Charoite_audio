# Retry adapter source candidate / Кандидат адаптера повтора

This is not an installed fix or a completed nightly acceptance. The existing
night_gate policy is unchanged. A private wrapper's daily started marker was a
separate suppressor; its historical effect on a particular night is unknown.

`scripts/with_process_lock.py --lock STATEFILE -- COMMAND ...` keeps a stable
kernel flock across exec. Contention returns75, invalid state returns nonzero;
it never removes a stale-looking lock or evaluates argv with a shell. Its
containing directory and inode must remain trusted and stable.

Protection lasts only while at least one process keeps the descriptor open.
The immediate command may close it; a close_fds descendant is not protected
after the last retaining ancestor dies. Full installation remains blocked until
the real UX/child chain proves retention or has independent orphan-work exclusion.
The synthetic counterexample is part of the required verification, not a claim
of process-tree isolation.

`scripts/stage_retry_wrapper.py` accepts explicit source/output/interpreter/
helper/nightly/lock/data-root paths. It checks the known wrapper hash and removes
only started-marker suppression/creation. A probe of the unchanged night_gate
before UX omits UX for brief/skip/defer; errors or unknown output keep the source
fail-open behavior. Every outcome still reaches the original nightly exec with
CHAROITE_SLOT=1, retaining its final gate, telemetry, cwd/env and brief. A probe
cannot admit heavy work around the existing resource guards. A new private
output outside Git contains the candidate and outer-lock argv manifest. The
source, launchd and installation remain untouched. Do not execute that manifest
on real data for this task. A refused staging can leave a partial private output;
it has no installed status and must not be executed without a complete manifest.

## Current evidence

- Base2106ed52; prior wrapper hash and private candidate hash are in
  `staging-receipt.json`. The installed source remained byte-identical.
- A private source copy, patch and candidate exist outside this repository.
  They are not included in the public packet.
- The first targeted execution was blocked by quiet_guard exit3 (recording).
  No new tests, mutation experiments, real night or model calls ran.
- INPUTv2 allows staged source work only. The original descendant-retention risk
  remains an installation blocker; source review cannot attest a working system.

## Repeat when the resource gate permits

```sh
quiet_guard.sh && heavy_run.sh "$CHAROITE_PYTHON" -m pytest -q \
  tests/test_process_lock.py tests/test_stage_retry_wrapper.py \
  tests/test_retry_wrapper_integration.py tests/test_night_gate.py
```

The tests use synthetic paths/status/artifacts and fake commands. Signals target
only isolated fixture processes after reading their full command identities.
Cases include failed with rc0, interrupted/missing final status, current success,
brief, next-night rollover, preserved completed bytes, contention, exec failure,
symlink/hardlink/FIFO refusal, retained descendants and the close_fds limitation.
The same executable public shell fixture is checked before/after the transform,
including window/resource/other-owner guards, marker, UX and final source seam.
Fake commands use the actual unchanged public decision function and record UX,
heavy/brief and final-gate observations. This is not an installed private-wrapper
or full-nightly run. Staging integration tests also exercise private permissions,
manifest binding, non-overwrite and fixed CLI refusal without source disclosure.

Required targeted mutation experiments: remove flock acquisition, remove FD
inheritance, allow a symlink lock, and disable the wrapper hash/private-output
guards. Require a passing baseline and the exact related assertion failure,
not a generic failure/error/skip. The critical zones are registered in layout.
These mutations and full CI are still pending; no green result is claimed.

The private exact wrapper before/after function projection and candidate syntax
check also remain gated. The source patch can be reconstructed from the known
wrapper bytes using the committed staging utility; preserve the private base
copy if the installed wrapper later changes.

Кандидат исходников сохранён без изменения установки. Выполнение синтетики,
мутаций и проверка настоящих границ наследования FD ещё не пройдены. №955
остаётся незавершённой; история не доказывает потерю конкретного ночного повтора.

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
