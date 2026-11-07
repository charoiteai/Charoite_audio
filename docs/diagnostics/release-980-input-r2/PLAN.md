# Release 980 — revised supplemental external validation INPUT

Documents only; implementation is not authorized. This plan supersedes the earlier plan without changing the original 63-file input packet, source or raw fixtures. Vanilla frozen external has not passed; merge remains HOLD. Both independent revised INPUT reviews must be C0 before root code GO.

## Frozen boundary and unchanged observation

Exact source: definition `74ad984b3b9d4be17586d607b27cd3f44924c459`, gate SHA256 `efbb97708d07e1a0654155516711f7c303154c6a22a1976d8fef196b81bcc4bf`; runtime `94867ecb54593018b0568a18fa9501770cdfb768`; plan SHA256 `091c9596f348066b323d22c6888872625c8c9d92f635dd7f8d382958f3bc3717`. Run42405663537/attempt1 and branch `verify/release970-32-94867ecb-74ad984b` stay fixed. The original four-field root approval and hash `83724a385fa27e7b0eaaf42c509b71cae97f06b5bd14e26dcb689a6471d1c40d` remain unchanged. All exact pins are in `PINS.json`.

Static source: `validate_release_pr:437-455` permits exactly one global skipped pair; official CI workflow `ci.yml:221,334` produces one skipped pair per push run. Original `aggregate:519-522` owns its `CompletedProcess pr` and passes `strict_json(pr.stdout)` to the validator. Original `aggregate:551-560` compares the final CI receipt and then writes raw `strict=PASS/external_completed_run_verified=true` before returning. That raw output alone must never reach the delivery/consumer path.

The unchanged captured GraphQL rollup is 28 rows (24 SUCCESS, 4 SKIPPED); REST `filter=all` is 29 (25 success, 4 skipped), with complete counts and unique identities. Both valid push runs (42403270503, 42405431329) contain a complete skipped pair. PR run42405436253 has successful four mutation shards and verdict. The sole REST-only successful Copilot check128502032210/run42405444700 has explicit GitHub-managed dynamic provenance. Preserve both raw surfaces separately; do not force identical sets or hardcode counts. The copied source/fixture manifests are byte-identical to the first packet, including timestamps and hashes; no fresh capture or experiment is claimed.

## Trusted launch before verifier execution

The trusted ROOT supervisor, independently of the candidate verifier, must check the root-selected supplemental record's exact byte hash, verifier clean tracked commit and file SHA256, frozen definition/gate hashes, old approval/context/plan pins, and the approved invocation/destination. These checks occur BEFORE executing candidate Python code. A verifier self-check is defense in depth and cannot establish its own trust. The supervisor executes exactly the verified bytes from a sealed private snapshot (no unchecked reread of a mutable source path); an untrusted or stale script must not execute even if it would claim that its self-check passed. No generic new launcher service is proposed: ROOT performs this bounded launch gate and records the check/exec/exit outcome.

Supplemental root record schema is exact: `{v:1, task:980, verifier_sha:<40hex>, verifier_sha256:<64hex>, frozen_definition_sha:<40hex>, frozen_gate_sha256:<64hex>, original_root_approval_record_sha256:<64hex>, pr_policy_fixture_manifest_sha256:<64hex>, scope:"replace_validate_release_pr_only"}`. Its hash is an independently ROOT-pinned literal. Production values must match `PINS.json`; null supplemental pins remain unapproved. Invocation binding additionally records a fresh ROOT-selected random invocation ID, exact context bytes/hash, original approval bytes/hash, original-core arguments, and delivery parent identity/basename. A prior invocation or copied stale output cannot satisfy the current launch.

## Exactly one frozen module seam

Load the authenticated original gate file directly from the unchanged frozen checkout. Keep its original ROOT, main and aggregate function/code identities, all globals and all dependency objects. Replace ONLY module-global `validate_release_pr`, then call the captured ORIGINAL `main()` exactly once. A fixed argument builder always selects original mode `external`, the exact original trust/source/context pins, and a newly allocated private core-output child; refuse other modes, `--K`, duplicated/unknown forwarding and caller-supplied normal delivery `--out`. No replacement of `api`, `git`, subprocess, parser, preflight, ROOT, observer, mutator, artifact checks or receipt comparison is permitted. No trace hook, general frame scanner or alternate API orchestration is permitted. Compare the original module namespace and protected function/code identities before invocation and after return; the one authorized validator replacement is the only module-global delta.

The original main-level definition guard, aggregate-level root approval/clean checkout, runtime preflight, 32-shard platform/ZIP/import receipts, canonical source hashes and exact682 key multiset, canonical `--merge-shards`, and typed final CI receipt comparison all still execute in the ORIGINAL functions. This remains an explicitly supplemental execution; vanilla74ad is not described as passing. If the narrow seam cannot be authenticated, stop; copying/patching the frozen aggregate is not a fallback authorized by this plan.

## Original PR stdout: narrowly bounded capture

The replacement accepts the original `(pr, p)` parameters. A second `gh pr view` response is never described as the original response. Before any REST collector work, the replacement uses only the immediate caller frame (`sys._getframe(1)`), verifies its code object is the captured ORIGINAL aggregate code, globals dictionary is the authenticated original module, filename is the frozen canonical gate path, and call location is the pinned original validator call at line522. Pin the exact supported CPython3.12 runtime/version and source/call-site contract in the launch evidence; absent/unsupported frame shape fails closed.

Read ONLY the immediate caller's named local `pr` CompletedProcess and its immutable `stdout` bytes. Do not enumerate/log frame locals, inspect any other local, follow `f_back`, walk the stack, mutate the frame, or patch subprocess. The original frozen code already fixes the command and checks its return code before the call. Require exact CompletedProcess/bytes types; capture bytes before releasing the frame reference in `finally`. Strictly parse captured bytes using the unchanged duplicate-field/type-preserving parser, and require `same_typed(parsed_raw, supplied_pr)` plus typed plan equality to the trusted frozen plan. A mismatch, duplicate field, fake caller, second invocation or missing invocation fails closed. Retain exact captured bytes, length, SHA256 and observation time; never re-encode them as a substitute raw response.

The replacement's initial REST PR/check/suite/run/job collection is bound to that captured typed GraphQL snapshot: reconcile readiness/head/base and every shared check, record explicit representation differences, and perform an immediate collector-end reread for observed drift. A reread is a separate raw observation. This brackets observations and rejects observed changes; it does not claim an atomic GitHub snapshot.

The frame facility is a CPython implementation detail and may be unavailable; the approved behavior is refusal, not a broader capture mechanism. [CPython documentation](https://docs.python.org/3.12/library/sys.html#sys._getframe).

## Complete union and authenticated role policy

1. Preserve exact raw bytes for the original rollup and every independent REST/recheck response. Collect all same-candidate REST `check-runs?filter=all`; complete pagination or fail closed. Validate strict schemas/types, complete row counts, unique check/run/job identities and links. No latest-only, green-only, name deduplication or dropped historical failures.
2. Reconcile the complete observed union. GraphQL `detailsUrl` must match the actual REST check's `details_url` or `html_url` according to its explicit observed check type, with matching name/status/conclusion and official app/suite/head. If both URL fields exist, retain and validate both. Resolve REST numeric check IDs from returned ID fields and exact `check_run_url` records; never infer an ID from a URL suffix. A CodeQL URL `/runs/<id>` belongs to its Advanced Security check/suite and is not an Actions job URL. Conflicting or ambiguous mappings fail closed.
3. Every observed row is completed/successful except an authenticated official CI push skip pair. Each Actions check must link through exact repository run/attempt/job/suite/app/workflow/event/head/URL fields and complete job lists. Non-CI required roles bind their existing official workflow paths and job names. The independent CodeQL check binds the official Advanced Security app and matching repository/head/suite; it cannot be forced through Actions URL parsing. Explicit successful REST-only Copilot binds its observed managed dynamic path/event and cannot satisfy a mandatory role. Unknown check/provenance, failed/incomplete row or unknown SKIP is rejected.
4. Group ALL observed CI runs by `(repository, workflow_id, run_id, run_attempt)`, including a run with no retained skipped row. Every official same-SHA CI push run must be completed/successful, have its complete normal successful jobs and exactly one skipped unsharded `mutation (changed lines)` plus one skipped `mutation verdict`. Reject missing, duplicated, unpaired, cross-run or extra skips. No global skip-count allowance.
5. Preserve the exact original mandatory-success role set. Four PR shards `(1)` through `(4)` and successful verdict must belong to one coherent official CI `pull_request` run tied to PR906 and exact head/base/repositories; no cross-run mixture or push substitute. All retained check/job conclusions are judged; no failed/incomplete result is filtered.

## Full post-replay comparison, with honest temporal limits

After original main returns integer0 and the genuine original invocation is established, collect the complete metadata again. Compare the policy-relevant projection below, including identity sets, field presence/types and all policy decisions, to the initial validated collection and captured original rollup. Reject new/removed check/run/job identities or any projected drift; retain both raw phases and mismatch evidence. Normal timestamp/description noise outside this declared projection is retained in raw evidence but not silently promoted into a policy condition.

| Surface | Required comparison projection |
|---|---|
| PR GraphQL | `headRefOid`, `baseRefOid`, `state`, `isDraft`, `mergeable`; row type/name/workflow/status/conclusion/`detailsUrl` and matched REST ID |
| PR REST | number, state/draft/mergeable/mergeable_state; head/base SHA/ref and repository ID/full_name; check association to PR906 |
| REST check | ID/node ID/name/head SHA/status/conclusion; `html_url`, `details_url`, app ID/slug, suite ID and corresponding rollup match or explicit REST-only role |
| Suite | ID/head SHA/branch/status/conclusion, app ID/slug, repository/head-repository identity and exact check/run membership |
| Actions run | ID/attempt/workflow ID/path/event/head SHA/branch/status/conclusion/check-suite ID/HTML URL/repository/head-repository; PR-number/head/base/repository association |
| Actions job | ID/run ID/attempt/name/workflow/head SHA/branch/status/conclusion/HTML URL/check-run URL; exact complete job membership |
| Collections | complete page counts, all unique row IDs/links, authenticated push groups, coherent mandatory PR run and representation reconciliation |

Missing policy-required fields fail validation; do not manufacture fields absent from an endpoint. A value used for any acceptance or provenance decision must be in the projection. Two unchanged observations prove only that no drift was observed in those readings; neither GitHub atomicity nor freedom from changes between/after readings is claimed. Root revalidates normal merge prerequisites separately.

## Private staging and atomic no-overwrite publication

The operator-requested normal delivery path is ONE regular-file bundle basename, never a directory exposing `strict.json`. The trusted ROOT supplies a pinned trusted owner-only destination parent (open directory FD plus device/inode/owner/mode identity), a fresh single-component basename and invocation ID. Reject symlinked/untrusted parents and unsupported descriptor-relative operations. Existing basename of any type, including a stale PASS, symlink/dangling symlink, directory or hard link, causes refusal; preserve it unchanged and never regard it as this invocation's result.

Allocate an unpredictable exclusive0700 staging directory on the SAME local filesystem under the trusted parent, owned by the current authorized user; verify owner/mode/device/inode with descriptor-relative no-follow operations. The original core receives only a nonexistent child `stage/core-out` as `--out`; it must not receive the destination or any pre-existing/symlinked core-output path. Its unmodified raw PASS can exist only inside this private evidence stage. Do not use automatic failure cleanup; preserve private logs/raw metadata/partial core output and error phase. Staged raw `strict.json` is NEVER consumer input, regardless of its PASS fields.

Only after genuine original main/aggregate success, the full final metadata check, module-identity checks and all digest/schema validations succeed, create a fresh regular bundle file with exclusive no-follow creation inside the stage (0600 while writing). Include exact original `strict.json` bytes (no rewrite), original `canonical.txt`, supplemental sidecar, bundle manifest, and both raw metadata phases plus their manifest. No historical shard archives are copied. Verify exact archive entry allowlist, no duplicate/traversal/symlink entries, sizes and digests by reopening only regular no-follow files. Flush/fsync the complete file, seal to0400 and sync required metadata, close every writable handle, then reopen read-only/no-follow and verify descriptor/inode/link identity (one private link) and all bound bytes before publication. Unsupported permissions/fsync/filesystem semantics fail closed. The trusted parent/stage are not writable by untrusted parties; concurrent hostile same-UID/root mutation is outside this trusted-supervisor boundary.

Publish by exactly one descriptor-relative POSIX hard-link operation: `os.link(sealed_basename, delivery_basename, src_dir_fd=stage_fd, dst_dir_fd=parent_fd, follow_symlinks=False)`. This exposes the complete sealed file atomically and refuses an existing destination with `EEXIST`, including a concurrent contender. It does not overwrite and needs no check-then-create race. Never fall back to ordinary rename/replace, unlinking a destination, copying into the destination, or creating a destination directory and filling it. `EXDEV`, missing hard-link/dirfd/no-follow support, network/unsupported filesystems, bad owner/mode, source symlink or unknown capability means refusal. This is a narrow proof-bundle operation, not a general publisher. [Python descriptor-relative link](https://docs.python.org/3.12/library/os.html#os.link), [GNU link/linkat errors](https://sourceware.org/glibc/manual/latest/html_node/Hard-Links.html), [Apple atomic link and EEXIST contract](https://developer.apple.com/library/archive/documentation/System/Conceptual/ManPages_iPhoneOS/man2/link.2.html).

After link, verify that the destination's no-follow regular-file identity is the sealed inode, the two expected aliases account for its link count, recheck bundle hash and fsync the parent directory before returning0. Do not mutate the sealed inode via its staging alias. Retain private staging until acceptance is independently recorded; any later cleanup removes only this invocation's validated private entries/aliases, preserves the published link and all failure evidence, and never unlinks an existing/foreign destination. Cleanup failure is reported honestly; do not let cleanup rewrite a result.

Partial writing, fsync/link/identity failure, exception, postcheck failure or interruption gives no accepted PASS. Before atomic link, the requested path remains absent (or an existing path is unchanged). An interruption or fsync/error AFTER successful link can leave a COMPLETE bundle at that basename; it remains UNACCEPTED without the separate trusted ROOT-observed successful exit and current invocation binding. Do not claim atomicity between a filesystem link and process exit. Do not delete an unknown/raced path to simulate rollback.

## Bundle binding and consumer acceptance

The exact versioned supplemental sidecar binds: kind=`supplemental_external`, outcome=PASS, fresh invocation ID, verifier commit/file hash, supplemental record hash, frozen definition/gate hash, old root approval hash, exact context byte hash and typed context, runtime/tree/plan, critical run/attempt, PR906 head/base, original raw PR stdout hash, all raw-response manifest/projection hashes, explicit union reconciliation and authenticated groups/coherent mandatory PR run, and original strict/canonical byte digests. Strict schemas/types and no duplicate/extra fields; all pins agree with the unchanged original strict receipt. The sidecar never changes the CI receipt or original four-field approval.

The bundle manifest separately binds strict, sidecar, canonical and raw-evidence manifest/member digests plus code/records/context/invocation. This avoids self-hashing cycles: sidecar binds strict/canonical/raw manifest; outer manifest binds sidecar too; the trusted ROOT acceptance record binds the WHOLE sealed bundle digest and observed exit0. Preserve exactly original strict bytes; no field is added to that receipt. Keep all retained raw bytes, including detected drift and failure evidence, private until normal authorized handling.

Consumer acceptance is conjunctive: trusted pre-exec check/exec record for the CURRENT fresh invocation; original-wrapper process exit exactly0 independently observed by ROOT; expected fresh no-follow regular-file bundle at the pinned destination; whole bundle digest/inode match; valid bound sidecar+manifest+strict+canonical+raw manifests with all context/code/root pins; both final metadata phases accepted. A staged receipt, sidecar alone, arbitrary file named strict.json, stale bundle, log string PASS, unconfirmed exit, interrupted run or nonzero/exception is never accepted. Existing stale material remains unchanged but cannot satisfy the current invocation. No consumer may discover/follow the private stage automatically.

## Proposed bounded operator contract

ROOT first performs and records its independent pre-exec gate. The verifier then receives only: frozen root, runtime source, exact context and original approval files/hash; supplemental approval file plus independently pinned hash; trusted destination directory FD/identity and fresh delivery basename; fresh invocation ID. It constructs the original main's fixed `external` argument list with private `--out` itself. No forwarded mode or normal delivery `--out` is supported. Planned CLI contract (placeholders only; the script does not exist yet):

```text
release980_external.py
  --definition-root FROZEN
  --source RUNTIME
  --context CONTEXT
  --approval-record ORIGINAL_APPROVAL
  --approval-record-sha256 83724a385fa27e7b0eaaf42c509b71cae97f06b5bd14e26dcb689a6471d1c40d
  --supplemental-approval-record SUPPLEMENTAL_APPROVAL
  --supplemental-approval-record-sha256 ROOT_PINNED_LITERAL
  --delivery-dir-fd INHERITED_ROOT_DIRECTORY_FD
  --delivery-parent-device ROOT_PINNED_INTEGER
  --delivery-parent-inode ROOT_PINNED_INTEGER
  --delivery-name FRESH_SINGLE_COMPONENT_BUNDLE_NAME
  --invocation-id ROOT_FRESH_RANDOM_HEX
```

The independently ROOT-approved context/record supplies exact definition/branch/runtime/plan pins; the fixed original argument builder passes them unchanged and substitutes ONLY private staging for original `--out`. This CLI accepts no positional mode, forwarding delimiter, `--out`, `--K`, duplicate flags or unknown arguments. Raw evidence/staging location is allocated internally under the trusted descriptor, never chosen as a normal delivery directory. The inherited FD is checked by fstat; the trusted ROOT also records its owner/mode and canonical authorized parent. There is no implementation or runnable command in this INPUT revision.

## Finite planned controls — NOT RUN

Every row is a planned same-input BEFORE/AFTER or single-delta control; source reads are not experiments. Reuse original finite metadata negatives; add the following bounded cases.

| ID | Input/control | Required outcome |
|---|---|---|
| P01-P03 | Captured two valid push groups; one group; third distinct valid group | Metadata accepts authenticated run-local pairs without a hardcoded total; no standalone external PASS |
| N01-N05 | Missing/duplicate/cross-run pair; unknown SKIP; failed/incomplete/REST-only failure; foreign metadata/link/app/workflow; missing/mixed mandatory PR roles | Reject every variant; retain complete evidence |
| T01 | Wrong/stale verifier commit/file/record or a script that would write an execution marker | Trusted ROOT rejects BEFORE candidate execution; marker absent; no bundle |
| T02 | Wrong original gate/source/approval/context/branch/runtime/plan pin | Distinguish pre-import pin failure from an actual inside-original-core runtime/artifact rejection; never label a stub as the latter |
| C01 | Original aggregate frame, exact original stdout bytes and supplied typed value | Authenticate one callback; exact byte/hash retained; REST collection binds the same typed initial snapshot |
| C02 | Second read substituted as original; fake/wrong caller/location/code/globals; wrong CompletedProcess/stdout types; duplicate JSON field or typed inequality; repeated/missing callback; unsupported CPython | Reject each case; no stack walk or extra global patch |
| D01 | Change ANY projected PR/check/suite/run/job identity/readiness/event/workflow/app/attempt/URL/status field, membership or page count between initial/collector-end/final reads | Reject observed drift; preserve actual different responses; no atomic-snapshot claim |
| U01 | GraphQL maps only actual REST html_url; only details_url; CodeQL non-Actions URL; conflicting/ambiguous mapping or fabricated suffix ID | Accept only unambiguous documented endpoint linkage; reject fabricated/conflicting variants |
| H01 | Harness executes source-identical original main/aggregate in external mode with fixed original pins and only one namespace replacement | Observe genuine original function/code identities and bounded callback/inside-core failure provenance; no synthetic success may count as real frozen integration |
| H02 | Fake success/no original invocation, swapped main/aggregate/code/global, wrong mode/forwarded args, exception or nonzero | Reject even if a forged strict PASS or sidecar exists; delivery unaccepted |
| H03 | Actual original runtime/preflight/artifact/import/canonical/final-receipt failures | Reject through actual original code; record exact boundary reached, separate from pre-import failure and any isolated helper stub |
| B01 | Original core writes raw external PASS, then final metadata check fails/throws/is interrupted | Raw receipt stays only in0700stage; delivery absent/unchanged; no accepted PASS |
| B02 | Existing delivery regular file/stale PASS/directory/symlink/dangling symlink/hard link, or simultaneous link contenders | Refuse without replacing/unlinking existing bytes; at most one atomic no-overwrite winner |
| B03 | Pre-existing/symlinked stage child, wrong owner/permissions/device, source/destination inode or link-identity mismatch, unsupported no-follow/dirfd/FS | Refuse closed; private evidence retained; no fallback publisher |
| B04 | Partial bundle write, entry/digest/sidecar/context/record/invocation corruption; file fsync failure; EXDEV/EEXIST/link error; parent fsync failure | No accepted PASS; no partial destination copy; existing path preserved; report prelink versus postlink failure honestly |
| B05 | Terminate at every stage, including after atomic link/before exit0; unknown exit or nonzero | No accepted current result; a complete postlink file may remain but ROOT acceptance gate refuses it |
| B06 | Successful bundle inode/digests/mode; cleanup of staging alias, cleanup refusal/error, attempted foreign/path cleanup | Published sealed bytes remain bound; never unlink foreign delivery or discard failure evidence; cleanup does not forge acceptance |
| E01 | Later ROOT native replay of unchanged run42405663537/attempt1 after code/OUTPUT/ordinaryCI gates | Genuine full frozen chain plus final metadata check and bound publication/exit0; no new actual mutants |

Harness honesty: use pure typed metadata fixtures and actual fixture HEAD/tree for Git fixtures; never resolve historical74ad/9486 objects in depth1 CI. Invoke original source-identical main/aggregate without replacing protected validators when claiming inside-core coverage. Test-only bounded observation of known function identities/exception provenance is not a production trace hook. Isolated collector/publisher tests may stub the core only when labelled helper tests and may NEVER establish full integration or external PASS. A successful complete immutable frozen integration is reserved for the later ROOT native replay; no mock or fake return0 can replace it.

## Review and execution gates

This revised INPUT adds requirements only; no code, tests, model calls, canonical replay, actual mutants or CI actions run here. Both independent revised INPUT reviews must close Critical to C0. Root then decides code GO. After implementation, meaningful finite controls through the resource gate, both independent OUTPUT reviews on the same exact commit, ordinary CI limited to the fix980 branch and independent ROOT pre-exec approval precede any corrected external. Frozen verify32/runtime9486, CI run/check/artifact records, old approval, observer/dependencies and original receipt schema remain unchanged. No push/merge/release/sign/install/owner operations are authorized by this document. Rollback is refusal to use the supplement; historical proof remains intact.
