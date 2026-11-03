# Task 818: independent INPUT review packet

Status: **POLICY A SELECTED; READY FOR ROOT TO FREEZE/SUBMIT INPUT**.
Preparation only; no implementation and no formal review verdict are claimed.
Source base: `bcfdbad0a1497a973b5d4fb65def8eb9e3df3c81`, tree
`20a04d09b803db3efce9d193e5c8ec065562e9ba`.

## Activation record (2026-11-03)

The coordinating Codex chose conservative policy A under the owner's delegated
technical decision-making. The owner asked whether Codex can choose solutions;
this record does **not** say the owner literally selected option A. Waiting for
a separate legacy-policy answer is removed. Independent INPUT review is still
required before code. The previous report dated 2026-11-02 remains historical.

For unknown legacy structure with a standalone protected-heading candidate,
preserve **the entire original file byte for byte** and refuse only the affected
automatic destructive operation, before any model call. Report the concrete
ambiguity reason; continue unrelated dossiers. Inline, closed-fence and quoted
mentions are ordinary text. Never infer an older author's intent from bytes.
Read projections are not write/delete permission. Missing/empty sources alone
are not a blanket refusal. Full rebuild and body-span replacement have distinct
preservation promises, and forget eligibility must never expand.

Root must freeze a tracked public packet SHA and give **the same packet/base**
separately to `gpt-6.1-terra` and `gpt-6-luna`, after the established privacy scan.
No owner data, data-derived fixtures, graph, transcripts, config, untracked
files, repository history or peer verdict may enter that exported packet.

## Requested result

One parser in the existing dossier load/result abstraction makes readers agree
while retaining admitted author content, new-response warnings/links and
current-member source links. Full-file revision holds preserve older generated
annotations; changed full rebuilds have the explicit boundary below and
unresolved follow-up891. Repeated rebuilds stop accumulating body tails in
author edits. Keep model-output
sanitization separate: an untrusted generated body cannot supply protected
sections. Reuse existing lock, backup and CAS doors.

Read [contracts and plan](818-contracts-and-plan.md), [15 acceptance groups](dossier-parser-acceptance.md),
[historical baseline](dossier-parser-baseline.json), [current refresh report](818-policy-a-refresh-report.md)
and [source manifest](818-source-contract-manifest.json). The 43 oracles below
are future requirements, not executed implementation results.

## Frozen grammar and admission

Physical-line candidates outside fences/quotes match exactly:

- Protected: `[ \t]*#+[ \t]*(Источники|Правки автора)[ \t]*`; one or more hashes,
  no upper bound, no suffix text, horizontal spaces/tabs only.
- Body: `[ \t]*##[ \t]*TITLE[ \t]*`; TITLE is exactly `Сейчас`, `Как пришли`,
  `Решено`, `Открыто`, `Кто в теме`. A matching prefix with suffix is text.
- A fence opens on a physical line with zero to three leading ASCII spaces,
  three or more equal backticks or tildes, and optional information text
  (backtick info contains no backtick). It closes only with the same character,
  at least the opening length, zero to three leading spaces and trailing
  spaces/tabs only. Protected lines inside a closed fence are text. An unclosed
  fence makes the existing-file destructive boundary uncertain: refuse it.
- Lines with up to three leading spaces followed by `>` are quotation text,
  including nested quote markers. A tab-indented protected line remains a
  heading candidate as explicitly required by the legacy grammar.

A **complete generated shape** has the closed frontmatter and title/automatic
notice envelope emitted by `render` at the pinned base, all five unique ordered
body headings, then one sources and one terminal author section, without an
unclassified outer remainder. Existing metadata fields may vary or be added by
existing frontmatter helpers; fingerprint must be present, not assumed valid.
Supported protected-heading spelling variants above are accepted in that same
complete shape. The terminal author payload includes all later ordinary text
and non-protected Markdown subheadings; a second protected candidate is never
silently swallowed into that payload. Unclassified outer remainder means an
extra prefix outside the render envelope or extra protected boundary, not an
arbitrary author subheading. The parse projection retains body/author/source content. This is a
syntax class, not proof of historical intent; policy A admits this class.

Full rebuild retains the admitted author payload and regenerates the body and
source section from the new response and current members/files. It preserves
new response warnings/links and current member links, not every older generated
warning or arbitrary extra source link. An existing revision holds the whole
file before generation, including its old warnings/links; body-only review has
the separate protected-byte contract below. Stronger retention of old generated
annotations across changed responses/composition is unresolved follow-up891,
not a guarantee of this parser stage or permission for real-file migration.

An unknown/incomplete/reversed/repeated structure with a protected candidate is
**ambiguous legacy**: its read projection may expose all sections/remainder,
but ordinary rebuild, review/body edit, LABELS_ONLY and dossier deletion refuse
the affected operation without changing any original byte. Duplicate protected
boundaries or unclassified outer remainder also refuse, never first/last-match
truncation. A file with no protected candidate and no uncertain boundary is
representable plain/incomplete text: ordinary rebuild remains supported if
existing decode/manual/revision checks permit it. Thus empty/missing/no-body
inputs and missing sources alone are not globally refused. Review still requires
a complete admitted body and sources; forget retains every old exclusion and
adds no eligibility. The known complete class and no-protected plain class are
distinct, explicit admissions; an arbitrary author-only file is not the latter.

For decode damage, raw byte provenance and the intact author boundary/content
must be established before repair; damaged author data refuses before generation.
Literal valid U+FFFD in intact author text is not decode damage. Independently,
`revision()` at the current base holds a `YYYY-MM-DD`-shaped scalar (calendar
validity is not checked) **or any U+FFFD in the closed
revision scalar**. The damaged-date hold from PR900 takes priority even under
`--full` rebuild: no generation, overwrite or backup. Forget deletion eligibility
must not expand. Explicit forget of a cited source retains its existing revision
clearing contract, including a damaged-date scalar; it is a separate operation
from rebuild. Ambiguous structures still refuse both forget edit and deletion.

## Finite acceptance cases

Each ID is one oracle with explicit representative parameters. `REFUSE A` means
whole-original-file byte equality, zero generation calls for that operation,
visible parse reason and continued processing of unrelated dossiers. It is not
a model failure. All former POLICY placeholders are resolved below.

| ID | Constructed input / action | Required oracle |
|---|---|---|
| G01 | Full `render` envelope, five ordered body sections, sources, author | Complete shape; spans cover all input/remainder; ordinary rebuild and body edit admit subject to existing gates |
| G02 | Full shape with inline `- discussed ## Правки автора` / `##Правки автора`, final author | Inline stays body; final author lines preserved once |
| G03 | Full shape with final `##Правки автора`, then an inline spaced mention in author | Preserve every author line; no substring preference; unknown author-only equivalent is REFUSE A |
| G04 | Protected levels 1,2,3,6,7; leading none/two spaces/tab; gap none/space/tab; trailing none/two spaces/tab | Structural candidates exactly as frozen; same full shape admits spelling variants; unknown legacy variants REFUSE A |
| G05 | Bare `## Источники` plus meaningful body-looking tail in unknown legacy | REFUSE A; entire tail/file retained, no inferred intent |
| G06 | Bare `## Правки автора` in unknown legacy | REFUSE A, identical ambiguity rule to G05 |
| G07 | Inline `см. ## Правки автора`, no standalone protected section | Ordinary text; no manual section extracted; plain-class rebuild may admit; no review/delete permission |
| G08 | Backtick/tilde fences with protected lines; unclosed fence | Closed-fence lines stay text; an unclosed fence REFUSE A for affected existing-file write |
| G09 | `> ## Источники`, `> ## Правки автора`, nested quotes | Quote lines stay text; cannot create outer boundaries |
| G10 | `## Сейчас`, `##Сейчас`, two-space/tab indented start, `## Сейчас extra` | First variants exact body candidates; suffix form text; prefix alone never complete |
| S01 | Full envelope missing sources/author/both | Read projections remain; remaining protected candidate makes incomplete legacy REFUSE A; both absent/plain may rebuild; review/delete refuse |
| S02 | Author before sources | Read projection preserves both and remainder; destructive operations REFUSE A |
| S03 | Repeated sources/author or extra trailing protected section | REFUSE A; never truncate at first/last match |
| S04 | No body heading, prefix only, five sections out of order | Incomplete, never canonical/delete admission; no-protected plain may rebuild; protected ambiguity REFUSE A |
| D01 | Known shape, invalid UTF-8 only in body/fingerprint, intact author, no revision hold | Existing ordinary repair may admit; exact raw backup and complete author content survive |
| D02 | Invalid UTF-8 in author heading/payload | Refuse before generation/write; original bytes unchanged; not model failure |
| D03 | Valid UTF-8 literal U+FFFD in author | Complete known shape keeps literal; author-only unknown REFUSE A for ambiguity, never code-point damage |
| D04 | Date-shaped revision (including `2026-99-99`) or scalar containing U+FFFD; normal and `--full` | Hold before generation; unchanged bytes/no dossier backup; unrelated themes continue; preserve current conservative check and PR900 counters |
| B01 | LF/CRLF with/without BOM | Raw provenance explicit; admitted body edit preserves all outside-body bytes except deliberate revision field |
| B02 | Bare CR, mixed LF/CRLF, Unicode line separator | Refuse affected existing-file write with visible unsupported-newline reason; readers remain non-destructive |
| B03 | Direct `DossierLoad("ok", text)` and disk load of same normalized text | Same interpretation; only disk load has original-byte provenance; direct load cannot admit overwrite of an existing disk file alone |
| B04 | Missing, empty, unreadable file | Distinct states; missing/empty rebuild still supported, unreadable refuses |
| R01 | Split, manual, labels, composition, kept-from-night, plan, doctor | One shared interpretation with separate caller admission |
| R02 | Label only in body/sources/author | Only admitted body label affects body predicate; ambiguous body is unknown, not clean evidence |
| R03 | Composition `[]`, malformed empty, absent | Retain priority and None/empty distinction; fallback read does not admit write |
| R04 | Metadata-looking body lines | Header readers never take body text as metadata |
| W01 | Empty, no heading, no sources, author-only input | Empty/no-protected plain rebuild remains; author-only unknown REFUSE A; no blanket sources guard |
| W02 | Review missing sources or ambiguous | No cloud/generation/write permission from projection |
| W03 | Normal admitted review with links/warnings/manual | Change body plus deliberate revision scalar only; all other original bytes identical; sanitizer retained |
| W04 | LABELS_ONLY with/without revision | Admitted body edit only; keep revision exactly, never add one; ambiguous structure REFUSE A |
| W05 | Forget canonical automatic vs manual/ambiguous/revised/labeled | Never add eligible paths; all prior keep reasons remain; ambiguity preserves file; existing backup/report doors retained |
| W06 | Plan force/unchanged/labeled/revised/gone-source/damaged-source | Existing hold/skip/add/rebuild semantics; U+FFFD revision stays held; no incremental implementation |
| P01 | Full-shape G02/G03 rebuilt twice with identical generation/source input | Complete author text once; stable after first rebuild; new-output warnings/links and current-member sources retained. Changed-output and revision-hold controls expose the full-rebuild boundary above |
| P02 | Admitted review twice with identical fixed body | Stable protected bytes; no duplicate sections |
| P03 | Generator returns only four headings, reordered/duplicated headings or an unclosed fence; also a complete valid output | Check the fully rendered, sanitized candidate with the same parser before backup/write. Reject invalid candidates via existing malformed-output handling without replacing the old file; accepted output is admitted on the next load/rebuild |
| F01 | Dossier/source changes during generation/review | No overwrite; snapshot/fingerprint checks and visible reason retained |
| F02 | Backup failure or graph lock unavailable | No write; existing reporting/index behavior retained |
| F03 | Parse refusal just before write; equal normalized text/different bytes | No write; normalized equality never substitutes for disk provenance |
| E01 | Own temporary graph/config, synthetic files, real rebuild/review/labels functions | Inspect same-input before/after files; deterministic generation doubles are explicitly zero real model calls |
| E02 | Synthetic forget dry-run/apply public functions | Inspect plans/copies/final files; zero added deletion eligibility |
| E03 | Ambiguous dossier with a meeting link and revision, both live graph and handled snapshots | Neither edit nor delete the dossier; skip `_strip_meeting` and `without_revision` for it, keep complete original bytes after dry-run/apply, report manual follow-up and continue unrelated eligible files |
| V01 | Focused regression, CI, critical mutations, OUTPUT/final risk | Exact source SHA and commands/results; unrun checks incomplete |
| V02 | Installation if authorized by root | Independently verify installed files against accepted SHA |

Body edit means splice through existing gated writer with exact raw mapping;
unsupported mapping refuses. Full rebuild regenerates the outer `render` text,
retains admitted author content, new-response warnings/links and current-member
source links, and backs up all old bytes. Older generated annotations absent
from the new response/current inventory are replaced; the whole-file revision
hold is a separate operation boundary. Stronger retention remains unresolved
in follow-up891. The regenerated outer document is not byte-identical.

## INPUT closure before implementation

Independent Terra C0/I2/M0 and Luna C0/I0/M1 reviewed source93f97689 against
basebcf on the identical public snapshot. Coordinating Codex checked the source
and accepted the two Important paths: forget can edit after refusing deletion;
looks_valid accepts four headings and does not establish producer/parser closure.
P03/E03 above add explicit oracles; the original41 become43 without adding an
acceptance group. Forget must hold every destructive edit, including snapshots,
not merely its deletion predicate. Validate final rendered producer output with
the same classifier before any write/backup; keep untrusted-output sanitization
separate and use existing malformed-generation handling, not a new retry policy.
Luna's wording finding is resolved by preserving the date-shaped check above,
including malformed calendar dates; no new calendar semantics are introduced.
These are source-reading decisions, not executed correction experiments.

## Review question

Before code, challenge whether the chosen policy, complete-shape/plain-class
admission, grammar and raw provenance form a minimal complete contract. Find
concrete loss/duplication/deletion cases, unnecessary machinery or unresolved
outcomes. Return Critical / Important / Minor, path/line, precise constructed
scenario, minimal correction and correct layer. Static reading is not an
executed experiment; an absent/aborted verdict is not clean.

## Русское резюме

Codex выбрал A в рамках делегированного технического решения; отдельного
ответа владельца больше не требуется. Спорный старый файл целиком сохраняется,
опасная операция отклоняется до модели с понятной причиной, другие темы идут
дальше. Это подготовленный INPUT-контракт, не реализация и не завершение №818.
