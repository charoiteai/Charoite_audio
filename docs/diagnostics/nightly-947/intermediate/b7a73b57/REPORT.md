# Nightly dossier refusal: source and synthetic diagnosis

The source admits several operational failures under one model-silent label. That label cannot identify whether generation started, timed out, returned empty text, failed formatting/auditing or was refused during admission. A synthetic before/after comparison establishes a changed full-source/context boundary; it does not establish the cause of a particular installed nightly incident.

## Proven source behavior

- `scripts/nightly.sh:305-313` maps dossier exits2/4 to the same model-silent reason. A final `failed` status can coexist with process rc0 because optional-step failures are recorded separately.
- `scripts/nightly_dossier.py:340,493,517,549,614` increments the common refusal counter for unreadable inputs, append/semantic exceptions, generation/format failure and backup failure. The name does not imply an empty response.
- `scripts/nightly_dossier.py:731-732,889-892` separates legacy topic attempts from refusals. The exit threshold is `refusals >= max(3, attempts // 10)`; a simultaneous scan failure returns4. Pre-scan unreadable holds can increment refusals without incrementing legacy attempts.
- The source delta at `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962` replaces the former direct generation stream with `dossier_incremental.local_complete`. It requires a loopback Ollama adapter, a verified local selector contract and complete-input context admission before streaming.
- The same delta changes rebuild prompts from per-source2500-character clipping to full source text under a total28000-character cap. Full input plus the conservative UTF-8-byte context guard can therefore refuse work previously admitted to the old generation boundary.

## Same-input synthetic result

Before: `f0a3ce57568efb5e2246fd25e627fa76114e152f`. After: `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962`. The same literal fixtures/configurations and adapter double ran in separate fresh interpreters against clean tracked worktrees. No graph scan/publication or real model generation ran; socket connect is prohibited by the control.

| Invented case | Before | After |
| --- | --- | --- |
| Short source, context8192 | Adapter double entered | Adapter double entered |
| Long source, context8192 | Adapter double entered; source clipped | Context refusal before adapter construction/stream; source complete |
| Same long source, context32768 | Adapter double entered; source clipped | Adapter double entered; source complete |

The short prompt is2336UTF-8 bytes on both snapshots. The long prompt changes6957→17913bytes; input/config hashes are equal across snapshots, while prompt hashes intentionally differ. Privacy/schema/frontmatter/graph-name dependency bytes are equal. All eight binding assertions passed; receipts and hashes are in `causal-before.json`, `causal-after.json` and `causal-binding.json`.

This establishes the combined full-source/admission delta, not a guard-only effect. Admission to a deterministic adapter does not prove actual server acceptance, output quality or successful publication. The32768 fixture is a positive control, not a recommendation to change an installed model/window.

## Addressed controls

The existing three-file suite passed213 tests in5.83s:

- `tests/test_dossier_ollama_contract.py`: supported/unsupported metadata, malformed metadata, metadata timeout, local selectors and intercepted generation transport.
- `tests/test_dossier_transport_contract.py`: pre-constructor engine/context refusals, valid deterministic profiles and rejected/truncated/timeout audit-stage outcomes.
- `tests/test_dossier_queue.py`: queue/time-budget accounting and combined scan failure plus refusal threshold returning4.

The first invocation failed213 teardown checks because its explicit temporary base was outside the platform temporary root. Only the invocation was corrected; the protective fixture was preserved. `first-run-result.json` retains the failed result. The successful bounded run had one configuration warning because plugin autoload was disabled; this is not a full release, mutation-suite or installed-model result.

## Limits and next correction

Installed/runtime observations and provenance remain private. No owner graph, source document, prompt, raw nightly output, response text, arbitrary environment value or runtime wrapper source appears here. Current source/version/configuration facts cannot reconstruct incident-time facts. Missing call-ledger rows alone cannot prove that no request started: telemetry can be disabled or fail, and admission can occur before ledger creation.

The minimal next correction is bounded reason/phase telemetry at the dossier orchestration boundary. Keep topic/legacy-attempt/request/final-refusal units separate, classify preflight/context/metadata/timeout/empty/format/audit/backup/scan/index outcomes without text, preserve the exact exit threshold and publication safeguards, and retain retryable work. Do not relax privacy/context guards, switch models/windows or rerun owner data as part of that correction. A separate implementation input review and addressed negative/positive controls are required.

## Repeat

Use the normal quiet/heavy resource wrappers. With `CHAROITE_PYTHON` pointing to an approved interpreter, run the three test files shown in `synthetic-result.json`. Run `causal-control.py <clean-tree>` once per exact SHA in fresh interpreters; compare input/config/control hashes and the expected cases above. A suitable platform temporary root is required. Do not point the control at a data tree or load owner configuration.
