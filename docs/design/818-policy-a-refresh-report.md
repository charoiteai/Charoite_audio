# Task 818: policy-A INPUT refresh on current public main

Date: 2026-11-03. Executor: Codex `prepare_818_policy_a_input`.
Scope: bounded public documentation, source manifest and synthetic baseline;
no parser implementation. Task 818 as a whole is **unfinished**.

## Result for the user

The original failure is still reproducible on the fetched public main: an
inline author-heading mention is captured into author text and grows through
two repetitions (502 -> 602 -> 702 characters). An unknown bare source heading
also removes meaningful text from the body projection. This block does not fix
those runtime paths. It removes an unnecessary owner-decision wait and gives
implementation/review exact preservation and refusal outcomes before code.

Coordinating Codex selected conservative A under delegated technical judgment:
unknown legacy protected-heading ambiguity retains the entire original file
byte for byte, refuses only its destructive automatic action before generation,
reports why and continues unrelated dossiers. This is not a statement that the
owner literally chose option A. Missing/empty sources alone are no blanket
refusal; read projections do not grant write/delete permission. Full rebuild
and body splice promises differ. Existing forget eligibility never expands.

All 41 oracles and the 15 acceptance groups now have explicit current outcomes.
The deliberate compatibility tightening is visible: unknown author-only input
is held even though the old characterization admitted it. A literal valid
U+FFFD is still ordinary author text; ambiguity is a separate refusal reason.
The current source's damaged-revision hold also remains mandatory under full
rebuild, with no generation or backup for that held theme.

## Source and provenance

- Exact already-fetched public `origin/main` comparison base:
  `bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`.
- Base tree: `20a04d09b803db3efce9d193e5c8ec065562e9ba`.
- New branch: `docs/818-policy-a-input-bcf`, created directly from that base.
- Imported only public documentation commits `5309257df74bb568bcf07a51159a1523104afc6f`,
  `efd89490bd218cf58d371341fca02eac308dcf69`, `f12d0d4f44d41f11be06c1252c815c83ae0ca3c4`
  as `c490fa0c`, `9b8a9039`, `66b80fc6` respectively.
- Original `fix/818-unified-dossier-parser` branch remains at full f12d0d4f SHA
  above. Historical 2026-11-02 report identity and old baseline are preserved.
- 18 public source/test blobs independently matched `git rev-parse BASE:path`
  against `git hash-object path`; all equal to bcf. This is not 18 test runs.
- Relevant 826 -> bcf delta: PR900, lineage
  `fdaae018c3db9dd713bd9f60a21c79a1081490d4`: 6 changed source lines
  (4 added / 2 removed) and 19 added existing test lines. `revision()` now
  holds any closed revision scalar containing U+FFFD as well as a valid date.
  Existing two-mode test is inspected, not newly executed in this block.

## Evidence actually obtained

Before the optional probe, all three constructed strings were inspected:
inline mention plus real final author section, genuine unspaced author heading
plus later inline mention, and bare source heading plus meaningful body tail.
They are invented examples, not stored-document excerpts.

A fresh installed quiet_guard followed by the default shared-lock heavy_run
completed the current-base probe with exit 0. A second guarded run of the
retained repeat fixture, now checking all 18 source blob pins before import,
also exited 0 with identical output. `HEAVY_WAIT_MAX_S=30` bounded
waiting without changing the shared lock or resource signals. Output is
[dossier-parser-baseline-bcf.json](dossier-parser-baseline-bcf.json):

- Inline extraction/render lengths: 502, 602, 702 (old bug reproduced).
- Both unspaced-author lines retained: true.
- Bare-source-heading tail present in body projection: false (old loss reproduced).
- Damaged revision scalar still held: true.
- Valid literal U+FFFD preserved by author extraction: true.
- Actual model calls: zero. Actual rebuild/review/forget pipeline writes: zero.

Repeat from this branch's repository root using its Python environment and the
installed guards (no guard/path/data substitutions):

```sh
~/.config/charoite/quiet_guard.sh && HEAVY_WAIT_MAX_S=30 ~/.config/charoite/heavy_run.sh \
  ../charoite/.venv/bin/python -I -B reports/handoff/tasks/818_baseline_probe.py
```

This probe is source-level baseline evidence. It is not the future 41-oracle
matrix, a working pipeline scenario, review verdict, CI, mutation result,
installed-product verification or a real model experiment. The historical 826
baseline is not relabeled as bcf evidence; the current result is a separate file.

## Validation and exact next step

Preparation checks cover strict UTF-8, local document links, exactly 41 unique
oracle IDs, 15 acceptance groups, 18 pinned unchanged source/test blobs, no
src/tests diff and whitespace. Normal Charoite AI commit with mandatory hooks
and full-SHA readback remains the final local step; its result is handed to root.

Root privacy-exports a single tracked public snapshot from this branch and the
exact bcf comparison base, freezes packet/SHA and sends identical copies
independently to Terra/Luna for INPUT. No code before that input gate. Only
then implement and execute the complete synthetic user-scenario matrix through
the existing write/backup/CAS doors, applicable tests/CI/mutations, OUTPUT and
risk review, and authorized delivery. No real stored-data migration is admitted.

No owner configuration, graph, transcripts, meeting derivatives, bank code,
installation or production files were read or changed by this preparation.
No new network or model call was made. Formal review heads are root's next step.

## Русское резюме

Подготовлен конкретный пакет: политика A, точные допуски и 41 ожидаемый исход на
свежей базе. Старый дефект подтверждён той же синтетикой, защита повреждённой
ревизии сохранена в требованиях. Исправление кода, полный сценарий и поставка
ещё не выполнены; следующий шаг — независимый входной круг у главной сессии.
