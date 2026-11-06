# Nightly diagnostic 947: bounded input plan

Base: `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962`.
Scope: diagnose the dossier-step failure reason without reading owner graph, source documents, prompts, raw logs or model response content. No product change, installed change, real nightly invocation, model generation or service restart is authorized in this packet.

## Established contract

- `scripts/nightly.sh:305-313` maps dossier exits 2/4 to a generic model-silent label.
- `scripts/nightly_dossier.py:889-892` returns 2/4 when the aggregate refusal count reaches `max(3, attempts // 10)`; the count includes more than empty model output.
- `scripts/nightly_dossier.py:480-615` includes input/adapter refusals, invalid generated format, semantic/audit refusal and backup failure in that aggregate.
- `src/dossier_incremental.py:21-77` adds strict loopback Ollama, declared local-selector version and complete-input context admission before generation.
- The prior delivery attested source and synthetic scenarios; it did not attest the bundle, launchd execution or owner nightly operation.

## Diagnostic sequence and acceptance matrix

1. Compare source SHA and exact relevant bytes with the installed launchd script, interpreter and wrapper. Project only allowlisted nonsecret launchd values and enum/boolean/numeric config fields. Do not infer an effective configuration before proving the wrapper root/env chain.
2. Distinguish current facts from incident-time facts. A present version/configuration does not prove its incident-time value.
3. Reuse existing isolated transport and queue controls for pre-request refusal, context overflow, invalid metadata, successful supported transport, empty/invalid output, transport timeout and the aggregate exit contract. Run only after fresh resource gates and coordination; all requests are doubles, real generation count must stay zero.
4. Compare the exact pre-change and post-change admission paths on the same invented short and oversized inputs where feasible. Keep model adapters deterministic; classify a simulated path as simulation.
5. If safe historical fields cannot distinguish timeout/empty/validation/preflight, report that information loss and the precise missing evidence. Do not relabel the incident as a proven regression.

## Exact objects and projection policy

Only the source-defined persistent nightly status, the two known product launchd labels, their explicit shell entrypoint and directly sourced helper, the selected data-root user configuration and the selected loopback version endpoint are permitted. Wrapper/helper raw contents, filesystem paths and opaque hashes remain private. Do not follow arbitrary config references or read environment/key/credential/prompt files. Compare the four tracked nightly/dossier/adapter files by local hashes and report equality only.

- Status: validated `state`; validated start/end/mtime timestamps; numeric `rc` and `slept_s`; compare `failed` tokens only to fixed source literals, emitting fixed reason codes and an unknown-token count.
- Plist/runtime: enabled/loaded booleans, known shell kind, argument count, source-target equality, calendar Hour/Minute integers, numeric runs/exit code; environment only `CHAROITE_SLOT`, `CHAROITE_ONE_MODEL`, `PYTHONUNBUFFERED`, `NIGHTLY_MAX_H`, `DOSSIER_BUDGET`, `DOSSIER_FULL_BUDGET`, `DOSSIER_SUNDAY_BUDGET`, `NIGHTLY_DOSSIER_FULL`, `NIGHTLY_WAIT`, `MEMORY_INDEX_BUDGET`. Root paths are compared only as booleans. Unknown environment entries yield a count.
- Configuration: normalized engine enum `ollama`/`mlx`/`other`, positive integer `num_ctx`, loopback-endpoint boolean. No model/role/prompt/URL value is emitted. Parse as data without importing runtime code or following references.
- Metadata: one GET of the already approved loopback `/api/version`, no redirects, bounded response; emit only stable normalized version and contract boolean. This is current metadata.
- Existing call telemetry: only the already authorized incident-day file, bounded no-symlink read; exact validated incident window plus exact `nightly_dossier` process literal; output aggregate counts, numeric duration/token totals, zero-output-token counters, validated first/last timestamps and fixed outcome enums. Never emit model, consumer, engine/method strings or raw rows. Missing records cannot prove preflight refusal because logging can be disabled or fail.

Keep runtime projections and their provenance outside the public packet. Public artifacts contain source analysis and invented controls only.

## Exact synthetic comparison and expected matrix

Before: `f0a3ce57568efb5e2246fd25e627fa76114e152f`; after: `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962`. Both are clean tracked worktrees. Use the same control bytes, invented inputs, adapter double and interpreter in separate fresh processes. Record both prompt/input hashes, byte sizes and adapter-entry counters; compare dependency source hashes before attributing a delta. A double admission does not establish actual server support or publication success.

- `tests/test_dossier_ollama_contract.py`: supported metadata admits the intercepted POST; old/unknown/timeout metadata refuses before that POST; invalid selectors never issue HTTP.
- `tests/test_dossier_transport_contract.py`: engine/transport mismatch and complete-input overflow refuse before constructor/stream; valid finite profiles pass the deterministic stage pipeline; malformed/truncated/timeout stage results refuse. No actual socket/model request.
- `tests/test_dossier_queue.py`: actual queue/aggregate logic with invented graph files and deterministic generation; budget is separate from refusal; `test_silent_model_and_failed_scan_exit_4` explicitly combines scan failure with three failed topic attempts and asserts exit4.
- `causal-control.py`: same short/long literal inputs at8192 and long input at32768, both SHAs; fake adapter only, socket connect prohibited. Expected: short inputs admitted on both; long8192 admitted only by the older generation boundary, candidate refuses before stream; long32768 admitted on both. The prompt builder also changed from per-source2500-character truncation to complete sources under a total28000-character cap; this is intentionally part of the exact source delta. Record prompt hashes/completeness rather than assuming prompt bytes remain identical. This compares the combined build/admission behavior, not the context guard in isolation, output quality or the incident cause.

## Minimum future correction, only after separate implementation input review

Persist bounded machine-readable phase/reason counters separately from owner text, including attempted request count, request-started count, preflight, context, metadata, timeout, empty, format, audit and IO failures. Keep existing fail-closed transport/context guards, retries and publication preservation. Counting units must stay distinct: scanned topics, legacy build attempts, generation/metadata HTTP starts and final topic refusals. Multiple relevance/generation/audit calls and retries belong to one topic attempt; do not add request counts to final refusals. Preserve the exact current threshold `final_refusals >= max(3, legacy_attempts // 10)` and combined scan-failure exit4; pre-scan unreadable holds currently can add refusals without adding a legacy attempt. Assign one fixed terminal topic reason (stage that prevents publication), while counting retry/request outcomes separately; include scan/index failures and interruption state. Failure telemetry must never carry prompts, source names, paths, exception strings, endpoints or model output. No request or model switching is authorized by diagnostic observability work.

Review request: identify privacy, causal-inference or diagnostic gaps in this bounded plan; Critical / Important / Minor with file/line and minimal remedy. Do not assume synthetic success establishes owner nightly success.
