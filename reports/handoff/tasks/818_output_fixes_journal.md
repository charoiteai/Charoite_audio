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

## OUTPUT2 Important I1: exact normalized author payload

- 2026-11-03 08:37 MSK: resumed from
  `e7d20aba5d64a956d6d307a4e5ad45680bb882af`. Read both independent OUTPUT2
  reports: Terra C0/I1/M0; Luna C0/I0/M0. The coordinating root accepted I1
  and separately reproduced the helper's whitespace/empty collapse. The root's
  new BEFORE JSON is outside this executor's ownership and is not staged here.
- Added BEFORE actual normal/full rebuild regressions over LF/CRLF inputs:
  spaced marker, empty payload, nonempty text with outer blank lines, whitespace
  only, and bare marker without final newline. Both rebuilds are requested by
  changing the same synthetic source's mtime, so normal-mode checks cannot pass
  by skipping the second build. Missing/empty/plain old-file controls and the
  existing malformed-producer retry test are included.
- BEFORE focused: **24 failed / 22 passed / 235 deselected, 1.07 s**. Exactly
  20 real rebuild cases lost normalized author content; four renderer API tests
  failed because the new keyword did not yet exist. Six old-file controls and
  16 malformed-producer cases already passed. This distinguishes behavioral
  evidence from the API-extension checks.
- Minimal fix: `_manual_for_rebuild` returns the exact normalized terminal
  author span. `None` means no section; `""` means a present empty payload.
  `render(..., *, author_payload=None)` embeds an explicit payload after its
  own author heading, before the existing producer classifier. No extra
  section parser, suffix search, projection stripping or truthiness admission.
  Omitted/None payload keeps the previous default marker bytes.
- AFTER focused: **46 passed / 235 deselected, 1.10 s**. All 20 preservation
  cases perform two real rebuilds with two deterministic generation calls,
  retain exact normalized author payload, and verify the actual backup bytes
  at each step. Missing/empty/plain controls still get the default marker.
  Malformed bodies still use exactly the established two temperatures, retain
  all original file bytes, and create no backup.
- AFTER finite 13-module suite listed above: **994 passed / 3 xfailed,
  15.20 s**, exit 0. No legacy delete veto, explicit forget revision behavior,
  general reader projection, backup policy or resource gate changed.
- Additional public-only backward-compatibility experiment: extracted only
  the old `render` function AST from exact e7d20a source and executed it with
  the same unchanged helpers. Compared both omitted and explicit-None new
  calls over 24 cases (three body forms including sanitizer input, two themes,
  gen absent/present, sources absent/present): **24 byte-equal cases**, exit 0.
- Limits: full rebuild regenerates the outer envelope and normalizes supported
  newlines; this test promises exact normalized author content, not outer raw
  bytes/BOM/CRLF. Same-second backup collision remains the pre-existing separate
  debt. No real model, owner data, CI, mutations or delivery was used here.
  Addressed acceptance and final risk gates remain with the coordinating root.

The BEFORE and AFTER focused commands used a fresh installed guard/heavy wrapper
as above, with this exact pytest target and selector:

```sh
task_resource_root=$(mktemp -d /tmp/charoite-818-author.XXXXXX)
~/.config/charoite/quiet_guard.sh && HEAVY_WAIT_MAX_S=5 CHAROITE_ROOT="$task_resource_root" ~/.config/charoite/heavy_run.sh ../charoite/.venv/bin/python -m pytest tests/test_dossier_parser_contract.py -k 'retains_exact_author or without_author_section_keeps_default or render_embeds_exact_author or P03_actual_producer_closure' -q --tb=short
```

The finite AFTER command used the same fresh wrapper with all 13 modules already
listed above, without a selector. The compatibility experiment used the same
fresh wrapper and installed Python with stdin containing:

```python
import ast
import subprocess
import sys
sys.path.insert(0, "src")
from charoite_graph import dossier
source = subprocess.check_output(["git", "show", "e7d20aba5d64a956d6d307a4e5ad45680bb882af:src/charoite_graph/dossier.py"], text=True)
node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "render")
namespace = dict(vars(dossier))
exec(compile(ast.Module(body=[node], type_ignores=[]), "legacy_render_818", "exec"), namespace)
legacy = namespace["render"]
body = "## Сейчас\nplain\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—"
count = 0
for text in ("", body, body + "\n## Источники\nuntrusted"):
    for theme in ("Sample", "A: B"):
        for gen in ("", "synthetic-gen"):
            for members, files in (([], {}), (["Generic"], {"Generic": {"kind": "Ядра"}})):
                args = (theme, text, members, files, "synthetic-fingerprint", "2026-11-03", gen)
                expected = legacy(*args).encode()
                assert dossier.render(*args).encode() == expected
                assert dossier.render(*args, author_payload=None).encode() == expected
                count += 1
print(f"default renderer backward comparison: {count} byte-equal cases; real model calls: 0")
```
