# Task 818: independent INPUT review draft

Status: **NOT SUBMITTED; OWNER POLICY ANSWER REQUIRED**. Preparation only.
The source base is `826001b200fa4f6cd63666df0c285d4553641145`
(tree `78e024db5318afbb7685d45771262d699143d2ad`). Preparation started from
`efd89490bd218cf58d371341fca02eac308dcf69`; the intervening changes are documents.
Do not implement or treat this draft as an approved INPUT round.

## Activation record (must be completed before submission)

- Owner answer: **PENDING**. Time, a selected UI default and a general instruction
  to continue work do not count as the answer.
- Choice A: for an ambiguous protected heading in unknown legacy structure,
  preserve the entire file and refuse the affected automatic write. This may
  narrow old ordinary-rebuild admission; it must be visible in the report.
- Choice B: reserve each supported standalone protected heading as an outer
  section in the selected legacy grammar. This chooses a syntax convention; it
  cannot establish whether an older author intended a quotation.
- Root must record the actual answer and exact grammar/admission consequence,
  replace this pending section, freeze the packet SHA, and give **the same clean
  packet and base** separately to `gpt-6.1-terra` and `gpt-6-luna`.
- No private documents, data-derived fixtures, graph, transcripts, local config,
  untracked files, repository history, or other reviewer's verdict go in the
  packet. Export tracked public files from the pinned base plus these design
  documents; run the established privacy checker before sending.

## Requested result

One section parser in the existing dossier load/result abstraction must make
readers agree while preserving author edits, source links and warning blocks.
Repeated rebuilds must stop accumulating body tails in author edits. Text-only
compatibility projections must never grant permission to replace/delete bytes.
Keep model-output sanitization as a separate boundary: an untrusted model body
must not gain permission to supply sources or author sections.

Read [contracts and implementation plan](818-contracts-and-plan.md), the
[existing acceptance draft](dossier-parser-acceptance.md), and the recorded
[baseline](dossier-parser-baseline.json). The finite case list below refines the
15 acceptance groups. None of its new cases has been executed in this block.
The existing baseline is prior evidence, not a newly repeated experiment.

## Proposed finite acceptance cases

Each ID is an independent oracle. Parameter sets are explicit representatives,
not a claim that syntactically identical bytes reveal author intent. Cases marked
POLICY get their exact outcome from the activation record before INPUT review.

| ID | Constructed input / action | Required oracle |
|---|---|---|
| G01 | Full `render` envelope, five ordered body sections, sources, author section | One complete structural interpretation; all spans cover the input or mark remainder |
| G02 | Inline `- discussed ## Правки автора` and `##Правки автора`, genuine final author section | Inline text stays body; final author lines stay author text |
| G03 | Genuine `##Правки автора`, later inline spaced mention in its text | Keep every preceding/following author line; no substring preference |
| G04 | Protected heading levels 1, 2, 3, 6, 7; leading none/two spaces/tab; gap none/space/tab; trailing none/two spaces/tab | All are structural candidates under the proposed exact grammar below; legacy write admission is POLICY, no unsupported-to-empty conversion |
| G05 | Bare `## Источники` followed by meaningful body-looking text in unknown legacy structure | POLICY; preserve all bytes when refusing, never claim inferred intent |
| G06 | Bare `## Правки автора` in unknown legacy structure | POLICY, same ambiguity treatment as G05 |
| G07 | Heading-like inline `см. ## Правки автора`, no final section | Text, not an outer heading; review the deliberate compatibility change |
| G08 | Fenced backtick and tilde blocks with protected lines, including unclosed fence | Closed fences remain text; uncertain unclosed boundary refuses affected destructive action |
| G09 | `> ## Источники` and `> ## Правки автора` | Quoted lines remain text |
| G10 | Body start `## Сейчас`, `##Сейчас`, indented start, `## Сейчас extra` | First three are exact structural candidates; suffix `extra` is text; no prefix-based completeness |
| S01 | Missing sources, missing author, both missing | Reader projection remains representable; ordinary rebuild/review/forget follow separate contracts |
| S02 | Author before sources | Preserve both sections and all lines, or explicit refusal under selected grammar |
| S03 | Repeated sources, repeated author, unexpected trailing section | Preserve complete file on non-unique boundaries; no first/last-match truncation |
| S04 | No body heading, body prefix only, five sections out of order | Distinguish incomplete/manual structure from canonical; no deletion admission from a prefix |
| D01 | Invalid UTF-8 only in replaceable body or fingerprint; intact genuine author section | Ordinary rebuild may repair current supported path; exact raw backup and author content survive |
| D02 | Invalid UTF-8 in author heading or author payload | Refuse before generation/write; unchanged original bytes, no model-failure classification |
| D03 | Valid UTF-8 with literal U+FFFD in author text | Keep literal text; do not infer decode damage from that code point |
| D04 | Damaged input plus valid revision date | Existing revision hold remains effective |
| B01 | LF and CRLF, with/without BOM | Raw provenance explicit; admitted body-only edit preserves every byte outside body span |
| B02 | Bare CR, mixed LF/CRLF, Unicode line separator | Explicitly support with exact byte mapping or refuse affected write; normalization is not provenance |
| B03 | `DossierLoad("ok", text)` and disk load of same normalized text | Same section interpretation; only disk load has captured original bytes |
| B04 | Missing, empty, unreadable file | Preserve existing distinct load states and missing/empty rebuild path |
| R01 | Same input through split, author preservation, label detection, composition, kept-from-night, plan and doctor | Shared interpretation; caller-specific admission stays separate |
| R02 | Label only in body / sources / author text | Only admitted body label affects body-label predicate; ambiguous parse is not clean body evidence |
| R03 | Valid frontmatter `состав: []`, malformed empty composition, absent composition | Existing composition priority and None/empty distinction survive |
| R04 | Metadata-looking lines in body | Header readers do not take body text as metadata |
| W01 | Ordinary rebuild: empty, no body heading, no sources, intact manual-only input | Do not add a blanket sources requirement; ambiguity can separately narrow admission only under selected policy |
| W02 | Review of missing sources or ambiguous structure | No cloud request/write admission from a read-only projection |
| W03 | Normal review with links, warnings and manual notes | Change admitted body and revision field only; preserve sources/manual; body generated through existing sanitizer |
| W04 | LABELS_ONLY with existing revision/no revision | Remove labels through admitted body edit; never add a revision date merely for label removal |
| W05 | Forget of canonical automatic dossier vs manual/ambiguous/revised/labeled dossier | Never expand current whole-file deletion eligibility; backup and report remain |
| W06 | `plan` force, unchanged, labeled, revised, gone-source, damaged-source cases | Existing hold/skip/add/rebuild semantics survive; parser is no new incremental implementation |
| P01 | G02 and G03 rebuilt twice with identical source/generation input | Complete author text once, no growth from extraction, source links/warnings retained |
| P02 | Review applied twice with identical synthetic fixed body | Stable protected content; no duplicated sections |
| F01 | Stale dossier or source changes during generation/review | No overwrite; existing snapshot/fingerprint checks and user-visible reason |
| F02 | Backup failure and graph lock unavailable | No write; existing report/index behavior retained |
| F03 | Parse refusal just before write; equal normalized text but different raw bytes | No write on refusal; normalized equality is not proof of original snapshot equality |
| E01 | Isolated temporary graph/config with synthetic files, real rebuild and review/labels write doors | Before/after files inspected; generation is a deterministic test double and explicitly not a real model call |
| E02 | Synthetic forget dry-run/apply through its public functions | Inspect plan, copies, final files; no new deletion permission |
| V01 | Focused regression suites, applicable CI, critical mutations, output review/final risk gates | Record exact source SHA and actual commands/results; unrun checks remain incomplete |
| V02 | Delivery to working installation if authorized by root | Verify installed files independently against accepted SHA; development success is not installation evidence |

The proposed protected-heading grammar is a complete physical line matching
`[ \t]*#+[ \t]*(Источники|Правки автора)[ \t]*`, outside fenced/quoted text.
The number of hashes has no upper bound, preserving the existing seven-hash
case. Body heading candidates match `[ \t]*##[ \t]*TITLE[ \t]*`, where TITLE
is exactly one of `Сейчас`, `Как пришли`, `Решено`, `Открыто`, `Кто в теме`.
These are syntax candidates, not permissions. Canonical generated classification
requires the whole render envelope, all five ordered body sections and unique
sources/author sections in generated order; a matching prefix is insufficient.
Other structure remains explicitly legacy/incomplete/ambiguous. Readers can
expose a non-destructive projection; destructive admission follows the selected
policy and caller contract. Repeated protected sections or unclassified outer
remainder refuse destructive admission. INPUT reviewers must challenge this
proposed grammar before implementation, rather than leave an implicit regex
choice to the implementation stage.

## Review question

Before code, assess whether the chosen policy, grammar, raw provenance and
distinct admission rules form a complete minimal contract for these cases.
Find concrete loss/duplication/deletion scenarios or unnecessary machinery.
Use `Critical / Important / Minor`, source path and line, precise synthetic
scenario, minimal correction and correct ownership layer. Reading code is not
an executed experiment. A missing/aborted review is not a clean verdict.

## Русское резюме

Это чистый публичный проект INPUT-брифа на закреплённой базе, а не запуск круга.
До явного выбора legacy-policy реализация и отправка брифа остаются закрыты.
Матрица конечна по перечисленным случаям; полученные результаты пока ограничены
ранее сохранённым baseline и текущей статической сверкой публичного кода.
