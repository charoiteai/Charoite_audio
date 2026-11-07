# Release980 supplemental helper and ROOT consumer

The standalone stdlib helper is `scripts/release980_external.py`. It preserves frozen definition74ad, runtime9486, run42405663537/attempt1, plan091c and root83724. Only original `validate_release_pr` is replaced; original main/aggregate/preflight/API/import/artifact/canonical/final-receipt functions execute unchanged. This is supplemental proof, never vanilla74ad external PASS.

## Trust before any candidate Python execution

ROOT must independently verify the authorizing supplemental record hash, clean verifier commit/source hash, managed CPython executable realpath/hash/exact3.12 version, and its approved runtime/startup closure BEFORE executing this candidate script. Use already trusted OS/ROOT tooling to do this; the script's checks cannot establish its own pre-execution trust. Execute verified bytes from a sealed0400 snapshot under a private0700 directory, not an unchecked mutable script path. Start supervisor with `-I -S -B` and a sanitized environment. Preserve HOME/authentication/proxy routing; exclude Python/site/dynamic-loader injection variables. Do not install or change packages.

The authorizing record is created/committed independently by ROOT after exact OUTPUT review; this helper never creates it. It has the r2 fields `v,task,verifier_sha,verifier_sha256,frozen_definition_sha,frozen_gate_sha256,original_root_approval_record_sha256,pr_policy_fixture_manifest_sha256,scope` plus `managed_python` and `managed_tools`. All immutable values are checked literally. `managed_tools` pins git and gh by path, realpath and SHA256. `managed_python` contains path, realpath, SHA256, exact version, `closure` mapping approved absolute directory roots to `tree_hash` digests, explicit `search_paths` selected from those roots, an exact `links` map for used symlinks, explicit `files` hashes for the native framework target, `excluded_links`, pinned `venv_config`, and exact `canonical_search_path`. ROOT must approve the concrete closure, including managed stdlib and every global/venv site startup path; writable/changed closure entries and missing, changed or uncovered link targets are HOLD. The managed stdlib has three symlinks: link text is hashed, used native links must resolve to an explicitly pinned file or real closure. The unused stdlib/site-packages link is hashed but not traversed or trusted: its text/target/reason are pinned in `excluded_links`, only this standard unused registration may be excluded, pinned pyvenv.cfg must say include-system-site-packages=false, and both isolated/restored paths and the exact approved canonical plain-child sys.path must exclude it. ROOT audits/pins the actual managed startup closure before the plain-child path probe; no site/.pth code is executed before that independent trust gate. Unknown/external used targets, loops, changed links and excluded search-path use refuse. No broad external package closure is added. The file-tree digest includes all regular files, modes, bytecode and startup files. No runtime closure record was authorized or generated during this implementation.

Supervisor repeats all pins before candidate exec. Child starts with `-I -S -B`; it restores only explicitly pinned package paths by appending them, without running site/.pth. This preserves frozen importlib.metadata behavior rather than patching it. Original canonical merge child keeps its unchanged command (without -S); it inherits sanitized `PYTHONSAFEPATH`, `PYTHONNOUSERSITE`, no Python path/startup injection, and `PYTHONDONTWRITEBYTECODE`. Its managed stdlib/site/.pth/customization code must be covered by ROOT's same audited closure. A missing/unverifiable closure is HOLD, not a fallback.

## Manual trusted launch and mandatory consumer

After the independent pre-exec gate and OUTPUT/CI approval, ROOT manually invokes the reviewed supervisor; existing delivery automation is untouched:

```sh
"$PY" -I -S -B "$SEALED_HELPER" supervise \
  --verifier-root "$VERIFIER_ROOT" --definition-root "$FROZEN" \
  --source "$RUNTIME" --context "$CONTEXT" --approval-record "$ORIGINAL_APPROVAL" \
  --supplemental-approval-record "$SUPPLEMENTAL_APPROVAL" \
  --supplemental-approval-record-sha256 ROOT_PINNED_LITERAL \
  --delivery-parent "$PRIVATE_PARENT" --delivery-name FRESH_BUNDLE.zip \
  --acceptance-name FRESH_ACCEPTANCE.json --invocation-id ROOT_FRESH_64_HEX
```

The parent must be an explicitly approved absolute, nonsymlinked, owner-only0700 local POSIX directory. Only Linux/macOS descriptor-relative hard-link semantics are supported. The invocation claim is exclusive and remains after failure. The original OUT is always a fresh child of a random private stage, never the delivery path. Raw original PR stdout is read only from the authenticated immediate aggregate frame's one local CompletedProcess; duplicate JSON/type mismatches and an unsupported code/call-site/CPython contract refuse before metadata acceptance.

Independent complete CI workflow run listing by exact head includes runs with no check rows; every attempt1..current and all jobs/suites/check rows must reconcile. All observed checks are judged, including REST-only Copilot; original mandatory PR roles remain unchanged. The sole exact suite114879227597/app1236702 empty automatic registration is separately recorded as `empty_unexecuted_app_registration`, statusqueued/conclusionnull. It is not an executed check, completed CI run, successful role or SKIP. Classification requires the complete filter=all suite list empty, exact head/repo/app identity, zero typed count, no PR/check/Actions association, and an unchanged header across phases. Unknown/populated/changed/Actions-empty suites refuse. The independent primary raw proof is retained alongside all original metadata; nothing is silently dropped.

Only after original core success and full final metadata comparison is one sealed0400 ZIP bundle atomically hard-linked to the fresh requested filename (`EEXIST`, never overwrite/replace/delete). It binds exact original strict/canonical bytes, sidecar, raw metadata manifests/bytes, code/records/context and fresh invocation. Two observations reject observed drift; they do not claim atomic GitHub state. A post-link interruption may leave a complete file, but it remains UNACCEPTED.

Supervisor calls `Popen.wait()` itself. Only actual exit0 of that exact sealed child permits `accept_published`; no CLI accepts a supplied exit code. The consumer reopens no-follow regular0400 bytes, checks inode/link identity and all binding/digest/schema constraints, then atomically creates a fresh protected acceptance record. No consumer/record, stale/raw strict receipt, unconfirmed/crashed/nonzero child, corruption or partial publication means HOLD. ROOT requires supervisor exit0 AND the fresh protected acceptance record bound to the requested bundle/current invocation. No consumer may follow a private raw strict receipt. Private failure evidence/staging are retained; no automatic cleanup or existing-path deletion is implemented.

## Reproduce controls (not a real frozen replay)

Use the existing quiet_guard then heavy_run wrappers, with native tempfile base and the existing managed interpreter. Do not override HOME, owner data, models or proxy/auth settings:

```sh
quiet_guard.sh && HEAVY_WAIT_MAX_S=30 heavy_run.sh "$PY" -I -S -B \
  -m unittest discover -s tests -p test_release980_external.py -v
```

The tests cover metadata before/after, pair/cohort/history/provenance negatives, narrow capture helper stubs, real original main/aggregate to an intentional genuine runtime guard failure, startup isolation, private atomic publication and actual-child-wait consumer helper integration, plus five changed guard mutants. Synthetic publisher/consumer data and frame stubs are labelled helper boundaries, never full frozen success. A successful full frozen34-job integration and actual supplemental external/consumer acceptance remain ROOT native gates after both OUTPUT reviews and ordinary CI on this fix branch. No actual682 rerun, frozen CI write, release, signing or installation occurs here.
