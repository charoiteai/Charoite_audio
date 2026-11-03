# Task 818 OUTPUT Critical fixes

Scope: the two Critical findings in OUTPUT Terra on source
`b9d37c8219de16d9c4376b963cfdd9b150709145`, compared with
`bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`. Executor works only on public
synthetic fixtures, dossier/frontmatter and the bounded regression suite.

- 2026-11-03 08:26 MSK: read the supplied source contracts, finite 43-oracle
  manifest and both OUTPUT reports. Root's separate admission probe reproduces
  the two Critical cases; it is not an executor-owned file. Explicit forget's
  existing citation/revision edit is retained; a rebuild hold does not annul
  an authorized forget operation.
- Added regressions before the fix: quoted/plain/indented revision scalars with
  comments and an invalid neighboring field; actual normal/full nightly runs
  with an unrelated theme; historical protected spellings including multiline
  whitespace, a header body-start false match, and actual forget plan/apply.
  The deletion regression requires retention of the file, not byte identity
  across explicit forget's already-supported citation edit.
- BEFORE: focused regression command below on unchanged production source:
  **20 failed / 14 passed / 205 deselected, 1.71 s**. Failures showed raw
  comments defeating scalar reads, both nightly modes changing the held bytes,
  false protected matches becoming deletable, and actual forget planning the
  fenced dossier for deletion. The first unrelated-theme fixture reused a core
  stem for a meeting, so it did not prove continuation. Fixed fixture names and
  repeated its nightly-only BEFORE: **2 failed / 237 deselected, 1.43 s**;
  both themes were processed and the held dossier was still overwritten.
- Fix C1: when composing the full header fails, reuse `yaml.compose` and its
  scalar marks on just the located field and indented continuation. Do not
  construct YAML date/bool values; retain the existing raw fallback for a field
  that itself is malformed. The unquoted `тема: A: B` value stays readable.
- Fix C2: record a conservative legacy deletion veto in the same parse result.
  Old protected regex matches outside the admitted protected boundaries, or an
  old body-start match before the real body, veto deletion only. The historical
  600-character fingerprint window and protected header indentation/hash-count
  exclusions remain. Readers still interpret closed fences as ordinary text.
- AFTER focused: **34 passed / 205 deselected, 1.41 s**. Actual normal/full
  nightly runs kept revised bytes unchanged, made zero calls for that theme,
  made no backup for it, and built the unrelated theme. Actual forget plan/apply
  retained the fenced file and its fenced text while removing the explicit
  citation by the established edit route; the other automatic dossier was
  deleted with its normal backup.
- AFTER finite suite: **952 passed / 3 xfailed, 14.00 s**, exit 0. The existing
  PR900 damaged-date normal/full tests are included. All generation is through
  deterministic doubles; **zero real model calls**, no owner data, no delivery,
  no CI or mutation run performed by this executor. Both independent updated
  OUTPUT reviewers and remaining delivery gates belong to the coordinating root.

Every runtime used a fresh ordinary `quiet_guard.sh` followed in the same `&&`
chain by installed `heavy_run.sh`, the default shared lock and an executor-owned
new temporary resource root. No shared signals, locks or owner processes changed.
The repeat command uses neutral equivalent paths to the executed installed tools:

```sh
task_resource_root=$(mktemp -d /tmp/charoite-818-output-fixes.XXXXXX)
~/.config/charoite/quiet_guard.sh && HEAVY_WAIT_MAX_S=5 CHAROITE_ROOT="$task_resource_root" ~/.config/charoite/heavy_run.sh ../charoite/.venv/bin/python -m pytest tests/test_dossier_parser_contract.py -k 'revision_scalar_comment or actual_revision_comment or legacy_protected_matches or legacy_body_start or actual_forget_keeps' -q --tb=short
```

The corrected nightly BEFORE command replaces the `-k` expression with
`actual_revision_comment`. The finite AFTER command replaces the focused target
and selector with the following 13 modules, retaining `-q --tb=short`:

```text
tests/test_dossier_parser_contract.py tests/test_dossier.py tests/test_dossier_encoding.py tests/test_dossier_plan.py tests/test_dossier_queue.py tests/test_nightly_cloud_reports.py tests/test_forget_meeting.py tests/test_forget_index_recovery.py tests/test_forget_index_capture.py tests/test_forget_mutation_behaviors.py tests/test_forget_night_processes.py tests/test_graph_collective_nodes.py tests/test_frontmatter_bom.py
```

The source snapshot tested is the bounded source/test diff to the initial
executor HEAD above. Final commit identity is recorded by the coordinating root;
this journal cannot embed its own containing commit SHA.
