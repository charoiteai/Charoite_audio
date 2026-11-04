# Task 818: mutation survivor resolution

Status: **local bounded verification complete; canonical exact-SHA CI and final independent
review remain open**. The original CI run is FAIL, never a clean gate.
Executor: bounded follow-up from source `e7849c6321192cb677605be8a2ce34857e97d72f`.
Comparison baseline: `bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`.

## Source and evidence scope

Root supplied byte-exact public GitHub artifacts for merge SHA
`9a6441fafae97a62cc18107f5f03215f27fe23bf` and independently verified its whole
tree equal to e784. Executor read all four exact machine reports and their
source nodes. The merged old-source verdict is 219/219 judged, 211/211 critical
judged, 33 critical + 2 noncritical survivors, skipped/unread zero. No artifact
in `818_mutation_ci_e784` is edited by the executor.

Existing `mutate_check.scan` and `applied` applied each exact reported key to
source in a tracked public clone of e784. Each new-module baseline passed;
normal pytest/conftest and fresh quiet/shared heavy gates were used for every
mutant. This is targeted local evidence, not the canonical all-selected-tests
judge. Reports retain original local survivors rather than relabeling them.

First frozen 37-test snapshot judged 24 keys: 21 killed / 3 survived. The second
46-test snapshot judged shard4 plus the flow-YAML case: 12 judged, 9 killed /
3 survived. After merging the repeated scalar key: **35 unique keys, 30 killed
(28 critical + 2 noncritical), 5 old-source survivors addressed by approved source consolidation or ordered-boundary cleanup**.

Repeat command for the ordinary synthetic scenario module:

```sh
task_resource_root=$(mktemp -d /tmp/charoite-818-mutation.XXXXXX)
~/.config/charoite/quiet_guard.sh && HEAVY_WAIT_MAX_S=5 CHAROITE_ROOT="$task_resource_root" ~/.config/charoite/heavy_run.sh env PYTHONDONTWRITEBYTECODE=1 ../charoite/.venv/bin/python -m pytest tests/test_dossier_mutation_contract.py -q --tb=short
```

For each targeted replay, copy the test module into a tracked clone of e784;
find its exact key with existing `scan(..., rel=source_path)` over that module;
call `applied`, require a changed parseable source, replace only that module,
run the command above, then restore the source. The exact result and failing
scenario IDs are in `818_mutation_targeted_e784.json` and
`818_mutation_targeted_e784_shard4.json`. Their source is e784; they cannot be
used as canonical proof for the final candidate SHA.

## Exact-key ledger

All line references below are to e784, not shifted candidate lines. `killed`
means a meaningful assertion failed in the exact local mutant; it does not
close root's canonical CI/review gate.

| Exact mutation key | Original line | Synthetic scenario / source proof | Local status / next step |
|---|---:|---|---|
| `scripts/nightly_dossier_review.py::review::return X → return None::abbe618be9a560a3#0` | 220 | named unread/missing/damaged failure tuple before cloud | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::DossierParse::True → False::2202827bf79899cf#0` | 715 | immutable parse admission/result | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::Lt → LtE::399ecf4108909fd0#0` | 743 | first physical protected line immediately after closed header | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::In → NotIn::6c700b84bf6cdd3c#0` | 786 | body heading inside sources refuses | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::not X → X::8dcd0a280f366746#0` | 801 | missing-fingerprint diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::DossierLoad.refusal::return X → return None::f7d79d668bfda80d#0` | 865 | successful refusal API is empty str, never None | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::DossierLoad.replace_body::Or → And::78148e0dfac37f07#0` | 871 | exact unsupported-newline exception detail | killed; canonical current-SHA gate pending |
| `src/charoite_graph/frontmatter.py::with_scalar_preserving::And → Or::690110e02bb8f4fd#0` | 288 | flow mapping preserves no-space colon; empty scalar keeps legal gap and outside bytes | killed; canonical current-SHA gate pending |
| `src/charoite_graph/frontmatter.py::with_scalar_preserving::1 → 0::54865cce079c38a2#1` | 288 | flow mapping preserves no-space colon; empty scalar keeps legal gap and outside bytes | killed; canonical current-SHA gate pending |
| `scripts/nightly_dossier.py::_build::And → Or::61da76121f35fbd1#0` | 446 | actual two-attempt malformed producer retains old bytes and no backup | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::NotEq → Eq::80e969bec2c39984#0` | 753 | backtick-containing info: tilde valid, indented backtick invalid | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::Gt → GtE::40109b805cceb788#0` | 765 | ordered distinct body/protected starts: equivalent boundary | old mutant survived; approved redundant-boundary cleanup applied; canonical pending |
| `src/charoite_graph/dossier.py::_parse_dossier::Lt → LtE::710e4604c2fda424#0` | 787 | ordered protected interval/body-title guard | old mutant survived; approved redundant-boundary cleanup applied; canonical pending |
| `src/charoite_graph/dossier.py::_parse_dossier::NotEq → Eq::cc21c6367487b43e#0` | 793 | duplicate protected diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::Eq → NotEq::eaa01583d626048d#1` | 803 | body-order versus inside-sources diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::DossierLoad.replace_body::Or → And::7acbd9dcdc93d903#0` | 882 | complete candidate with unsupported newline refuses | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::kept_from_night::return X → return None::33daf27be27c0428#0` | 940 | kept reason returns parser diagnostic string | killed; canonical current-SHA gate pending |
| `src/charoite_graph/frontmatter.py::with_scalar_preserving::Eq → NotEq::0bca859e51e8214e#0` | 288 | flow mapping preserves no-space colon; empty scalar keeps legal gap and outside bytes | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::1 → 0::54865cce079c38a2#1` | 753 | backtick-containing info: tilde valid, indented backtick invalid | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::1 → 0::54865cce079c38a2#7` | 787 | ordered protected interval/body-title guard | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::NotEq → Eq::8d384319f80de005#0` | 795 | missing protected-set diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::NotEq → Eq::ee8b322e98cd217b#0` | 799 | outer-envelope versus inside-sources diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::Lt → LtE::0d6128171d6ae2cf#1` | 803 | body-order versus inside-sources diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/frontmatter.py::with_scalar_preserving::Eq → NotEq::823ca7f445472158#0` | 288 | flow mapping preserves no-space colon; empty scalar keeps legal gap and outside bytes | killed; canonical current-SHA gate pending |
| `scripts/nightly_dossier_review.py::_review_loop::10 → 0::098eb32ba392607f#0` | 505 | shared lazy date scalar for preflight/write; actual review checks written revision | old mutant survived; approved shared date/refusal return applied; candidate mutation killed |
| `src/charoite_graph/dossier.py::DossierSection::True → False::2202827bf79899cf#0` | 707 | immutable section positions | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::And → Or::e82a028460ac0b29#0` | 782 | missing fingerprint has explicit hold | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::NotEq → Eq::60c739c5b47af798#0` | 797 | reverse protected-order diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::_parse_dossier::Add → Sub::75b0dff3537cbad9#1` | 799 | outer-envelope versus inside-sources diagnostic | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::DossierLoad::False → True::b2360c734f08a173#0` | 823 | debug representation omits original raw bytes | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::DossierLoad.refusal::return X → return None::980403c674a650e2#0` | 864 | both old author refusals retained in one tested return; damaged author exact hold | old mutant survived; approved shared date/refusal return applied; candidate mutation killed |
| `src/charoite_graph/dossier.py::DossierLoad.replace_body::Or → And::45051c745207cf50#0` | 883 | specific parse error versus fallback exception | killed; canonical current-SHA gate pending |
| `src/charoite_graph/dossier.py::kept_from_night::return X → return None::51dd4d7116e55c60#0` | 942 | both refusal and complete guards retained in one tested return | old mutant survived; approved shared date/refusal return applied; candidate mutation killed |
| `src/charoite_graph/dossier.py::kept_from_night::LtE → Lt::1d09c473da9d2843#0` | 947 | old one-hash protected spelling stays eligible | killed; canonical current-SHA gate pending |
| `src/charoite_graph/frontmatter.py::with_scalar_preserving::Sub → Add::f0830db7418d6ae3#0` | 288 | flow mapping preserves no-space colon; empty scalar keeps legal gap and outside bytes | killed; canonical current-SHA gate pending |

## Accepted ordered-section cleanup: proof, risk and reversal

A physical line produces at most one section with one title. Sections have
strictly increasing, unique starts. Protected titles and body titles are
disjoint. Therefore the body section cannot equal a protected start: `>` versus
`>=` at765 is equivalent. Including the source boundary in the source/manual
interval cannot make the body-title predicate true: the first `<` versus `<=`
at787 is equivalent. The candidate expresses the same intervals by slices of
that same ordered section tuple; no second parser is added. Empty body/protected
sets, reversed/duplicate sections and author-body headings retain the same
short-circuit and projection behavior. Titles before sources are computed once
and reused for the original admission and diagnosis.

The `<` versus `<=` diagnostic mutation at803 is **not** equivalent: that list
includes the source title and changes the displayed reason. Exact reason tests
kill it. An initial source hypothesis was corrected after the executed replay;
it is not included in the two equivalent boundary keys.

Root approved exactly this bounded cleanup before editing. Same-input old/new
comparison passed 78 synthetic LF and CRLF+BOM cases: every parse field, read
projection and all four operation admissions equal. See
`818_mutation_parser_equivalence.json`. Risk: any future parser change allowing
overlapping section starts or multi-title physical lines would invalidate the
proof. Revert/reassess the slice cleanup if that invariant or any read/admission
oracle changes; do not waive a new live mutant. The grammar, whole-file hold,
raw provenance, protected bytes, revision holds and conservative forget veto are
unchanged.

## Accepted shared-value/return consolidations

Root rejected removal of the author-boundary and complete-body protections and
approved exactly these consolidations. They preserve the safety predicates and
error strings, including the boundary-not-found outcome:

- Review-loop date: compute `revision_date = stamp[:10] if may_edit else None`
  once inside the existing try; use it for preflight and the final write, with
  `None` still used for LABELS_ONLY. Read-only mode retains its lazy slice. The
  exact old date-slice key survives in the old discarded preview, but is killed
  in the candidate by actual normal review's written revision date assertions.
- Damaged author: initialize the previous boundary-not-found reason; on a found
  raw line, retain the prior UTF-8 suffix test and choose its previous damaged
  reason or empty success string. A single return reports either refusal. If no
  boundary is found, the initialized original reason is returned unchanged.
  Found/damaged, found/intact and absent-boundary branches therefore have the
  same outcome as before, even outside today's constructor alignment invariant.
  The candidate shared-return mutant is killed by actual damaged-author loads.
- Kept reason: retain both `why` and `not complete` predicates in one conditional,
  and return `why or` the previous incomplete-body fallback. For every combination
  of reason/complete this equals the previous two sequential conditions. A known
  complete body with an unsupported newline still holds; existing one/six-hash
  eligible files still yield exactly an empty string. Both candidate condition
  and shared-return mutants are killed by those separate real API scenarios.

Before/after evidence: the same final 55-case test module passes against e784
production source (55 passed, 0.50 s) and the current candidate (55 passed,
0.50 s). Actual review uses the real graph lock, backup and CAS writer with a
fixed generation double; normal/labels-only and revision-present/absent cases
check the final file and exact backup bytes. The candidate's changed source-line
mutations were all tested: **12/12 killed, zero survivors**. Its source hashes,
exact keys and failed assertions are retained in
`818_mutation_targeted_cleanup.json`; the prior five survivors remain honestly
recorded as survived in the old-source artifacts.

The expanded 14-module regression passed: **1049 passed / 3 xfailed, 17.33 s**,
exit 0. The exact module list/repeat wrappers are in the mutation journal.

A second identical-input old/new comparison covers **336 cases**, including the
78 grammar/projection cases, all 128 standalone invalid UTF-8 body bytes in both
LF and CRLF and two damaged-author cases. All parse fields, projections, four
operation admissions and kept reasons match. The original class/parser/kept AST
is extracted from exact e784, using the unchanged current dependencies. See
`818_mutation_consolidation_equivalence.json`. These are finite synthetic checks,
not a historical-owner-data experiment or a universal parser proof.

Risk/reversal: reassess if raw normalization, physical-line grammar, section
ordering or operation admission changes. Shared refusal consolidation preserves
both old protection outcomes regardless of the alignment invariant. Restore or
correct the bounded refactor if identical-input admission/reason or protected-byte
checks diverge; no new live mutant can be excused by this historical source proof.

No critical zone, limit, threshold, mutation plan, runner, skip, waiver or retry
budget is changed. No new deletion permission or real-data migration is allowed.
Zero real model calls, no owner data, push, merge or installation. Root still
must run the canonical mutation gate and independent exact-SHA reviews.
