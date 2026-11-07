# Release 980 — supplemental external PR metadata validation (INPUT ONLY)

This proposal does not change or reissue the frozen release-970 proof. No implementation is included. The root-reported vanilla external failure is not an external PASS; merge remains HOLD.

## Exact source and observed fault

All source below is pinned to definition `74ad984b3b9d4be17586d607b27cd3f44924c459`. The release candidate remains `94867ecb54593018b0568a18fa9501770cdfb768`; literal trust anchors are in `PINS.json`.

- `scripts/release970_gate.py:437-455`: checks every visible PR result, then compares a global list of skips with exactly one pair. The original mandatory-success role set must remain unchanged.
- `scripts/release970_gate.py:510-555`: external root approval and clean frozen checkout, runtime preflight, PR check, platform/artifact validation, canonical merge, then exact CI final-receipt comparison.
- `.github/workflows/ci.yml:221,334`: each push run skips `mutation (changed lines)` and `mutation verdict`; PR runs execute four mutation shards and verdict.
- `fixtures/pr-906-rollup.json`: 28 unique PR rows = 24 SUCCESS + 4 SKIPPED. `runtime-check-runs-all.json`: 29 unique complete REST checks = 25 success + 4 skipped, `total_count == len(check_runs)`. These are separate raw responses, not normalized replacements.
- REST-only `copilot-pull-request-reviewer` check 128502032210 is successful, linked to run 42405444700 / suite 114884774211, event `dynamic`, path `dynamic/agents/copilot-pull-request-reviewer`. Its absence from the GraphQL rollup is recorded, not treated as equality or discarded.
- Actual CI push runs 42403270503 and 42405431329 each contain three successful regular jobs plus exactly one skipped pair. PR run 42405436253 contains three successful regular jobs, four successful mutation shards, and successful verdict. All use the same candidate SHA and official CI workflow 317312253 / `.github/workflows/ci.yml`.

The raw endpoints, local file-write UTC times, lengths, and SHA256 hashes are in `FIXTURE-MANIFEST.json`. Static inspection above is not a policy experiment or a new critical run.

## Architecture decision and alternatives

Choose a new, single-file stdlib-only supplemental external entry point in its own reviewed commit. Authenticate its exact commit and source SHA256 from a separately committed root supplemental approval record, authenticate the original gate file digest before import, load the original file directly from the immutable definition checkout, replace only the module-global `validate_release_pr`, and invoke the original `main()` with the original external arguments. The validator closes over its own metadata collector; it must not patch original `api`, `git`, `ROOT`, `preflight`, `aggregate`, artifact logic, mutator, observer, or receipt functions. Verify original object identities/ROOT around the one deliberate symbol replacement. A new process starts every invocation.

This preserves the original main-level definition check and aggregate-level root approval, tracked cleanliness and runtime preflight; the original canonical source/plan hashes, import receipts, ZIP digests, exact 682-key multiset, canonical `--merge-shards`, and CI final receipt comparison still execute unchanged. The original four-field root approval and every cohort record stay byte-for-byte unchanged. This does not represent the vanilla frozen executable as passing.

Alternatives: editing the frozen gate invalidates the pinned definition and is rejected; copying the entire aggregate creates unnecessary proof divergence; filtering/deduplicating skip names or allowing a literal four skips hides provenance and is rejected. The in-memory seam is acceptable only after both reviewers verify the bounded replacement and trust binding; otherwise the minimal fallback is a separately reviewed exact copy of only the external orchestration that calls unchanged original validators, with its additional divergence explicitly reviewed. No fallback is authorized by this packet.

## Replacement validator: finite policy

1. Read raw `gh pr view` readiness and REST PR identity plus complete same-candidate REST `check-runs?filter=all`. Check `total_count` against all retained rows (follow pages or fail closed); do not rely on latest-only filtering. Preserve both raw surfaces. Check types, JSON duplicate fields, unique IDs/URLs, repository, candidate head and release base, open/non-draft/mergeable state. Unknown check types fail closed.
2. Inspect the complete observed union. Match each rollup row injectively to a REST check by exact official URL/ID and name/status/conclusion; record REST-only rows with their provenance. Reconcile the explicit successful Copilot role above; do not require artificially identical row sets. Every observed check must be completed and successful except an authenticated push skip pair. An unexpected check/provenance or unknown skipped role fails closed; there is no blanket success-role wildcard.
3. Resolve GitHub Actions job URLs to official repository run/attempt/job GETs and check suites. Require positive unique identities, exact URL/run/job/check links, complete job listings, official app identity, suite/run/job candidate SHA, repository and workflow identity/path, run status completed/success. Reject aliases, foreign repositories/apps, missing pages, unknown events, and link mismatch. For non-CI mandatory roles bind existing official workflow paths and job names. Bind the independent `CodeQL` check to the GitHub Advanced Security app/suite/repository/head. Bind only the explicit Copilot extra to its observed GitHub-managed dynamic path/event; it cannot satisfy any mandatory role.
4. Group skipped rows by authenticated `(repository, workflow_id, run_id, run_attempt)`. Every contributing CI push run must have exactly the unsharded mutation row and verdict row, both skipped, plus the official complete successful normal CI jobs. No other skip is allowed. Require the two exact pair roles once each within a run, not a global skip count; reject unpaired, repeated, extra or unknown skipped jobs. Enumerate every observed CI run, including ones with no skipped row, so a missing pair cannot disappear from consideration.
5. Require the original mandatory role set unchanged. CI PR mutation shards `(1)` through `(4)` and verdict must be successful in one coherent official `pull_request` run tied to PR 906, its exact head/base and repository; a push success or cross-run mixture cannot substitute. Every observed CI push run must itself be completed/successful, with all its retained checks/jobs judged. Failed or incomplete historical/all-filter checks remain failures; no latest/green-only selection is allowed.
6. Re-read head/base/readiness and complete metadata after the original artifact/canonical replay; compare validated identities/status/conclusions and reject drift or a new/removed check/run. Keep both raw observations; differing representations remain explicit. Do not attach the extra data to the original CI final receipt or change its schema.

## Proposed CLI, trust and output contract

The future supplemental script is `scripts/release980_external.py`. Exact CLI (paths are operator placeholders, never trust anchors):

```sh
python -B "$SUPPLEMENT/scripts/release980_external.py" \
  --definition-root "$FROZEN" \
  --supplemental-approval-record "$SUPPLEMENT_APPROVAL" \
  --supplemental-approval-record-sha256 ROOT_COMMITTED_LITERAL_SHA256 \
  --pr-evidence-out "$PR_EVIDENCE" -- \
  external --source "$RUNTIME" --out "$OUT" --context "$CONTEXT" \
  --expected-definition-sha 74ad984b3b9d4be17586d607b27cd3f44924c459 \
  --expected-branch verify/release970-32-94867ecb-74ad984b \
  --approval-record "$ORIGINAL_APPROVAL" \
  --approval-record-sha256 83724a385fa27e7b0eaaf42c509b71cae97f06b5bd14e26dcb689a6471d1c40d
```

The new root record has exact schema `{v:1, task:980, verifier_sha:<40hex>, verifier_sha256:<64hex>, frozen_definition_sha:<40hex>, frozen_gate_sha256:<64hex>, original_root_approval_record_sha256:"83724a385fa27e7b0eaaf42c509b71cae97f06b5bd14e26dcb689a6471d1c40d", pr_policy_fixture_manifest_sha256:<64hex>, scope:"replace_validate_release_pr_only"}`. Its byte hash is a root-selected literal, independent from input metadata, context and output. Match verifier commit/file/cleanliness and original definition/gate bytes before loading. `PINS.json` leaves all supplemental implementation pins null until reviewed code exists; it is not an approval record.

Preserve original `strict.json` generation and exact CI receipt comparison. Retain every raw PR/check/suite/run/job response under the separate evidence directory, including failure evidence. After successful original replay and unchanged final metadata recheck, write a separate versioned `supplemental-pr-metadata.json` sidecar with exact fields: `v`, `kind="supplemental_external"`, `outcome="PASS"`, `verifier_sha`, `verifier_sha256`, `supplemental_approval_record_sha256`, `frozen_definition_sha`, `frozen_gate_sha256`, `original_root_approval_record_sha256`, `runtime_candidate`, `plan_sha256`, `critical_run_id`, `critical_attempt`, `pr_number`, `head_sha`, `base_sha`, `raw_response_manifest`, `reconciliation`, `authenticated_push_groups`, `mandatory_pr_run`, `frozen_strict_sha256`. Strict types, no duplicate/extra fields. The sidecar must explicitly say this is a supplemental run; neither CI artifact nor root approval changes. Failed post-replay recheck means no supplemental PASS even if the original replay wrote a receipt.

## Planned controls — same inputs BEFORE/AFTER, NOT RUN

| ID | Single input or controlled delta | Frozen BEFORE expected | Supplemental AFTER expected |
|---|---|---|---|
| P01 | Complete captured rollup28 / REST29; two valid push pairs | Reject: global expected push skips | Accept PR metadata only; not external PASS |
| P02 | One valid push group, coherent PR successes, matching union | Accept original shape | Accept authenticated equivalent |
| P03 | Add a third distinct valid push run plus complete exact pair | Reject global skip list | Accept with third authenticated group; no hardcoded count |
| N01 | Delete one pair row, duplicate one row/ID/URL, or split pair across runs | Reject or historically weak; record actual | Reject each delta |
| N02 | Unknown/extra SKIP or skipped mandatory PR shard/verdict | Reject | Reject each delta |
| N03 | Failed, cancelled, queued, incomplete or neutral row, including REST-only | Reject when visible / blind to REST-only | Reject each delta, no filter |
| N04 | Wrong repo/app/workflow/event/head/base/PR/job-link/attempt or truncated pages | Original lacks new provenance checks | Reject each delta |
| N05 | Remove a mandatory role; satisfy four PR shards across different runs; omit entire push pair | Reject missing visible role / provenance weak | Reject each delta |
| N06 | Head/base/check/run drift between first and final observations | Original only initial read | Reject each delta |
| N07 | Change supplemental pin, original gate bytes, ROOT, approved branch/record, runtime/plan, or extra patched symbol | Reject existing frozen pins where applicable | Reject before replay, retain original checks |
| N08 | Corrupt artifact digest/import receipt/key identity/canonical result/final CI receipt | Reject original protection | Reject unchanged original protection |
| E01 | Same immutable run42405663537/attempt1 replay after INPUT/code/OUTPUT reviews | Root already observed vanilla failure; no rerun here | Original full external checks + explicit supplemental sidecar; no new mutants |

Shallow-checkout regression boundary: metadata tests use pure strictly typed captured/synthetic fixtures. Git-fixture tests use their actual fixture HEAD/tree; they must not resolve historical 74ad/9486 objects in depth-1 CI. Stubs validate only their stated unit boundary and never count as an actual frozen full-34-job integration. The later root native replay is the separate integration gate.

Only after both clean independent INPUT verdicts and root GO: implement the bounded script and meaningful finite controls, run them through the project resource gate, review the exact new code snapshot with Terra and Luna and pass ordinary CI on the fix980 branch, then root schedules one corrected external replay. Any new CI push is limited to the fix980 branch; neither the frozen verify32 branch nor runtime9486 is pushed or changed. This docs-only INPUT authorizes no actual682 mutation rerun, CI write, push, merge, release, signing, install or owner operation; later fix980 CI is a separate root action after GO. A malformed/incomplete review is not a clean verdict. Rollback is simply not using the supplement; historical proof remains intact.
