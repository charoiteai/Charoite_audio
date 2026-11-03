# Task 818 implementation journal

Source: delegated policy-A task and frozen public INPUT packet at
`ca615e9ba1da3edfcf1fb99ceed63b6d2af8f448`; product comparison base
`bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`.
Executor: coordinating Codex's implementation fork, branch
`fix/818-policy-a-parser`. No other agent or model was launched by this fork.
The root owns review verdicts, tracker, CI and delivery. All inputs here are
constructed public fixtures, with zero real generation/model calls.

## Entry and BEFORE (2026-11-03, local time UTC+03)

- 07:52:31 root quota check: weekly 10080-minute window 1%, authorized work;
  global budget file read before work. Fresh root check 08:01:47 also 1%.
- Own worktree created from exact `ca615e9b`; old worktrees/branches retained.
  `git diff --stat bcfdbad0 ca615e9b -- src scripts tests` returned empty.
  Rules, INPUT contracts, 43-oracle matrix, manifest and baseline were read.
- 07:59 real `nightly_dossier.run` twice on one synthetic graph, identical
  generation/source input: lengths **502 -> 683 -> 849**, two deterministic
  generation calls, zero real models. The author's note accumulated body tails.
  This is a different experiment from the saved parser-only **502 -> 602 -> 702**
  baseline; neither sequence replaces the other.
- Same real producer wrote all three malformed bodies: four sections, repeated
  section, unclosed fence (each reported built=1). New contract assertions failed.
- Real forget plan admitted edits of ambiguous revised dossiers in live graph
  and an old-place handled snapshot. The proposed text removed the meeting item
  and revision scalar. Dry-run retained bytes; the pre-apply hold assertion failed.
  A test-helper import error in the first attempt was corrected before this
  forget observation; that failed import was not a forget experiment.
- BEFORE commands used installed `quiet_guard.sh && HEAVY_WAIT_MAX_S=5
  CHAROITE_ROOT=<own-resource-root> heavy_run.sh <repository-python> -m pytest
  tests/test_dossier_parser_contract.py -q -s --tb=short`, and the E03 selection
  after correcting the helper. Tests create their own graph/config/data roots.

## Implementation decisions

- One physical-line classifier and section spans live in immutable `DossierLoad`.
  Disk byte provenance remains separate from normalized text; direct construction
  does not manufacture it. Closed frontmatter is excluded from section scanning.
- Known complete render shape and no-protected plain class have separate
  admissions. Unknown protected structures and unclosed fences refuse affected
  writes before generation. Read projections do not grant edit/delete permission.
- Rebuild keeps author indentation/content, existing revision priority, repair
  only outside an intact raw author suffix, existing lock/backup/CAS and retries.
  Sanitization remains the separate untrusted-model-output boundary. The final
  rendered candidate must be complete and admitted by the same classifier within
  the existing two malformed-output attempts, before backup/write.
- Review and LABELS_ONLY splice the body through the existing gated writer,
  retaining BOM/newline/prefix/suffix bytes. Only normal review deliberately
  inserts/replaces the revision scalar; scalar replacement preserves the rest
  of frontmatter and refuses duplicate/unsupported mappings.
- Forget holds both edit and deletion for ambiguous affected dossiers before
  `_strip_meeting` or `without_revision`, including handled snapshots. Existing
  non-dossier behavior and automatic-dossier deletion exclusions remain.
- Doctor reports unknown dossier bodies separately; absence of a known body
  label is not reported as clean evidence for an ambiguous document.
- Tests of unrelated write behavior now use complete render fixtures. Legacy
  projections, inline mentions, reversed/missing sections retain explicit tests;
  the intentional inline-extraction behavior change is addressed individually.

## Finite matrix and evidence owners

All product checks below are represented by tests; they are not claimed executed
until the AFTER section records an actual command/result.

| Oracles | Tests / evidence |
|---|---|
| G01, B03, R01 | `test_G01_B03_shared_load_provenance`, existing wrappers/plan/doctor regressions |
| G02, G03, G07 | `test_G02_G03_G07_inline_and_manual_terminal_text`, actual P01 rebuild |
| G04 | `test_G04_protected_grammar` (5 levels x 3 indents x 3 gaps x 3 tails) |
| G05, G06, S01-S04 | `test_G05_G06_S01_S02_S03_S04_ambiguous_is_not_permission`, legacy split regressions |
| G08, G09 | `test_G08_G09_fences_and_quotes`, unclosed producer case |
| G10 | `test_G10_exact_body_grammar` |
| D01, D02 | `test_D01_D02_raw_damage_boundary`, real encoding rebuild regressions |
| D03 | `test_D03_valid_replacement_is_not_decode_damage`, literal-author rebuild regression |
| D04 | `test_D04_revision_hold_is_shape_only`, PR900 damaged-date two-mode regression |
| B01, W03, W04 | `test_B01_W03_W04_exact_splice`, actual review twice incl LABELS_ONLY |
| B02 | `test_B02_unsupported_newlines_refuse` |
| B04 | `test_B04_missing_empty_unreadable`, real missing/empty rebuild regressions |
| R02-R04 | `test_R02_R03_R04_shared_reader_boundaries`, doctor unknown-body assertion |
| W01 | `test_W01_plain_has_distinct_admission`, ordinary encoding rebuild regressions |
| W02 | `test_W02_review_refuses_ambiguity_before_cloud` |
| W05, E02 | existing automatic/manual/revised/labeled forget plan/apply regressions |
| W06 | existing `test_dossier_plan.py`, queue and encoding revision regressions |
| P01 | `test_P01_actual_rebuild_is_stable`; full rebuild has no outer-byte identity promise |
| P02, E01 | `test_E01_P02_actual_review_splices_twice`; deterministic doubles only |
| P03 | `test_P03_actual_producer_closure`, successful P01 canonical reload |
| F01 | dossier/source-change encoding and review regressions |
| F02 | backup-failure, missing snapshot and graph-lock regressions |
| F03 | normalized-equal/raw-different hold and unsupported-scalar tests |
| E03 | `test_E03_actual_forget_preserves_ambiguous` live/old/external snapshot dry-run/apply |
| V01 | root: exact-SHA CI/full/critical mutations, independent OUTPUT/final risk |
| V02 | no installation authorized to this fork; root delivery decision and verification |

## AFTER status / limits

- 08:01 AFTER pytest blocked before execution by installed quiet hook: local
  models occupied 24.4 GB at an 11.5 GB threshold. 08:06 fresh `quiet_guard.sh`
  returned 3 for the same reason. No narrow test, alternate lock, model stop or
  resource-gate bypass was used. Models and owner installation were untouched.
- Static `ruff check` on the explicit changed Python files: passed (08:06).
  This is syntax/lint evidence, not executed user behavior.
- AFTER behavior, focused regressions, CI/full/critical mutations and independent
  OUTPUT/final risk remain unverified at this entry. Root will receive exact
  source SHA. No push, merge, installation or stored-data migration by this fork.
- Original task 818 includes forbidden real stored-data migration: this public
  synthetic change cannot claim completion of that migration or of all task 818.
