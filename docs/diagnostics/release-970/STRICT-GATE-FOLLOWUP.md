# Neutral addressed strict-gate follow-up

Base definition79ea7fed419027c65f90f87d4c1d0d331a949544; runtime remains
50b0704fe280360b1d7f828fd601c6216959e55d and approved plan hash remains
30656652e03d97617fbd5b825d977e53061acb9e925d769822d5e34219742b99.
This bounded delta adds three controls. It is not a new mutator or a release
acceptance; actual682/publication remain0 pending the final source approval.

## Upload boundary

All three evidence uploads require success() && !cancelled(). A failed,
canceled or incomplete job publishes no new artifact. Missing evidence still
fails aggregate/external acceptance. The aggregate itself remains always-run so
that it can report missing/failed predecessors. Structural weakening and the
success/failure/cancellation truth table are checked; ordinary workflows are
unchanged.

## Independent definition anchor

External replay requires --expected-definition-sha, --expected-branch,
--approval-record and --approval-record-sha256 from a separately frozen root
record. The closed record contains exactly definition_sha, branch,
runtime_candidate and plan_sha256. Its bytes/hash, exact CLI pins, context,
actual definition checkout and clean tracked state must agree before platform
API reads. Root will freeze the final reviewed SHA/branch/runtime/plan hash
outside the run, then append the dynamic run/attempt in a separate context.
The definition does not hardcode its own future commit SHA. Coherent fake API
metadata for another valid SHA/branch is insufficient and rejected before API.
Final external result records the independently supplied approval record hash.

Example after root freezes the final record (placeholders are operator inputs):

```
python policy/scripts/release970_gate.py external --source runtime --out external-replay --context expected-run.json --expected-definition-sha APPROVED_SHA --expected-branch verify/release970-50b-strict --approval-record root-approval.json --approval-record-sha256 APPROVED_RECORD_SHA256
```

## Passive execution observation

The canonical engine has no existing actual-import journal: run_tests launches
[sys.executable,-B,-m,pytest,...] in its own clone, preserving environment except
CHAROITE_ROOT. A separate stdlib observer is copied alone as sitecustomize.py to
an isolated bootstrap directory passed only in the subprocess environment.
It removes only that directory from sys.path. No parent environment mutation,
canonical argv/runtime/test source edit, import replacement or outcome override
is introduced. An inherited nonempty PYTHONPATH or an existing sitecustomize
startup module is refused, not silently removed or shadowed.

The observer records the actual canonical process and every direct canonical
pytest child: PID/parent, argv and executable, cwd, Git HEAD/tree, start/elapsed,
filesystem source hashes, and primary source execution origins observed through
Python audit exec events. On this Mac, actual sys.orig_argv[0] is a framework
launcher while sys.executable is the virtualenv entry; both are preserved,
with exact remaining argv and interpreter/parent linkage. No -I shortcut is used.
Its own loaded sitecustomize source hash is measured from disk, not copied from
the expected configuration. Callback exceptions never alter exec or return code;
errors/missing receipts fail the wrapper's independent evidence policy.

Expected variant bytes are generated only by unchanged legacy plan_for/applied,
reading/writing a private temporary copy of each source file. The actual runtime
file is never modified for this preparation. Each K's key has one unique expected
(path,mutated SHA) and differs from baseline. At pytest startup, actual primary
checkout hashes identify baseline versus that exact key; the key's own source
must actually execute from that checkout with that mutated SHA. All direct child
suites, key order and counts, baseline suites, process/time linkage and canonical
origins are checked independently during collection and external replay.

Observation is deliberately scoped: the eight named canonical paths plus the
98 selected runtime/test/config files (overlap retained once). It does not attest
all third-party modules or auxiliary fixture subprocesses. Actual origins for
watched source files are retained; a same-name core source from another root or
installed package fails. No broad temporary-file allowlist is introduced.
Legitimate fixtures outside this primary scope can therefore cause a conservative
FAIL; any required exception must be justified separately, never silently counted
as approved target execution. The observer is not a VM/code-object attestation.

Closed per-K imports.json evidence binds context, K, actual PID linkage,
observer source and all process records. Limits:64 processes,256 unique observed
origins per process,100KB raw process receipt,4MB combined receipts,3600 seconds
per process. The canonical mutation budget/timeout/job envelope stays unchanged.
An observer error, foreign/unmutated source, wrong key/hash, missing child/linkage
or unreadable/extra schema fails. Only successful complete evidence can upload.

## Addressed experiments and limits

A before/after fixture uses the old public validator79ea: plausible Facts and
approved-hash claims pass without actual import proof; the new validator rejects
foreign-source evidence even with corrected member hashes. Native synthetic
fixtures run a fake canonical script with two real one-test pytest children:
baseline source returns0, changed source returns1; both actual origins/hashes are
recorded. With an installed unmutated substitute, child return codes remain0/0
but evidence fails. A callback exception preserves canonical/pytest return codes
and independently fails evidence. Missing/PID/key/hash/elapsed/schema negatives
are also exercised. These are mechanism experiments on synthetic sources, not
current-release mutant outcomes and not the90 selected runtime suites.

Two delivery-path corrections were also tested: CLI source/output paths are normalized before subprocess cwd changes, and the own temporary canonical report.txt.json is renamed to facts.json so the uploaded artifact contains exactly the six declared files. A fake producer drives the real collect and payload validator without a real mutant run.

See STRICT-GATE-FOLLOWUP-CHECKS.json for executed counters and exact source
hashes. Initial startup binding failure (23PASS/3FAIL) was fixed using measured
Mac argv/executable behavior, without relaxing missing-evidence refusal. Final
source remains unpushed; actual682, release, install and restart are unperformed.

## По-русски

Новый шаг сохраняет реальные пути и хеши кода из процесса мутатора и его прямых
pytest-процессов. Каждый ключ связывается с ожидаемыми байтами через прежний
legacy applied(), без правки рабочего кандидата. Ошибка наблюдателя или подмена
источника блокирует доказательство и выгрузку, сохраняя поведение канонического
движка. Внешняя приёмка также требует отдельную запись root с точной SHA и веткой.
