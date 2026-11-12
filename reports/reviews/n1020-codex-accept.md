# Refactor verification

Base `2a849865ab834648d975969d6c7a10b9b2611001`; branch `codex/1020-refactor`.
Candidate code patch SHA256: `ee8edf1938983c3b7631b436f79c3f1e1c3631c3bc61e9f450e0c893be5ef61b`.

## Result

- One shared `scratch_files.scratch_dir` replaces the duplicated implementations;
  both existing `_scratch_dir` imports remain callable. Normal-exit cleanup and
  independent directories are preserved. Forced process termination remains outside
  the atexit guarantee, as before.
- Eighteen repeated multi-line root explanations are concise. Every root-resolver
  body is unchanged, including the explicit named-root requirement of the MCP server.
- Graph search iterates the existing immutable generation instead of copying its
  primary tuple and all-document values into lists on every query. Ranking is unchanged.
- Public compatibility wrappers and reserved feature flags were retained.
- Production source is 125 lines shorter, mostly duplicated commentary. Including
  strengthened tests, the source/test change is 122 lines shorter.

## Evidence

Baseline targeted suite:170 PASS/2.69s. Final candidate:171 PASS/2.52s.
Eight existing pytest temporary-cleanup warnings appeared in both runs.
An intermediate run used a noncanonical explicit basetemp and triggered203
isolation teardown errors after203 passing bodies. That was a harness mistake;
final run uses the same standard pytest temporary-root scheme as the baseline.
The changed cleanup test now covers both entry points, verifies successful process
exit, directory creation and a single valid output path before asserting removal.

Synthetic model-free search,30 warm samples per checkout:

| Documents | Before median ms | After median ms | Before peak Python allocations | After peak | Identical result |
| --- | --- | --- | --- | --- | --- |
| 200 | 2.3241 | 2.3389 | 83773 | 80461 | True |
| 2000 | 22.0029 | 21.7354 | 1208001 | 1175889 | True |

No significant overall speedup is claimed from these short timing samples.
The memory figure is tracemalloc's peak Python allocation during one warmed query,
not total application RSS. Reproduce with the saved search benchmark script and
an explicit baseline/candidate checkout argument; all inputs are generated.

A full-sort-to-heap proposal was measured and rejected: random10,000 candidates
improved3.06→1.47ms, but common1,000-item/tied/ordered inputs regressed. The final
code retains the original sort and no speculative heap branch or tests for it.

Independent INPUT and final OUTPUT: gpt-6.1-terra, gpt-6-luna, gpt-6.1-sol;
final source snapshot C0/I0/M0 for all three. Static review is separate from the
root executor's runtime results. Accepted intermediate findings: preserve negative
cap/NaN semantics, strengthen cleanup output validation, measure ordered inputs;
the heap change was ultimately removed. Alias replacement was rejected to avoid
changing introspection and dynamic delegation behavior.

Canonical static-marker and public-format scans:0 findings. The owner explicitly
requested proceeding locally without iCloud settings/people gate. Full publication
privacy clearance is NOT claimed; hooks are not disabled or changed.

Initial quiet_guard failures were incorrectly summarized as model/night occupancy.
The later exact error showed sandbox EPERM on the local Ollama status endpoint.
An escalated read-only guard returned0; all actual tests and benchmarks subsequently
ran through the unchanged heavy_run wrapper. No models or services were modified.

Full candidate pytest:11280 passed,8 skipped,7 xfailed,8 warnings in216.92s.
The targeted cleanup mutant (remove atexit registration) was rejected by both
entry-point tests with the expected leftover-directory assertion; candidate source
was unchanged. CI/publication/installation and range-based mutation matrix are
not yet performed.

## Local delivery state

Ordinary commit attempted with hooks. Ruff and assertion check passed; canonical
privacy hook refused because its people gate cannot access the graph. No hooks
were skipped or modified. Product HEAD therefore remains the exact baseline.
The implementation is staged in the dedicated worktree and saved as a full patch.
No push, PR, merge, release or installed application change was performed.
Range-based mutation/CI acceptance remains pending; the targeted cleanup mutant
and complete local pytest result are real completed checks.

## Publication and architecture registration follow-up

Published candidate `ea47349c505baf6f72febc1b04065464821efa81` passed canonical privacy and ordinary commit/pre-push hooks. CI exposed missing tracked-file architecture registration that the earlier pre-commit local run did not detect. Register `scratch_files` in runtime: its default temporary directory consumes process environment, so base would be incorrect. Classify the exact saved benchmark path as history, update the independent classification contract, and regenerate the architecture map. Runtime code remains unchanged. Full CI and merge remain pending for this correction.
