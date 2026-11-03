# Incremental dossier acceptance

Status: implementation acceptance plan against `f0a3ce57568efb5e2246fd25e627fa76114e152f`. Shared parser and conservative ambiguity policy are accepted in this base. This document does not complete task 843.

Request: when a new meeting joins a topic, supply the existing dossier and only the new meeting texts; add supported facts and correct explicitly superseded facts while retaining useful links, warnings and author edits.
Minimal path: accept parser 818 first; use its shared projection in planning, append generation, judging and writing; integrate the existing membership plan; validate a candidate before backup and a guarded write; update membership and the index only for the version actually committed.
Before enabling this path, forgetting a meeting must remove its identity from both `состав` and `не_принято`, including supported aliases and list encodings.
Scope: public code and wholly invented fixtures; local model only. No owner data or production migration is used. Validation status is recorded below and in the linked synthetic receipt.

## Existing mechanisms and integration gaps

| Concern | Public source at the pinned base | Acceptance consequence |
| --- | --- | --- |
| Decision | [`dossier.plan`, `composition`, `membership_delta`](../../src/charoite_graph/dossier.py) return `skip/add/rebuild/hold`, canonical new/gone meetings and explicit reasons. | Reuse these rules; decide again against the captured graph and dossier before writing. |
| Night writer | [`_scan`, `_build`](../../scripts/nightly_dossier.py) use fingerprints and full generation; a revision currently blocks `_build`. | `plan` is not wired into this path; an appendable revised body needs the new append path. |
| Deterministic judge | [`check_revision`, `links`](../../scripts/nightly_dossier_review.py) check five headings, a 60% length floor and lost link targets; [`cloud_review.judge`](../../scripts/cloud_review.py) checks write boundaries, author edits and rewrites. | These guards are useful inputs, not proof that facts, warnings or topic relevance survived. Do not invoke their cloud transports. |
| Relevance | No incremental topic-relevance judge or append generator was found in the pinned dossier/night code. | Define and implement a local semantic judge; do not treat `looks_valid`, retained links or a length ratio as relevance. |
| Forget | [`forget_meeting.plan`](../../scripts/forget_meeting.py) removes citations and may delete an automatic dossier or clear its revision; it does not reference the two membership fields. | Header cleanup and its regression cases are a mandatory rollout gate. |
| Parsing | Current `split_dossier`, `preserve_manual` and `strip_protected` have separate heading rules. | Parser 818 and its agreed legacy ambiguity policy are a dependency; no parallel edits to those shared functions. |

## Finite scenario matrix

All names, dates and statements below are invented. Topic `Glasshouse`; meetings A=`2099-01-01_100000`, B=`2099-01-02_100000`, X=`2099-01-03_100000`; document `SensorGuide`. A says `Vent A is open.`; B says `A rain cover was fitted.`; X discusses a recipe and says nothing about Glasshouse. Baseline D has the five standard sections, `состав: [A]`, `не_принято: []`, body links `[[A]]` and `[[SensorGuide]]`, warning `⚠ Sensor is unverified.`, and author payload `Keep purple labels.`. Replace symbolic A/B/X with the full stems when building files.

Conservation oracle O: compare parsed before/after artifacts, not counts alone. Retain every prior supported fact and canonical link target unless a specific new source explicitly supersedes it; retain the exact unresolved warning and author payload. Keep a source-backed ledger for each intentional factual/warning change. `состав` contains canonical meetings actually represented by the accepted body; `не_принято` contains only semantic rejections, with no overlap. Re-read the committed file and index; a failed or skipped write must not advance either field or the index.

| ID | Concrete user action / input | Required outcome and oracle | Required evidence |
| --- | --- | --- | --- |
| S01 | Add B to D, with and without `ревизия: 2099-01-01`. | `add/appendable`; generator receives D plus B text, not A text; add the rain-cover fact and B link; O holds, composition becomes A+B, existing revision is retained. | Captured prompt + actual local generation/judge + files; deterministic input-selection test separately. |
| S02 | Instead let B explicitly say `Vent A is now closed; this supersedes the earlier open state.` | Update the current state to closed with B attribution; preserve the historical open decision with A attribution, unrelated facts, unresolved warning and manual payload. | Actual local model plus a human comparison of the invented before/after and change ledger. |
| S03 | Repeat S01 after success; separately edit A text/mtime or add a non-meeting core/document without a new meeting. | `skip/same`; byte-identical dossier, zero generation/judge calls and no new backup or membership churn. | Deterministic real file operations and call counters; no-model evidence only. |
| S04 | Add X; also submit a fluent five-section candidate that keeps every old link but adds the recipe as a topic fact. | Local relevance judge rejects the unrelated addition; old body/manual/warnings stay intact; X enters only `не_принято`; next identical run skips X. | Actual local judge on a fixed candidate, plus real generator-to-writer run; record these calls separately. |
| S05 | Submit a relevant candidate that silently removes the sensor warning or changes the author payload while retaining length and all links. | Conservation gate rejects the write; no membership/index advancement. A valid format or relevance verdict cannot override O. | Fixed-candidate deterministic checks and actual local judge, each identified. |
| S06 | Generation/judge times out, returns malformed output, cannot decode a verdict, or injects a protected section. | Preserve D and report an operational failure; no semantic-rejection stamp, so B remains retryable; no partial body/header/index write. | Deterministic fault injection, plus a separately recorded real-model refusal if exercised. |
| S07 | D is missing/empty; separately use unreadable D, no `## Сейчас`, no readable composition, or `состав: null`. | Missing/empty -> `rebuild/new`; unreadable/unparsed/no-composition -> explicit `hold` with existing bytes untouched. Legacy `Источники` fallback and explicit `[]` remain distinct. | Deterministic file operations through accepted parser 818. |
| S08 | Add B to a damaged UTF-8 body, a body with speaker labels, or a body exceeding `GROW_LIMIT` after the estimated addition; repeat with a revision. | Respect existing `damaged/labeled/grown` rebuild rules and revision holds; never append replacement characters. `--full` follows existing force/label/revision precedence. | Deterministic plan matrix at both thresholds and guarded writer outcomes. |
| S09 | Remove A from the topic or disk; repeat with a revision. | `gone` names A; rebuild without a revision, hold with one; no append can preserve unsupported A claims. Never rebuild a held body merely to make the run green. | Captured-snapshot and on-disk membership cases, including deletion during generation. |
| S10 | Forget B while B appears in `состав`, `не_принято`, or both; repeat with block lists, canonical aliases and a revised/manual dossier. | Remove B identity from both fields and normal citation handling; preserve unrelated membership/manual text; clear revision when required; next plan reports neither phantom `gone` nor retry of forgotten B. Automatic deletion and retained dossier paths are both covered. | Real forget plan/apply on isolated invented files; read back headers/body/report; regression in `test_forget_meeting.py`. |
| S11 | Rename A through a single redirect, then try a branching stub, a targetless stub and a redirect cycle. | Canonical A is not re-added or falsely removed; unresolved stubs do not cause destructive rebuild; a genuinely deleted canonical meeting is `gone`. | Deterministic `membership_delta` and write-time revalidation cases. |
| S12 | Edit D, B or the graph address while a candidate is generated; make backup fail or deny the final guarded write. | Reject the stale candidate under the existing captured-graph lock/CAS boundary; preserve concurrent edits; no accepted membership/index state without its body and backup. | Deterministic interleavings, failed-copy case and final file/index/backup readback. |
| S13 | Round-trip D twice with inline/fenced/quoted protected heading text, real protected headings, CRLF/BOM and heading variants accepted by parser 818; repeat accept/reject/skip. | One shared parse/projection across planner, judge, writer, review and forget; no duplicated sources/manual sections, body truncation, second-run growth or lost O elements. Ambiguous legacy input follows the approved 818 policy. | Parser regression suite plus real synthetic file operations; no-model evidence. |

## Completion gates and current evidence

Every row is required; grouped variants are separate cases, not one passing example. Before a long run, inspect the complete invented artifacts for S01, S02 and S04. Record the same pinned base, fixture bytes, exact isolated config/root/ports, resource-gate result, command, output, before/after hashes, model version and actual transport for each run. Assert zero cloud/fallback calls. Apply normal review, hooks, CI and mutation gates to the final implementation SHA.

No-model runs may prove selection, planning, parsing, membership, fault handling and conservation against fixed candidate text; they cannot prove generation quality or semantic relevance. Real-model acceptance requires actual local generation and judge calls for S01/S02/S04 and a local judge call for S05; distinguish generation calls, fixed-candidate judge calls, rejected candidates and transport failures. Stubs, cached answers and bypasses are not real-model results.

Implementation evidence: 1,070 selected deterministic regressions passed with three pre-existing expected failures and zero audit events. Actual local generation/judging passed four writer cases (S01 plain/revised, S02 and S04) plus two fixed-candidate judge probes (S04 and S05), using 13 model calls; all four unchanged repeats made zero calls and no new backup. See [the sanitized receipt](../../reports/handoff/tasks/843_acceptance/synthetic-model-receipt.json) for the exact source hashes, model digest and case evidence. Source transport failures are checked separately from semantic rejection. Full-suite CI and mutation acceptance remain required before delivery; no production-graph or installed-runtime claim is made here.

## Implementation decisions for input review

Wire the accepted plan into scanning and writing, including revised appendable bodies. Introduce an isolated incremental helper using only the existing body plus complete new meeting inputs; refuse truncation/overflow rather than stamp unseen meetings. Generation and semantic judging use the existing local transport with cloud fallback disabled. A strict structured local verdict distinguishes irrelevant additions (may update rejection membership) from malformed/transport/conservation failures (retryable, no membership advancement). Deterministic guards retain canonical links, exact unresolved warning lines, and exact author payload; the local judge additionally checks topic relevance and preservation of supported claims and attributed corrections. Protected headings in model output are refused. Revalidate dossier bytes, graph address, membership and source snapshot under the existing lock/CAS before backup/write. Use the same capture for index refresh. Forget removes canonical forgotten identities from both membership fields even for header-only references and block lists. Reuse parser 818; do not reimplement heading parsing.

Every S01–S13 variant remains mandatory; deterministic blackbox runs and actual local model calls are reported separately. No production migration or installation is included without successful checks.

### Input review resolutions

Both independent input reviews have no Critical findings. Accept all four Important findings and the overlapping Minor:
1. Assess each complete new source independently for relevance; a bad candidate never stamps its input rejected. Mixed relevant/irrelevant sources and uncertain verdicts are separate cases.
2. New semantic rejection is tied to full source content, canonical identity, and topic/body context; retry changed input. Legacy unversioned rejection is re-evaluated safely. An unreadable source is operational failure.
3. The committed dossier is authoritative. Index publication is recoverable rather than a two-file transaction: report its failure, and rebuild from disk on every subsequent run including skip. Test failure and recovery.
4. Preserve prior claims in D; old sources are deliberately absent. Require explicit new-source quotation for corrections, while retaining prior attributed history. This is not retrospective validation of D against unavailable A.

Add S14: mixed B+X, bad candidate from relevant B, uncertain relevance, edited rejected X, unreadable rejected source, index write failure/retry, unsupported and ambiguous factual correction.

Forgetting boundary: retained live dossiers and historical cloud snapshots lose
forgotten membership. A rejection-only entry never represented a body claim, so
cleaning it preserves the body and its revision. Removing composed membership
or a real body/source citation uses the existing revision/deletion policy.
New recovery copies in `.forget_backup` deliberately retain original bytes;
this task does not change the existing recovery/retention policy.

## Output review resolutions

Independent Terra and Luna reviews reported no Critical findings. Addressed findings: rebuild selection now admits complete sanitized source blocks without per-meeting clipping; forgetting captures aliases strictly and reports an unreadable snapshot; mixed accepted/rejected updates retain each rejection's actual pre-generation context, so it is reassessed after the body changes. The alleged empty-string composition rebuild was disproved on the reviewed tree: raw empty/whitespace entries already hold. Additional malformed canonical identities remain covered by the conservative header gate. The helper lives in the supported meeting layer, with no graph/runtime import exception.

The original full local run reached 10,582 passes, eight skips and seven expected failures; four legacy index tests still expected the earlier scan/rebuild routing and were then corrected to exercise valid forced rebuilds and strict scan failures. Final CI and the targeted rerun must validate those changes. This record does not turn the initial red run into a clean full run.


## S15: two dossiers in one graph, then one mixed-topic meeting

Use the standalone invented graph control `tests/dossier_graph_control.py`.
Create two topic dossiers in one graph, including one revised dossier, old
citations, an unresolved warning and a byte-sensitive manual payload. Run
without new inputs, then create a meeting containing one independent fact for
each topic and run the real nightly writer with the local model. Both dossiers
must add their own fact, preserve old contents/revision/manual bytes and remain
in the index. Neither may gain a fact solely about the other topic. Repeat:
zero calls, unchanged dossier bytes, no new backup; both backups match originals.

Judge the same fixed candidates with real local calls: each topic's supported
fact is accepted, mixed bullets and compound clauses are rejected, a new
foreign clause appended to an old line is rejected, an explicit source-stated
relationship is accepted and an ambiguous relationship is not accepted. Existing
transport/CAS rejection tests must still prove no membership/body/index advance.
A model verdict is semantic evidence; exact assertion-span coverage is only an
omission guard. No claim of guaranteed semantic correctness follows from one
finite matrix.

Initial S15 observed cross-topic generation. A prompt-only trial corrected the
two real writer outputs but falsely accepted three negative judge probes. It
was rejected. The stronger structured assertion/source-quote protocol must pass
S15 and the original S01/S02/S04/S05 local-model matrix before acceptance. Earlier
13-call evidence is tied to its recorded source hashes and does not validate a
later judge-contract implementation.

Each accepted source must contribute a supported new assertion. The aggregate
judge returns exact candidate spans and source quotations; deterministic
coverage rejects omitted new clauses. Each assertion then receives a separate
local audit with the complete prior dossier, its selected exact quotation and
the complete new source. Conditions, negations and retractions may occur on
different source lines; selecting an affirmative line cannot hide them.
A positive whole-meeting or
aggregate verdict cannot override a failed assertion audit. No old meeting text
is supplied to either judge.

The earlier eleven-case audit control includes old-object updates, an
explicit new relationship, anonymous subject tags, foreign paraphrases and
compound or qualified statements. This control passed independently, but does
not replace the full writer matrix: an earlier integrated audit passed S15 while
falsely refusing valid S01/S02 additions. Its overall result remains a failure.
The corrected full-source instruction and natural UTF-8 payload must pass both
full matrices and the independent qualification/negation control on their final
source. The two initial topic cores are independent;
separate maintenance references satisfy clustering without adding a relationship
between them.

Transport contracts exercise the real gateway with a bounded offline provider,
including full input, local-only construction, finite output, Russian input
capacity and agreement between the admission and provider context windows.
These are deterministic transport checks, not real-model quality evidence.
This path requires Ollama, whose request explicitly carries `num_ctx`. A direct
`mlx-server` configuration is refused before constructing a provider until its
actual server window can be reliably verified; a configured number alone is
insufficient evidence of that window.

Manual S02 inspection found another false-positive receipt: both the old open
state and its new closed replacement remained in `## Сейчас`. The correction
ledger existed, but the aggregate judge accepted the placement. A declared
correction of a current claim now requires its exact prior text in `## Как
пришли`, absent from `## Сейчас`, with the replacement in `## Сейчас`. The driver
checks those section outcomes. The earlier driver PASS remains historical and
does not constitute human acceptance of that S02 output.
