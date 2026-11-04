# Task 818: mutation gate follow-up

Executor role: bounded tests and accepted parser cleanup. Coordinating root owns
the tracker, canonical CI, independent review and delivery. This is unfinished
818 acceptance work, not a new feature or permission to migrate stored data.

- 2026-11-03 09:54–09:57 MSK: read the public input contract, acceptance matrix,
  prior output-fix journal and resolution, resource rules and exact CI survivor
  artifacts supplied by root. Initial source/worktree HEAD is
  `e7849c6321192cb677605be8a2ce34857e97d72f`. Root reports that CI merge
  `9a6441fafae97a62cc18107f5f03215f27fe23bf` has the identical whole tree;
  that equality is root's evidence, not an executor-owned remote fetch.
- Grouped meaningful synthetic cases: fence/frontmatter physical boundaries and
  exact diagnostics; body-splice rejection and exception detail; exact YAML
  scalar replacement; review's unreadable/missing API; actual malformed nightly
  producer. Every source/test selection was read, including `tests_for`'s import
  and explicit `_load` matching. The new module is selected for all four modules:
  dossier 23 selected test modules; frontmatter 10; nightly dossier 11; review 7.
- First ordinary run: 26 passed / 4 failed. The four failures were incorrect
  new expectations for empty YAML scalars followed by spaces. The helper inserts
  at YAML's empty-scalar mark immediately after the colon; original trailing
  whitespace remains after the new scalar. Corrected the expected exact bytes,
  preserving existing behavior; no frontmatter production fix was needed.
- Next ordinary run: 36 passed, 0.26 s. Added the immutable parse-result API case;
  the frozen targeted source/test snapshot then passed 37 tests, 0.24 s.
- Exact targeted replay of the first three CI reports: used existing
  `scan`, `applied` and `copy_tree` functions against exact e784 in a public
  tracked clone; each pytest used the normal conftest, a fresh quiet guard and
  shared installed heavy lock with a new temporary data root. New test module
  only: **24 judged, 21 killed, 3 survived**. One killed mutant is noncritical;
  20 are critical. Two surviving ordinal-boundary mutations are source-equivalent;
  the third exposed the absent flow-YAML spacing case and remains honestly
  recorded as survived in this first evidence file.
- Root approved the specific bounded parser cleanup before production editing:
  select body-tail and protected intervals by ordered section slices, compute
  titles before the first source once, reuse that value for admission/diagnosis.
  No grammar, policy, raw provenance, writer, deletion veto, runner, threshold,
  budget, retry or resource policy changed.
- Old/new parser comparison: **78 identical-input synthetic cases passed**,
  every parse dataclass field, read projection and all four operation admissions
  equal. Covers LF and CRLF+BOM, no protected/plain, all six protected/body
  orders, missing/duplicate/in-source headings, first post-header protected line,
  valid/invalid/unclosed fences, outer remainder and body headings in author text.
  This is local finite evidence and a source equivalence argument, not a full
  historical-data scan or canonical mutation verdict.
- Initial hypothesis that diagnostic line803 `<`→`<=` was equivalent was
  disproved by exact replay: unlike the body-title predicate, this list includes
  the source title and changes the displayed reason. New exact-reason tests kill
  it. The approved before-sources reuse still preserves the original `<` result.
- Root supplied unchanged shard4 and merged verdict: canonical old-source
  result is **219/219 judged, 211/211 critical judged, 33 critical + 2 noncritical
  survived, no skipped/unread**. The old run is FAIL. Started exact shard4 replay
  plus the first-run scalar survivor with additional flow mapping, representation,
  section immutability and deletion-admission cases. No old report is rewritten.

Runtime command template (each invocation has a new root):

```sh
task_resource_root=$(mktemp -d /tmp/charoite-818-mutation.XXXXXX)
~/.config/charoite/quiet_guard.sh && HEAVY_WAIT_MAX_S=5 CHAROITE_ROOT="$task_resource_root" ~/.config/charoite/heavy_run.sh env PYTHONDONTWRITEBYTECODE=1 ../charoite/.venv/bin/python -m pytest tests/test_dossier_mutation_contract.py -q --tb=short
```

Targeted mutation reproduction uses that same pytest command on a public tracked
clone of e784, with the new test module copied in and exactly one source mutation
from the preserved key applied by existing `mutate_check.scan`/`applied`. Its
baseline must pass first; restore the original module after each mutant. Local
targeted replay is not represented as canonical CI, and surviving equivalents
are not renamed killed. Test doubles perform zero real model calls. No owner
files/graphs/transcripts, live config or secrets are read or modified.

At that checkpoint, remaining work was shard4 resolution and regression. The
continuation below records their completed local checks; coordinating root's
exact-SHA canonical CI and independent reviews remain open. Merge, push and installed delivery are outside this executor's mandate.


## Approved consolidation and follow-up checks

- 2026-11-03 10:11 MSK: root refreshed the shared quota (8% in the 10080-minute
  window, ordinary usage allowed, existing waiver/base unchanged). Runtime
  remained owned by this executor; the other task was documentation-only.
- Exact old-source replay is complete: **35 unique survivors judged; 30 killed
  (28 critical + 2 noncritical), five old-source survivors**. The scalar-spacing
  survivor from the first run is killed by the second run's two flow-mapping
  assertions. The old evidence files retain their original statuses.
- Root approved shared-value/return refactors instead of deleting safety checks:
  date computed lazily once within the existing try and reused for preview/write;
  author-boundary and damaged-suffix refusals retained through one reason return;
  kept reason and complete-body guard retained through one combined return.
  No boundary or complete-body protection is removed. The exact branch proof,
  risks and reversal condition are in the resolution report.
- Current ordinary module: **55 passed, 0.50 s**. Includes actual review normal /
  LABELS_ONLY with and without an existing revision; real lock/backup/CAS, exact
  old-byte backup and final revision. All 128 standalone invalid UTF-8 body bytes
  in LF/CRLF retain the intact author boundary; damaged author files retain the
  exact explicit reason; complete files with unsupported newline cannot delete.
- Current changed-line mutation replay: **12/12 killed, zero survivors**; baseline
  55 passed, 0.48 s. The shared author return is caught by real damaged-author
  loads, kept guards by unknown-newline/eligible-old-heading cases, and the same
  date-slice key now by the actual written normal-review date. New source/test
  hashes and exact keys are saved in the cleanup evidence JSON. This is targeted
  local evidence only; it never substitutes for canonical CI's selected sets.
- Repeated the final same 55 tests on exact e784 production code: **55 passed,
  0.50 s**. Both before and after include the same actual write scenarios and
  fixed deterministic generation doubles; no real model call.
- Extended old/new source comparison: **336 cases passed**. All parse fields,
  projections, four operation admissions and kept reasons agree. Both previous
  author failure outcomes are retained; no forged parser state was used to make
  a mutant appear killed. Original 78-case proof remains a separate unchanged
  historical artifact.
- Expanded 14-module regression: **1049 passed / 3 xfailed, 17.33 s**, exit 0.
  Source/test hashes match the exact files in the cleanup report. The containing
  executor commit identity is recorded by root; this journal cannot embed its
  own containing SHA. Canonical CI and both independent reviews remain root's gate.


The expanded repeat command uses the same fresh wrappers, replacing the test
target with these 14 modules, retaining `-q --tb=short`:

```text
tests/test_dossier_mutation_contract.py tests/test_dossier_parser_contract.py tests/test_dossier.py tests/test_dossier_encoding.py tests/test_dossier_plan.py tests/test_dossier_queue.py tests/test_nightly_cloud_reports.py tests/test_forget_meeting.py tests/test_forget_index_recovery.py tests/test_forget_index_capture.py tests/test_forget_mutation_behaviors.py tests/test_forget_night_processes.py tests/test_graph_collective_nodes.py tests/test_frontmatter_bom.py
```

The source changes are only the accepted ordered-section intervals, shared lazy
review date and shared refusal return paths. Exact command sources and results
are described in the resolution/evidence records. Root-owned old CI artifacts
are kept unchanged and left for the coordinating root to stage; this executor
commits only its own explicit source, test and evidence paths. No push, merge or
installed delivery is attempted. All verification remains synthetic and makes
zero real model calls.
