# Task 818: public consumer contracts and minimal implementation plan

Source base: `826001b200fa4f6cd63666df0c285d4553641145`.
Status: preparation only; policy pending. References below are base line numbers.
This is a static inspection, not a test run. Consumer functions, not every use of
the word "dossier", define the parser scope.

## Existing contracts

| Consumer / source | Read contract | Write / admission boundary |
|---|---|---|
| `dossier.render`, `strip_protected` (`src/charoite_graph/dossier.py:555,647`) | Model output is untrusted; protected headings are removed before the pipeline appends its sections | Keep this separate sanitizer. Do not reinterpret model output as a stored dossier |
| `preserve_manual`, `split_dossier` (`dossier.py:627,656`) | Current independent regexes disagree: KEEP_RE is unanchored, split uses first anchored source/manual headings and first body prefix | Compatibility text/tuple API is read projection only; it must not silently admit writes |
| `DossierLoad`, `load_dossier` (`dossier.py:720,725`) | One byte read, strict full UTF-8 decode with separate damage state, then CRLF/CR normalization; direct construction currently supplies state/text | Correct owner for one parse result and raw provenance; normalized `.text` is not captured original bytes |
| `_manual_for_rebuild`, `_build` (`scripts/nightly_dossier.py:119,398`) | Ordinary rebuild admits intact UTF-8 without sources or even body heading; damaged input requires findable intact manual section | Pre-generation refusal; revision hold. Fresh read/fingerprint, graph mutation snapshot, backup, existing `safe_write.write_text(expect=...)` at 483–527 |
| `_DossierReads`, `_scan` (`nightly_dossier.py:86,309`) | Cache one load per path; classify revision/labels/fingerprint before queueing | Reload changed snapshot; preserve reporting/index counters; do not activate incremental additions here |
| `review`, `_review_loop` (`scripts/nightly_dossier_review.py:194,446`) | Review requires sources; author text excluded from cloud input; provider/privacy and current-source checks remain | Current normal review reconstructs sections at 519–530; replace with an admitted span edit. Keep `cloud_edit_graph`, closed-header revision, lock, source recheck, backup and CAS at 490–570 |
| LABELS_ONLY (`nightly_dossier_review.py:511,529`) | Same parsed body and protected content as review | No newly stamped revision date; retain existing revision metadata and separate outcome |
| `labeled_body` (`dossier.py:687`), doctor (`scripts/graph_doctor.py:235`) | Label detection uses body interpretation, excludes source/manual text | Doctor is observation; an unknown body must not be presented as evidence of absence |
| `composition`, `plan` (`dossier.py:816,873`) | Explicit composition field wins; `[]` differs from unreadable/absent; sources provide legacy fallback; plan uses body length/labels | Preserve plan decisions and revision holds. This task does not connect the later incremental writer |
| `kept_from_night` (`dossier.py:754`), forget (`scripts/forget_meeting.py:1146–1176`) | Empty keep reason currently means fingerprint + sources + manual + no manual edits/revision/body label | This is deletion admission, not just parsing. Ambiguous/incomplete/manual input must never become newly deletable; retain forget's strict decoding, path checks, snapshot/backup/apply machinery |
| `read_fingerprint`, `read_input`, `revision` (`dossier.py:747,549,700`), `_собрано` (`nightly_dossier.py:688`) | Metadata access remains distinct from body section splitting; reuse frontmatter helpers, do not add another YAML parser | Metadata-only facts are insufficient body ownership or overwrite permission |

`graph_search` consumes dossier index/doc text and does not call these section
parsers at the pinned base. Its generic search/document body extraction is not
an extra dossier writer and does not justify refactoring search in this task.

## Compatibility conflicts that must be explicit

1. `tests/test_dossier.py:283–295` deliberately expects author extraction for
   `см. ## Правки автора`, indentation and seven hashes. G07 intentionally changes
   the inline behavior; replace only that expectation with preservation of text
   and refusal/projection appropriate to the selected policy. Do not silently
   delete the whole parameterized characterization test.
2. `tests/test_dossier_encoding.py:379–399` exercises ordinary rebuild with empty,
   missing-body/manual-only and CRLF input. A new universal `sources is not None`
   guard would break supported behavior. Missing sources alone is not ambiguity.
3. `tests/test_dossier_encoding.py:109–145` admits repair of body/fingerprint
   damage but refuses damaged author payload/heading before generation. Preserve
   this distinction and literal valid U+FFFD (`:380–397`).
4. `tests/test_dossier.py:801–812` supports reversed source/manual order and
   missing sections as read projections. A stricter destructive-write contract
   must not erase these read cases or equate projection with permission.
5. `VALID_RE` (`dossier.py:445`) accepts a prefix rather than a complete heading;
   full parse acceptance and legacy read compatibility need separate outcomes.

## Minimal ownership and data shape (proposal for INPUT review)

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

Use line scanning with explicit spaces/tabs and fence/quote state. The proposed
exact grammar is recorded in the INPUT draft and must be frozen with its policy
before review; unknown protected headings cannot be resolved by choosing
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

## Ordered implementation and evidence plan

1. Root records the explicit policy, resolves grammar/admission alternatives,
   freezes the public packet and runs independent INPUT Terra/Luna on one SHA.
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

No implementation/test/model calls or production migration were performed by
this preparation. Task 818 as a whole remains unfinished. Its original stored
data migration is outside this public-code work and is not authorized here.

## Русское резюме

Минимальный путь — один результат разбора в существующем загрузчике и разные
допуски для пересборки, ревизии и удаления. Сырой снимок нужен отдельно от
нормализованного текста. Установлены точные старые тесты, где совместимость
сохраняется или меняется адресно; решение о неоднозначном legacy ещё ожидается.
