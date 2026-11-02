# Task 818: preparation handoff

Date: 2026-11-02. Executor: Codex `task_818`, isolated preparation branch
`fix/818-unified-dossier-parser`. Work started at documentation commit
`efd89490bd218cf58d371341fca02eac308dcf69`; code comparison base is
`826001b200fa4f6cd63666df0c285d4553641145`.

## Result and evidence

- [INPUT draft](818-input-brief.md): 41 identified synthetic oracles refining the
  existing 15 acceptance groups. It is blocked from formal submission until the
  owner explicitly selects the legacy ambiguity policy.
- [Contracts and plan](818-contracts-and-plan.md): exact source references for
  readers, model-output sanitizer, ordinary rebuild, review/LABELS_ONLY, planner,
  doctor and forget; separate read projection and destructive admission.
- [Source manifest](818-source-contract-manifest.json): 18 public source/test
  blobs independently compared to the pinned base with `git rev-parse BASE:path`
  and `git hash-object path`; all equal. This is not a claim of 18 executed tests.
- The existing [baseline](dossier-parser-baseline.json) was read, not repeated.
  No implementation code, test code, stored data, configuration or installation
  changed. No new test/model calls were made.
- UTF-8 decoding, local document links, unique oracle IDs and `git diff --check`
  passed. The standard commit hooks remain mandatory; their actual result is
  reported with the resulting commit to the coordinating session.

The previously uncomfortable scenario is explicit: broad author extraction can
accumulate inline body tails, while a narrower substring preference can discard
genuine author lines. The preparation now gives implementation/review a bounded
set of oracles and identifies the exact compatibility expectations that need an
intentional decision. No runtime improvement or data repair is claimed yet.

## Decisions and limitations

The shortest proposed path adds one parse result to the existing dossier load
abstraction and reuses current locking, backup and safe-write doors. Raw bytes
must remain distinct from normalized `.text`; `safe_write.write_bytes` cannot
replace the existing CAS-gated writer. Full rebuild and body-span replacement
have different preservation promises.

The important compatibility conflict is documented, not silently resolved:
`tests/test_dossier.py:283–295` deliberately treats an inline author-heading
mention as extraction input; ordinary rebuild cases in
`tests/test_dossier_encoding.py:379–399` do not require sources. Forget's
whole-file deletion predicate must not become more permissive.

The proposed grammar is an INPUT candidate, not active product behavior.
The owner policy answer remains pending. Independent INPUT/OUTPUT/risk reviews,
implementation, matrix execution, applicable CI/mutations and installation
verification have not happened in this block. Production migration is outside
this public-code preparation; task 818 as a whole remains unfinished.

## Exact next step

Root records the explicit owner answer, freezes the grammar/admission consequence
and constructs one privacy-checked tracked public packet from the pinned source
base plus these documents. Give identical packet/base separately to Terra and
Luna for INPUT review. Only after the input gate may implementation begin.
Until the answer, preserve this documentation commit and leave implementation
waiting; elapsed time is not consent.

## Русское резюме

Подготовка завершена: 41 проверяемый случай, карта потребителей и минимальный
план сохранены отдельно от кода. Пользователь получает конкретную постановку
для независимой проверки; исправление работающего сценария ещё не выполнено.
Следующая задача одна: №818 — активировать INPUT после явного ответа по policy.
