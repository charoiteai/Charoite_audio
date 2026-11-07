# Release970 strict gate — neutral OUTPUT

Runtime candidate is frozen50b0704fe280360b1d7f828fd601c6216959e55d,
base release102 is bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81.
Definition branch verify/release970-50b-strict starts at the runtime candidate
and adds only a finite verification tool, workflow, plan, tests and classification
metadata/documentation. Its exact definition HEAD/tree is recorded in the review
packet manifest; it must never be presented as the runtime SHA.

## Behavior and bounded scope

The unchanged candidate mutator still judges the actual mutants. The new
scripts/release970_gate.py wraps it, independently checks fresh primary JSONL
journals and Facts, and also requires canonical merge exit0. Exactly682 approved
critical IDs from eight paths are assigned by index modulo16; every expected
ID must occur once in its own ordered K journal as uncapped killed. Unknown,
incomplete, capped, malformed, extra, duplicate, substituted or stale evidence
fails. No old journal/key/job is resumed, read for coverage or unioned.

The on-push workflow checks out its definition SHA into policy and literal50b
into runtime, pins actual observed action SHAs, uses Ubuntu24.04 and Python3.12.16,
records actual native/package/image versions and validates98 frozen source/test/
config hashes. Sixteen fixed shards use the canonical command unchanged:

```
python runtime/scripts/mutate_check.py --range bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81..50b0704fe280360b1d7f828fd601c6216959e55d --only-critical --max 60 --timeout 120 --budget-s 3000 --shard K/16 --report REPORT
```

Each mutation step remains60min:3000+480=3480s. Shard bounded steps total93min
plus post-step margin within100min; plan/aggregate have45min ceilings. Ordinary
CI workflow, canonical engine, critical zone policy and runtime sources are
unchanged. The new definition tree is never used for canonical mutation/tests.
The only existing test change precisely scopes the artifact overwrite guard:
this exact workflow must use run ID+attempt+role/K names, error on missing files
and no overwrite. All other workflow uploads still require overwrite=true.
Negative fixtures weaken each exception condition and must be rejected.

The helper is correctly classified as a scripts CLI. Two layout metadata entries
register argparse help and explain why its definition-root bootstrap must remain
stdlib-only: importing the definition's product path canon would contaminate
canonical imports from the separately pinned runtime. The generated map changes
accordingly. No root-data exemption, critical zone or legacy code is removed.

## Two provenance chains and final acceptance

Internal receipts have closed typed schemas. GitHub run/attempt/repository,
definition SHA/branch/path, exact job roles/conclusions and artifact ID/digest/
run/repository/head links are checked through live API. Downloaded archive bytes
must match platform digests; archives have exact regular-file allowlists and no
extraction. The plan artifact is also checked. All16 artifacts must be fresh in
one attempt; subset retries fail. Other-attempt metadata is logged but payloads
are not opened for coverage.

An in-run aggregate cannot prove its own future completion. After that run is
completed, a separate operator must check out the exact reviewed definition and
run the external verifier with explicit frozen public context JSON containing
run_id, attempt, definition_sha, repo, branch and workflow:

```
python policy/scripts/release970_gate.py external --source runtime --out external-replay --context expected-run.json
```

That replay requires live completed-success run and all jobs, exact final artifact
and its downloaded digest, every primary shard/plan payload, another canonical
merge and byte-bound final receipt. Boolean/float counter substitution is rejected.
It also re-reads PR906 and requires unchanged50b/base00583289, ready/mergeable,
all checks completed with success except only the known CI push mutation selector/
verdict skips, and independent mandatory Linux/macOS/security/docs/supply/title/
Swift roles. Root must additionally read the actual platform facts and checkout/
tree binding. Neither synthetic PASS nor aggregate self-PASS authorizes release.

## Finite evidence and honest limitations

The19 grouped tests S01–S19 in tests/test_release970_gate.py exercise the accepted
INPUT2 matrix. S01 drives all16 synthetic journals through the real unchanged
canonical merge and independent final-artifact replay with fake transport only.
S02 proves actual canonical Facts merge can be green while strict rejection of
capped killed is mandatory. Remaining groups cover wrong IDs/counts/module/K,
typed/malformed/truncated data, source/config mismatch, failed/missing jobs,
stale API/attempt/artifact, unsafe ZIP, final failure, workflow weakening and
normal candidate CI failures. These are synthetic policy controls, not682 mutant
outcomes. No model, owner data, real mutation or application launch is used.

The first addressed invocation named a nonexistent layout-test file and ran0
tests. The first real run had76PASS/2FAIL: the harness assumed canonical merge
also emitted machine Facts, and the existing global overwrite test correctly
rejected the new immutable-attempt policy. The correction uses the real report
text+exit and primary per-K Facts, plus the precise tested guard exception above.
A later repeat had78 passing bodies but78 teardown errors because a manually chosen /tmp basetemp was outside the platform tempfile root; the command was corrected to the existing platform temp root, without weakening the guard. A first classification check rejected .github Python placement; helper placement
and explicit classification metadata corrected it. See STRICT-GATE-CHECKS.json for final
executed results and source hashes. None of these failures is a release PASS.

Actual current-release mutations=0, verification publication/runs=0, release
merge/publication/install/restart=0 for this implementation stage. OUTPUT requires
both independent reviewers and root acceptance before publishing this exact
frozen definition. Installed release remains0.102.0/build842; actual signed asset,
fresh backup, source FF, resource/recording/idle/no-call guards and installed UI
acceptance remain separate delivery gates.

## По-русски

Это конечная проверка доказательств нового критического прогона, а не замена
мутатора. Для каждого из682 ID нужен свежий uncapped killed в правильной доле,
канонический merge и независимая сверка уже завершённого CI с артефактами.
Настоящий прогон и поставка ещё не выполнялись; старые остановленные задания
не возобновляются, установленное приложение не меняется.
