# Task 818: public consumer contracts and minimal implementation plan

Source base: `bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`.
Status: policy A selected by coordinating Codex under delegated technical judgment.
Formal INPUT review is still pending; no implementation. References are current base lines.
This is a static inspection, not a test run. Consumer functions, not every use of
the word "dossier", define the parser scope.

## Existing contracts

| Consumer / source | Read contract | Write / admission boundary |
|---|---|---|
| `dossier.render`, `strip_protected` (`src/charoite_graph/dossier.py:555,647`) | Model output is untrusted; protected headings are removed before the pipeline appends its sections | Keep this separate sanitizer. Do not reinterpret model output as a stored dossier |
| `preserve_manual`, `split_dossier` (`dossier.py:627,656`) | Current independent regexes disagree: KEEP_RE is unanchored, split uses first anchored source/manual headings and first body prefix | Compatibility text/tuple API is read projection only; it must not silently admit writes |
| `DossierLoad`, `load_dossier` (`dossier.py:722,727`) | One byte read, strict full UTF-8 decode with separate damage state, then CRLF/CR normalization; direct construction currently supplies state/text | Correct owner for one parse result and raw provenance; normalized `.text` is not captured original bytes |
| `_manual_for_rebuild`, `_build` (`scripts/nightly_dossier.py:119,398`) | Ordinary rebuild admits intact UTF-8 without sources or even body heading; damaged input requires findable intact manual section | Pre-generation refusal; revision hold. Fresh read/fingerprint, graph mutation snapshot, backup, existing `safe_write.write_text(expect=...)` at 483–527 |
| `_DossierReads`, `_scan` (`nightly_dossier.py:86,309`) | Cache one load per path; classify revision/labels/fingerprint before queueing | Reload changed snapshot; preserve reporting/index counters; do not activate incremental additions here |
| `review`, `_review_loop` (`scripts/nightly_dossier_review.py:194,446`) | Review requires sources; author text excluded from cloud input; provider/privacy and current-source checks remain | Current normal review reconstructs sections at 519–530; replace with an admitted span edit. Keep `cloud_edit_graph`, closed-header revision, lock, source recheck, backup and CAS at 490–570 |
| LABELS_ONLY (`nightly_dossier_review.py:511,529`) | Same parsed body and protected content as review | No newly stamped revision date; retain existing revision metadata and separate outcome |
| `labeled_body` (`dossier.py:687`), doctor (`scripts/graph_doctor.py:235`) | Label detection uses body interpretation, excludes source/manual text | Doctor is observation; an unknown body must not be presented as evidence of absence |
| `composition`, `plan` (`dossier.py:818,875`) | Explicit composition field wins; `[]` differs from unreadable/absent; sources provide legacy fallback; plan uses body length/labels | Preserve plan decisions and revision holds. This task does not connect the later incremental writer |
| `kept_from_night` (`dossier.py:756`), forget (`scripts/forget_meeting.py:1146–1176`) | Empty keep reason currently means fingerprint + sources + manual + no manual edits/revision/body label | This is deletion admission, not just parsing. Ambiguous/incomplete/manual input must never become newly deletable; retain forget's strict decoding, path checks, snapshot/backup/apply machinery |
| `read_fingerprint`, `read_input`, `revision` (`dossier.py:749,549,700`), `_собрано` (`nightly_dossier.py:688`) | Metadata access remains distinct from body section splitting; reuse frontmatter helpers, do not add another YAML parser | Metadata-only facts are insufficient body ownership or overwrite permission |

`graph_search` consumes dossier index/doc text and does not call these section
parsers at the pinned base. Its generic search/document body extraction is not
an extra dossier writer and does not justify refactoring search in this task.

## Compatibility conflicts that must be explicit

1. `tests/test_dossier.py:283–295` deliberately expects author extraction for
   `см. ## Правки автора`, indentation and seven hashes. G07 intentionally changes
   the inline behavior; replace only that expectation with preservation of text
   and refusal/projection appropriate to the selected policy. Do not silently
   delete the whole parameterized characterization test.
2. `tests/test_dossier_encoding.py:398–418` exercises ordinary rebuild with empty,
   missing-body/manual-only and CRLF input. A new universal `sources is not None`
   guard would break supported behavior. Missing sources alone is not ambiguity.
   Policy A intentionally changes author-only unknown legacy: it now refuses the
   whole affected write before generation. Literal U+FFFD alone still is not
   damage; use a complete generated fixture to retain that independent test.
3. `tests/test_dossier_encoding.py:109–145` admits repair of body/fingerprint
   damage but refuses damaged author payload/heading before generation. Preserve
   this distinction and literal valid U+FFFD (`:398–418`).
4. `tests/test_dossier.py:801–812` supports reversed source/manual order and
   missing sections as read projections. A stricter destructive-write contract
   must not erase these read cases or equate projection with permission.
5. `VALID_RE` (`dossier.py:445`) accepts a prefix rather than a complete heading;
   full parse acceptance and legacy read compatibility need separate outcomes.

## Minimal ownership and data shape (frozen INPUT candidate)

Keep implementation in `src/charoite_graph/dossier.py`, attached to the existing
immutable load/result abstraction. One parser produces section spans and parse
diagnostics; wrappers reuse it instead of maintaining their own KEEP/source/
manual regex decisions. Exact field names are an implementation choice.

Required information is: original captured bytes when present; normalized text
for existing readers; decode state; section boundaries; complete/remainder and
ambiguity facts; whether the result has disk provenance. Separate ordinary
rebuild, review/body edit and forget admission from the tuple projection. A
direct `DossierLoad("ok", text)` computes the same structural result, with raw
provenance absent. Do not fabricate original bytes by encoding normalized text.

Use line scanning with explicit spaces/tabs and fence/quote state. The frozen
exact grammar and explicit complete-shape/plain-class admission are recorded in
the INPUT packet; unknown protected headings cannot be resolved by choosing
the first/last or spaced substring. A complete generated shape is a syntax
classification, not proof of an old author's intention. The selected policy
defines which structural classes admit an automatic write.

For a body-only edit, preserve raw prefix/suffix (including BOM and newline
bytes) and splice only the admitted body span. Use the existing gated UTF-8 text
writer when reconstruction can be exact; otherwise refuse the write. Existing
`safe_write.write_bytes` (`safe_write.py:124`) has no `expect` gate and is not a
replacement. No second atomic writer, new format registry, broad Markdown
framework or implicit stored-data migration is needed.

Ordinary full rebuild is a different operation: it regenerates outer text by
existing `render`, keeps the admitted author content, and backs up the complete
old bytes. Do not claim it preserves every old byte outside the body as a
body-only splice would. Decode-damaged body/fingerprint repair remains possible
only when the author preservation contract is established.

## Fresh-base revision hold (PR900)

Compared with the old preparation base 826001b2, the consumer delta is only
`dossier.py:700–706`: revision returns a valid date or a scalar containing
U+FFFD. Existing `tests/test_dossier_encoding.py:228–244` constructs an invalid
UTF-8 date and tests both ordinary and full rebuild. It requires the held
dossier bytes unchanged, no backup for that file, no generation for its theme,
other themes processed, revision/skipped=1, built=2 and model-failures=0. These
are inspected existing assertions, not tests executed by this preparation.
Source lineage: `fdaae018c3db9dd713bd9f60a21c79a1081490d4` (PR900). This
hold stays ahead of parser admission and must survive W06/D04 and forget.

The existing date predicate is shape-only (`YYYY-MM-DD`), not calendar
validation; preserve its conservative hold for `2026-99-99` as well. INPUT Luna's
Minor is a documentation clarification, not permission to narrow protection.

## Accepted INPUT findings before code

Terra Important1: forget's deletion hold does not cover its later edit path.
For every affected ambiguous dossier, including handled snapshots, refuse both
edit/delete before `_strip_meeting` and `without_revision`; preserve raw bytes
and surface the existing manual-follow-up signal. Check plan and actual apply.

Terra Important2: existing looks_valid permits four headings and incomplete or
misordered render results. After untrusted-body sanitization and full rendering,
the producer must validate the candidate with the same shared classifier before
backup/write; an admitted output remains admitted on the next load/rebuild.
Use the current malformed-generation/retry boundary, not an expanded call budget.
Final matrix is43oracles/15groups (P03/E03 added); no new source/data migration.

## Ordered implementation and evidence plan

1. Root freezes this selected policy A and exact grammar/admission packet,
   then runs independent INPUT Terra/Luna on one public SHA.
   Check their concrete scenarios against code before accepting/rejecting them.
2. Add the shared parse result/raw provenance to the existing load abstraction.
   Cover direct construction and retained public APIs. Do not change producer
   sanitization or add a second interpretation to fix a failed test.
3. Route author preservation, split, labels, composition, kept-from-night and
   plan through that result; wire rebuild/review/forget to distinct admission.
   Leave queue scheduling and incremental integration unchanged.
4. Replace review/LABELS_ONLY section reconstruction with admitted body-span
   replacement through existing mutation lock/backup/CAS doors. Compare original
   bytes or equivalent provenance, not normalized text alone, before overwrite.
5. Execute the finite matrix from `818-input-brief.md` under the installed
   quiet/heavy resource gates. Use isolated temporary configuration/data/ports,
   synthetic files and deterministic generation doubles. Record real write-door
   operations separately from actual model calls; models are not required for
   this parsing contract. Never reclassify a test double as model execution.
6. Focus regression checks on `test_dossier.py`, `test_dossier_encoding.py`,
   `test_dossier_plan.py`, `test_dossier_queue.py`,
   `test_nightly_cloud_reports.py`, the affected forget suites and doctor
   behavior. Run applicable CI and configured critical mutations on the same
   source SHA. A busy resource gate defers the run; it does not authorize it.
7. Independent OUTPUT and final risk reviews use the identical clean source SHA
   and pinned comparison base. Criticals require repair and both reviewers on
   the updated snapshot. Verify installed files only after all delivery gates.

No implementation, test-suite run, model call or production migration was
performed by this preparation. A separate current-base synthetic read-only
probe was executed; its limits are recorded in the refresh report. Task 818 as a whole remains unfinished. Its original stored
data migration is outside this public-code work and is not authorized here.

## Русское резюме

Минимальный путь — один результат разбора в существующем загрузчике и разные
допуски для пересборки, ревизии и удаления. Сырой снимок нужен отдельно от
нормализованного текста. Установлены точные старые тесты, где совместимость
сохраняется или меняется адресно. Политика A выбрана Codex как техническое
решение; теперь требуется независимый входной круг, а не новый ответ владельца.
