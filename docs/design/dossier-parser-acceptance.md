# Unified dossier parsing: implementation acceptance draft

Historical INPUT draft; implementation evidence is recorded in the coordinating
acceptance reports. Pinned preparation base:
`bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`.
Policy A is selected by coordinating Codex under delegated technical judgment,
not a claim that the owner literally chose A. Unknown legacy protected-heading
ambiguity preserves the entire file and refuses only the affected automatic
write before a model call, with visible reason; unrelated dossiers continue.
The exact grammar and caller admissions are frozen in [the INPUT packet](818-input-brief.md).
Formal independent INPUT review is required before implementation.
The original preparation contained no implementation or migration. The current
code stage has been implemented; real stored-data migration remains unverified.

## User outcome

A rebuild must preserve author edits and current-member source links without
repeatedly moving body text into the author section. The generated body and
source inventory are refreshed by full rebuild; old generated warnings or extra
links absent from the new response/current inventory are not retained by that
operation. Revision-held files remain wholly unchanged before generation.
Stronger retention across changed responses is unresolved follow-up891. All consumers must agree on one
parse result. A text-only compatibility projection must never silently become
permission to replace or delete stored bytes.

## Three synthetic counterexamples

1. A body list item contains `- discussed ##Правки автора`, followed later by
   a genuine final author section. The inline mention stays in the body; two
   rebuilds preserve the same author text without growth.
2. A legacy author section starts with `##Правки автора` and contains a later
   list item `- discussed ## Правки автора`. Both preceding and following
   author lines must remain; preferring the spaced substring is not a fix.
3. A body contains a bare line `## Источники` and meaningful text afterwards.
   Its bytes alone cannot prove whether that is an outer section or a quotation.
   Policy A refuses the affected write and retains all original bytes; the parser must not invent intent.

These examples are constructed strings, not excerpts from stored documents.
The refreshed bcf baseline and its repeat command are recorded in the
[policy-A refresh report](818-policy-a-refresh-report.md).

## Finite acceptance matrix

| Area | Required scenario and evidence |
|---|---|
| Inline mentions | Spaced/unspaced protected words in list items remain text; author extraction does not duplicate body tails |
| Genuine legacy headings | Explicitly enumerate supported levels, spaces/tabs, trailing whitespace and section order; preserve every author line |
| Semantic ambiguity | Apply selected policy A consistently to a bare protected heading; never claim inferred author intent |
| Code and quotations | Fenced code and quoted protected headings do not silently become outer sections; unclear boundaries refuse the affected write |
| Structural completeness | Separate canonical generated, incomplete/manual, reverse-order, repeated-section and unknown-remainder cases; a valid prefix or one source heading is insufficient proof |
| Missing sources | Preserve currently supported ordinary rebuilds of minimal files; do not apply stricter review requirements to every caller |
| Decode damage | Distinguish damaged header/manual text from repairable body paths and a literal valid U+FFFD; preserve existing revision holds |
| Byte provenance | Test LF, CRLF, BOM and unsupported newline classes explicitly; replacement changes only admitted body spans or refuses unsupported input |
| Direct construction | `DossierLoad("ok", text)` uses the same parse rules; absence of raw bytes cannot be disguised as an original disk snapshot |
| Reader consistency | `split_dossier`, author preservation, label detection, composition, kept-from-night, planning and doctor consume the same interpretation |
| Writer consistency | Ordinary rebuild, review, LABELS_ONLY and forget retain distinct admission contracts; a new parser must not expand deletion permission |
| Repetition | Two identical rebuilds retain complete author text, current-member source links and new-response warning blocks without extraction growth; changed-response and revision-hold controls distinguish the operation boundaries |
| Failed write | Existing graph lock, backup and compare-and-swap prevent overwrite on a stale snapshot, backup failure or parse refusal |
| Working synthetic flow | Exercise real public pipeline functions against isolated temporary files/configuration; inspect resulting files before and after with identical inputs |
| Verification and delivery | Independent input/output and final risk review, focused regression tests, applicable CI and critical mutations on the same source SHA; verify installed files separately |

One parse owner belongs in the existing dossier load/result abstraction.
Model-output sanitization remains a separate trust boundary. Reuse existing
locking, backup and safe-write doors; do not introduce a second atomic writer,
a new stored-format registry or a bulk migration as a side effect.

## Boundaries and dependent work

This change is a prerequisite for incremental dossier additions. That later
integration must retain the existing planning work and preserve old text,
source links, author edits and warning blocks while accepting only relevant new
material. It must not be implemented concurrently in the same source files.

Tests use generated public fixtures and an isolated local environment. Stored
owner documents, audio, transcripts and derivative selections are outside this
preparation. Legacy candidate counts are not a migration allowlist. Permission
to develop the parser is not permission to rewrite stored owner data.

## Русское резюме

Цель — единый разбор досье без потери и повторного наращивания авторского текста.
Матрица выше зафиксирована до реализации. Codex выбрал политику A: отказ от
спорной записи с сохранением всего файла до обращения к модели. Самостоятельный
технический выбор не выдаётся за буквальный ответ владельца. Далее — независимый
входной круг, реализация и проверка на полностью синтетическом окружении.
Дописывание досье идёт следующим этапом; работающие данные не мигрируются.

## Historical baseline observation (old 826 base; implementation unchanged)

On 2026-11-02 the three constructed cases were executed against old base
`826001b200fa4f6cd63666df0c285d4553641145` using the
installed resource gates, isolated Python source path and no model calls.
Repeated extraction/rendering grew the inline case from 502 to 602 to 702
characters. The unspaced genuine author section retained both author lines.
The bare source-heading case omitted the meaningful body tail from the body
projection. These observations reproduce existing behavior; they are not a
parser fix or approval of a legacy policy. Results are in
`dossier-parser-baseline.json`.

To repeat, use the repository Python environment with `PYTHONPATH=src` under
the installed quiet/heavy resource gates. Run the following synthetic probe:

```python
import json
from charoite_graph import dossier
body = "## Сейчас\n- discussed ##Правки автора\nbody-tail\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—"
text = dossier.render("Sample", body, [], {}, "synthetic-fingerprint", "2026-11-02")
text = text.replace("## Правки автора\n\n—\n", "## Правки автора\n\nowner-note\n")
lengths = [len(text)]
for _ in range(2):
    manual = dossier.preserve_manual(text)
    text = dossier.render("Sample", body, [], {}, "synthetic-fingerprint", "2026-11-02")
    text = text.replace("## Правки автора\n\n—\n", "## Правки автора\n\n"+manual+"\n")
    lengths.append(len(text))
legacy = "##Правки автора\nfirst-note\n- discussed ## Правки автора\nsecond-note\n"
manual = dossier.preserve_manual(legacy)
ambiguous = "# Dossier\n## Сейчас\nintro\n## Источники\nbody-tail\n## Как пришли\nmore-body\n## Правки автора\nowner-note\n"
parsed = dossier.split_dossier(ambiguous)
result = {"base_sha":"826001b200fa4f6cd63666df0c285d4553641145", "synthetic_only":True, "model_calls":0,
 "inline_growth_lengths": lengths, "inline_growth_reproduced":lengths[0]<lengths[1]<lengths[2],
 "unspaced_manual_kept_both": "first-note" in manual and "second-note" in manual,
 "bare_heading_body_tail_present": "body-tail" in parsed[1], "implementation_changed":False}
assert result["inline_growth_reproduced"] and result["unspaced_manual_kept_both"] and not result["bare_heading_body_tail_present"]
print(json.dumps(result))
```
