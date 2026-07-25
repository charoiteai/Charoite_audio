# Features

## During the meeting

- **Live transcript** — utterances merge into per-speaker paragraphs instead
  of raw 3-second chunks; a light model re-draws utterance boundaries inside
  a paragraph without changing a single word (strict validation).
- **Live diarization** — "Speaker 1/2/…" per voice from the first seconds.
  The owner's name is never guessed; your mic is labeled with your
  configured name.
- **Live names** — once someone introduces themselves (or is addressed and
  replies), their label is replaced with the name — retroactively across the
  transcript and in all future utterances. The name must literally occur in
  the text, which kills hallucinations.
- **Instant answer (⚡)** — the other side's question is detected via STT
  punctuation and lead words; a ready first-person answer arrives in ~2-3 s,
  with the question shown above it.
- **Cloud answer (☁️, opt-in)** — the same question goes to Claude in
  parallel (your subscription, `claude` CLI): local is instant, cloud is
  deeper 10-20 s later. Off by default — and off on every path: the switch
  is read by `src/privacy.py` and checked inside the thread that actually
  launches `claude`, so a manual request (⌘⇧⏎) cannot slip past it either.
  With the layer off the button is greyed out and says why;
  `SUFLER_NO_CLOUD=1` forces it off whatever the config says.
  The prompt carries only your role,
  never a topic list, and instructs honesty over confidence: meeting facts
  (agenda, numbers, statuses) come from the transcript or are declared
  unknown — early in a meeting a topic-primed prompt used to make the model
  present those topics as the actual agenda.
- **Auto-theses** — 📌 facts/decisions, 💎 highlights, 💭 ideas as the
  conversation flows; the heavy model periodically reviews them (🔬).
  Paraphrased repeats are filtered by a local NLI model (duplicate =
  mutual entailment; a refinement carrying a new fact is not a duplicate).
  The layer is optional: with no model in `models/nli/` it is simply off —
  see src/nli.py.
- **Déjà vu (⏮)** — when the conversation touches a "Core" (a cross-meeting
  topic from your graph), a thesis arrives: "⏮ discussed on Jul 15,
  status was …". Semantic match (bge-m3 embeddings, relative threshold),
  not literal words.
- **Auto-brief at meeting start (⏮)** — once the first utterances reveal
  the topic, the daemon pulls archive context once: top Cores with their
  status and last-discussed dates. Assembled from ready-made graph lines,
  no LLM — instant and nothing to hallucinate. Config: `meeting_brief`.
- **Live draft minutes** — refreshed every ~2.5 minutes; the final protocol
  on demand or after stop.
- **Ask the assistant** — type a question mid-meeting: the answer is built
  first from the live transcript, then graph memory, then model knowledge
  (with the source labeled).

## After the meeting (automatic)

1. **Transcript rebuild** — the full recording is re-diarized per channel:
   echo filtering, micro-fragment merging, clean paragraphs, names.
2. **Graph update** — entities (People/Systems), decisions, action items,
   Cores with status + chronicle; a meeting note with wiki-links.
3. **Meeting archive** — a "date — title" folder with every document and a
   link that opens the graph in Obsidian.
4. **Summary** — a one-minute read: bottom line up front → topics →
   decisions → action items (who/what/when) → open questions → **link to
   past meetings** ("was: … (Jul 15) → today: …") → navigation deeper.
5. **Debrief** (optional) — meeting Q&A, tasks, options for open questions,
   recommendations for the next meeting.

**Core revision** (`src/tier3.py`) — over time the extractor splits one
recurring topic into twin Cores. The revision finds such pairs — an
embedding prefilter (bge-m3) → an NLI judge — and runs itself:
incrementally after every meeting (this meeting's cores against all), and
as a full sweep via `scripts/tier3_cores.py --all-graphs --auto` (cron it
if you like — `--auto` merges only when `tier3_auto_apply: true`, else it
only marks; `--apply` remains for hands-on manual runs). Two levels of permission, because the edits differ in price.
Reversible ones always run: mid-confidence pairs get a "possible duplicate"
note (the morning brief collects those into "Tier3 asks you to merge"), and
nestings ("episode ⊂ process") are cross-linked, never merged. The
irreversible one — merging, where the chronicle is transferred and a
redirect stub is left in place of the duplicate — needs `--apply` on the CLI
or `sufler.tier3_auto_apply: true` in the config. The weaker the evidence,
the higher the bar: cores built by the extractor carry no `## Суть`, so the
judge is comparing today's status lines, and two live tasks of one project
read alike on those. For such a pair a confident verdict is not enough — the
chronicles must also be disjoint: twins come from *different* meetings (June
named the topic one way, July another), while two threads of one project are
carried through the same ones. A pair with a shared chronicle is marked, not
merged. Generic "hub" cores are never touched, and every write is preceded
by a backup into `Ядра/.tier3_backup/`.

**Nightly loop** (`scripts/nightly.sh`, cron/launchd it): Tier3 revision →
**morning brief** → **memory bench**. The nightly revision merges cores
only when `sufler.tier3_auto_apply: true`; without the key it stops at
reversible marks — the right to irreversible edits lives in the config,
not in the schedule. The morning brief
(`scripts/morning_brief.py`) writes `_Сегодня.md` into each graph — the
latest meetings with one-line gists, Decided/Tasks/Open from summaries,
live Cores and merge notes; assembled from ready-made graph lines, no LLM —
your morning context in one minute of reading BEFORE the first question.
The memory bench (`scripts/memory_bench.py` + `config/memory_bench.yaml`,
format in the example file) runs reference questions through the real RAG
loop and checks that must-have facts appear in the answers — degradation
after threshold/prompt tweaks shows up in the nightly log, not in a live
meeting. The steps are independent — a failed revision does not cancel the
brief — but the run exits non-zero if any of them failed: the job used to
end on an `echo`, so launchd reported green even on nights when nothing
happened at all. A sagging bench is a warning in the log, not a failure:
it signals degradation, it does not break the loop.

## Outside meetings

- **Dictation** (global hotkey) — speak → recognized locally → pasted into
  the active field; the clipboard is restored, images included.
- **Voice note** — speak a thought → the model cleans it up, adds a title,
  extracts tasks → a file in the graph (`Notes/`) + remembered in memory.
  The raw text is kept alongside ("As spoken").

- **English documents** — `sufler.language: en` switches minutes,
  summary, instant answers and graph node content to English (validated on an English
  transcript); hints speak the language of your role.
- **Night cycle in one click** — Settings installs the 04:15 launchd
  job (core revision with backups, morning brief, memory bench);
  the same button removes it.
- **Calendar brief** (opt-in) — a Settings toggle: before your next
  meeting the bar shows a button with its title, one click builds an
  archive brief. Only the title and time of the nearest event are
  read, locally.
- **Custom minutes template** — `sufler.minutes_template` in
  config.yaml: your own markdown skeleton instead of the default
  sections.
- **Meeting tasks** — minutes write assignments as checkboxes
  («- [ ] **Name** — what — when»); the Tasks window collects every
  `- [ ]` across the graph with an open-count badge, ticking writes
  `[x]` straight into markdown — Obsidian and the app always agree.
- **Streaming archive answers** — first words in ~1s, token-by-token
  with a typing cursor; the chat model picker lists what Ollama
  actually has.
- **Answer history** — past Q&A collapsed under the current answer,
  surviving restarts (up to 50 entries on disk).
- **Archive questions and briefs** — search v2: Russian morphology
  stemming, IDF (rare query terms weigh more), query coverage, file
  freshness, graph distillates ranked above raw transcripts, result
  diversity (one meeting no longer fills every slot). Weak matches are
  flagged "⚠ possibly not in the archive" — the assistant doesn't
  synthesize from irrelevant fragments. Answer sources are clickable
  (open in Obsidian). With the optional brain companion (:8100) a
  bge-m3 semantic layer finds answers even without word overlap.

## Document format

Everything is plain markdown, readable without rendering: bold-keyed lists
instead of tables, short blocks, the same structure every time, the main
point first (BLUF). Layers: Summary (1 min) → Minutes → Debrief → Transcript.
