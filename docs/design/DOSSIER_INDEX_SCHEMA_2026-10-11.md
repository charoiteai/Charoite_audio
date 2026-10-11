# Dossier index schema audit — 2026-10-11

## Defect and user-visible result

Valid JSON is not necessarily a valid dossier index. On main at
`7d1d58496d09a7a00f2c18fd997dce5cb2f4d5e7`, roots such as `null` or `[]`,
non-object entries and malformed search fields can raise during index lookup.
The graph-search fallback does not catch `AttributeError`, so an invalid derived
index can also prevent a normal graph query from returning source material.

`load_index` now validates the fields used by lookup before CJK normalization:
object root, list of object entries, nonblank string theme, optional list of
nonblank string keys. Any invalid entry rejects the whole derivative index.
Lookup returns no dossiers and graph retrieval remains available. The loader
never rewrites the index or source files.

Missing keys, empty key lists, unknown metadata and legacy CJK normalization
remain compatible. No mandatory version field or new dependency was added.
This is input validation, not a broad exception handler.

## Evidence

- New tests on unmodified main: **31 failed, 1 passed**. This includes four
  integration cases exercising graph retrieval with invalid indexes.
- Dossier and graph-search suites after the fix: **191 passed**.
- Six manual mutations individually disabled validation of the root, entry
  container, entry object, theme, keys container and key values. Every mutation
  made the focused tests fail (7, 3, 6, 5, 4 and 5 failures respectively).
- Combined with PR #792: dossier, graph-search, encoding, runtime-lock and
  workflow suites: **293 passed**. Both malformed-schema and non-UTF-8 refusal
  are retained. These are targeted checks; no new full-suite result is claimed.

## Delivery and review

This follow-up is stacked on `codex/repository-audit-2026-10-11` at
`511387b7abbf66c04aabdc9d1bb2a66581e01257` (PR #792). Applying the original
main-based patch produced two adjacent-line conflicts; the stacked version
retains the UTF-8 exception handling and both sets of regression tests.
Merge #792 first, then retarget this PR to main and rerun CI.

Independent adversarial review required by MAINTENANCE.md remains pending;
this change is submitted as a draft. No generated indexes, source recordings,
privacy rules or protection settings are changed. This audit does not measure
semantic search quality or model accuracy.
