# 975 — INPUT contract, not an implementation verdict

Base definition: `bbe5a6487cb016d3ef4529944b9e686a426354ca`.
Runtime candidate: `50b0704fe280360b1d7f828fd601c6216959e55d`.
Repair branch: `fix/release970-ci-shallow`; it must never match `verify/release970-*`.
Ordinary CI failure is preserved as failure on its original SHA. An existing
uncommitted publisher proposal changes two test files,16 insertions/13 deletions;
it has not been tested. Both INPUT reviewers receive exactly that proposal and
the same tracked-public baseline. Further implementation/commit requires rootGO
after both reviews. No earlier INPUT for this exact repair is claimed.

## Problem and permitted result

Ordinary Linux and macOS jobs use checkout depth1. One regression test executes
the old79ea validator through git-show; three native fixtures check out50b.
Neither historical object is part of the ordinary checkout. A fifth failure is
the permissions inventory discovering the new release970_gate CLI without
harden_umask or a named exemption. See PRIMARY-CI-RECEIPT.json for primary facts.

Only `tests/test_release970_observer.py`, `tests/test_private_permissions.py`,
and reports may change. Helper, observer, ordinary workflow, critical workflow,
frozen plan, canonical engine, selected runtime dependencies and runtime50b
must remain byte-identical tobbe. No release installation, model call, actual
release-mutant execution, push, PR, cancellation or rerun is authorized here.

## Proposed minimal route

Use the source checkout's actual available HEAD and tree to check out the native
synthetic fixture, then set its copied mini-plan's candidate/tree and command
range consistently with that available fixture identity. The proposal preserves
the plan's historical baseline as command text; the fake canonical script never
resolves that object. No production preflight/plan semantics are replaced by
this mini-plan. The synthetic script still runs two actual pytest subprocesses
against V=1 and V=2, restores the source and records their real return codes.
All Git object lookups performed by the permanent fixture must be satisfiable
from the available current checkout. No network fetch or history download may
be needed to make these tests work.

The permanent payload test must reject a missing imports.json even with a
coherent provenance member map, and reject a foreign-source observer error even
after its member hash is recomputed. The historical old79ea acceptance remains
explicitly attributed to the previously executed experiment in the tracked
STRICT-GATE-FOLLOWUP-CHECKS.json receipt. It is not rerun or silently replaced
with a claimed new before/after experience.

For permissions, use exactly one existing `UMASK_FORGIVEN` key for
`scripts/release970_gate.py`, with a substantive reason. Static code facts:
the helper's top-level imports are stdlib; required source/out arguments are
resolved explicitly; source preflight binds the public candidate/tree, tracked
cleanliness, ancestry and dependency hashes before engine loading; engine
imports are checked against that separate candidate; outputs are CI evidence
under explicit out/fresh temporary locations. Collection sets CHAROITE_ROOT to
the isolated runtime checkout. It does not discover an owner's meeting store.
This justifies classification as a public evidence CLI, like the already named
canonical mutator exemption. It does not claim artifacts receive mode0600.
Do not import a second path canon into the helper, change its production bytes,
broaden an allowlist, alter the AST/inventory logic, or exempt any other CLI.
The existing nonempty-reason, stale-entry, unknown-entry, AST and inventory
negative tests remain binding for all other entry points.

## Finite acceptance matrix

| ID | Scenario | Required evidence/result | Current status |
| --- | --- | --- | --- |
| C01 | Ordinary Linuxbbe/depth1 | Five exact original failures,11128 passes | Primary receipt recorded |
| C02 | Ordinary macOSbbe/depth1 | Same five failures,11140 passes | Primary receipt recorded |
| C03 | Dedicatedbbe full-history |27 policy tests pass; exact existing job | Primary receipt recorded |
| C04 | Missing imports payload + coherent hashes | GateError payload allowlist | Future run required |
| C05 | Foreign-source error + coherent member hashes | GateError observer error/foreign source | Future run required |
| C06 | Native valid source | Real children rc[0,1],3 process records, mutated executed origin/hash | Future run required |
| C07 | Native installed unmutated substitute | Real children rc[0,0], foreign-source error, evidence rejected | Future run required |
| C08 | Native observer callback exception | Real children rc[0,1], errors bounded at16, evidence rejected | Future run required |
| C09 | CLI relative source/out and exact collect payload | Existing path control and six-file archive assertions remain | Future run required |
| C10 | Closed observer negative matrix/startup shadow | Existing schema/PID/key/hash/time/error/launch refusal assertions remain | Future run required |
| C11 | Private permissions inventory | Exact one-key classification; all unknown/reason/stale/AST/inventory negatives remain | Future run required |
| C12 | Isolated one-commit checkout of repair SHA | Target tests pass with79ea and50b historical objects unavailable | Future run required |
| C13 | Exact repair SHA ordinary Linux+macOS | Both complete suites and other applicable mandatory CI pass | RootGO/publication required |
| C14 | Frozen-byte comparison and reviews | Entire diff only2tests+reports;104 canonical/selected dependency union and all verifier/workflow/plan bytes unchanged; independent OUTPUT/privacy/hooks | Future acceptance required |

After both INPUTs/rootGO, local commands must remain behind fresh native
quiet_guard0 and the established heavy_run chain. Minimum targeted command:
`python -m pytest tests/test_release970_gate.py tests/test_release970_observer.py tests/test_private_permissions.py -q`.
Repeat the same target set from an isolated depth1 clone of the exact committed
repair SHA, confirming the two historical objects are absent first. These are
synthetic policy/fixture tests; their fake script is not a release-mutant run.
If local gate stays3, do not execute local tests. C12 then remains pending unless
a separately permitted isolated depth1 run records the exact repair SHA, shallow
status, negative object checks for both historical79ea and50b commits, and the
actual three-file target invocation. Authorized exact-SHA ordinary public CI is
C13 evidence only; it does not replace C12. Full ordinary workflows stay unchanged.

## Evidence identity and stop conditions

Existing actual proof is run42388856807/attempt1, definitionbbe/runtime50b.
It remains separate from the proposed repair SHA and ordinary CI run. This
repair must not publish to verify/release970-* or restart682. Root evaluates
whether unchanged verifier/kernel bytes support applicability; any reuse must
name the original SHA/run and unchanged dependency set. This INPUT does not
grant that acceptance and does not relabel existing evidence.

Stop for any scope drift, frozen-byte change, shallow check still needing old
objects, skipped/removed checks, broad umask exception or unclear raw-data
boundary. Record the specific remaining failure; do not report partial tests
as full acceptance. Reviewers return Critical/Important/Minor with path/line,
scenario and minimal fix; a code read is not an executed experiment.
