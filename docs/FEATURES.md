# Features

***English** · [Русский](ru/FEATURES.md) · [中文](zh/FEATURES.md)*

## During the meeting

- **A nudge to record** — the most expensive mistake in a meeting is a
  forgotten button: with no audio there is no transcript, no minutes, no graph
  nodes. With the calendar enabled (the same opt-in toggle as the brief), the
  app shows a bar: “«Планёрка» has started — start recording?” with two
  buttons. **Recording never starts on its own** — only when you press it; a
  “Not now” is remembered and that meeting is not offered again. The window is
  from two minutes before the start to ten minutes after — later than that the
  offer is pointless, and a bar popping up mid-conversation costs more than it
  gives. All-day events and events without other attendees are not meetings:
  there is nothing to record about “pick up the parcel”.
- **Live transcript** — utterances merge into per-speaker paragraphs instead
  of raw 3-second chunks; a light model re-draws utterance boundaries inside
  a paragraph without changing a single word (strict validation).
- **Live diarization** — "Speaker 1/2/…" per voice from the first seconds.
  The owner's name is never guessed; your mic is labeled with your
  configured name. When the voice model is absent
  (`models/diar/embedding.onnx` does not ship with the product) or
  diarization is off in the config, the daemon says so in a status line at
  the start of the meeting: labels will follow channels, and you see it right
  away instead of discovering it in the transcript.
- **Voice against name** — names are recognised from the text of the
  conversation, and the light model sometimes assigns one to the wrong person:
  a deep-voiced participant becomes "Анна" because the name was spoken nearby.
  On top of the text checks there is a voice gate: the daemon tracks the median
  fundamental frequency of a label, and asks the light model whether the name is
  male, female or unisex ("Саша", "Женя" — unisex, never blocked). A name is
  rejected only when both sides confidently disagree; the label then stays an
  honest "Собеседник N". Inside the androgynous band (≈145-190 Hz, where pitch
  alone does not decide) no register is assigned at all. The error always falls
  towards silence: a missing name can be added later, a wrong one lives in the
  graph for months. The register is never stored and never written into
  documents — it lives in the meeting's memory exactly as long as the voice
  embeddings do (see [PRIVACY.md](../PRIVACY.md)).
- **Live names** — once someone introduces themselves (or is addressed and
  replies), their label is replaced with the name — retroactively across the
  transcript and in all future utterances. The name must literally occur in
  the text, which kills hallucinations. All trust checks live in one place
  (`src/speaker_names.py`) and are equally strict with and without the voice
  model: a name from `sufler.user_name` never goes to a participant (matched
  word by word, so "Igor" is recognised inside "Igor Vetrov"); a name heard
  only in the speaker's own lines, with no self-introduction, counts as
  addressing someone else (saying "Sash, could you…" does not make the
  speaker Sasha); grammatical cases are folded onto known people of the
  graph.
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

**Topic dossiers** (`src/dossier.py`, `scripts/nightly_dossier.py`) — a summary
per cross-cutting topic: current state, chronology, decisions, open questions,
who is involved, every point linked to its source node. A topic cluster is a
core plus everything that links to it; boundaries come from the links a human
already drew. Search consults `Dossiers/_index.json` **first** and only goes
into the graph for details, so "where does this topic stand" is answered from
one written summary instead of a dozen fragments. Rebuilds are incremental: a
dossier carries a fingerprint of its sources, and an unchanged fingerprint
means the model is not called. Hand-written additions live in the
`## Author edits` section and survive rebuilds. To check what a query would
find: `scripts/nightly_dossier.py --find "your question"`.

**Cloud dossier review — optional** (`scripts/nightly_dossier_review.py`). The
local model builds dossiers in bulk and cheaply, but misses the links: that a
decision has been superseded, that a deadline expired, that two sources
contradict each other. Opus sees those, and runs as a second pass. With
`sufler.cloud_edit_graph: true` it edits directly; **off by default**, in which
case it writes `Service_dossier_review_<date>.md` and a human applies the
fixes. Transcripts, minutes and `## Author edits` are untouched in either mode;
every edit is backed up to `Dossiers/.backup/<date>/` first.

**Protocol for participants** (`scripts/protocol.py`) — what you actually
send people after a meeting: the bottom line, decisions, action items, open
questions and risks, assembled from the Summary and the Minutes. Wiki-links
become plain names, thesis markers are stripped, empty sections are dropped.
`--style plain` produces text for email and chat (no markdown), `--copy` puts it
on the clipboard, `--out` writes a file. The raw transcript never enters the
protocol under any flag: mailing participants a verbatim recording of the
conversation is worse than mailing nothing.

**Forget a meeting** (`scripts/forget_meeting.py <date|stamp>`) — the other
side of recording: removes the meeting from all six places it lives — the
transcript and its derivatives, the folder under «Встречи-архив», the
«Встречи/» node, the transcript copy under «Документация», chronicle lines in
Cores (together with the fact that came from that meeting) and links in
Dossiers and people's nodes. A link inside a sentence becomes a
«(встреча удалена)» note instead of a dangling wiki-link. By default it prints
the plan: deletion is irreversible and needs `--yes`. Edits to nodes that stay
alive are backed up into `.forget_backup/<stamp>/` — we delete a meeting, not
someone's notes. Transcript and recording only, leaving the graph alone:
`--keep-graph`.

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

- **Archive folder names carry the meeting time** — «2026-07-24 11-30 —
  Topic»: five meetings a day are distinguishable at a glance, and folder
  mtime lies after re-processing anyway. Old-format folders migrate
  automatically on next update.

## Outside meetings

- **Dictation** (global hotkey) — speak → recognized locally → pasted into
  the active field; the clipboard is restored, images included. In the menu
  bar, dictation, note and diary sit in a column with their shortcuts in a
  separate right-hand column: on one row they did not fit and got truncated
  exactly at the shortcut.
- **Import recorded meetings** — `scripts/import_meeting.py file`
  (audio m4a/wav/mp3, text txt/md, subtitles vtt/srt from Zoom/Teams —
  speaker names preserved) → the full meeting archive: transcript,
  minutes, debrief, theses, graph; meeting date via `--date/--time`;
  the source file is kept next to the meeting materials (APFS clone).
- **Live meeting context** — the topic emerges a few minutes into the
  call; the daemon distills it from the live transcript, searches the
  archive and rebuilds the «past meetings» block in the hint prompt:
  hints, instant answers and Q&A see old agreements on the CURRENT
  topic, not just the two latest meetings. Local, on by default
  (`sufler.live_context`).
- **Cloud-refined hints** — the ladder pattern for hints: the local
  model answers instantly, then a cloud model (Claude CLI, default
  Haiku) appends «☁️ …» right into the SAME hint card (stale
  refinements go to the hints log only). Sends transcript to the cloud
  on every hint — separate switch, default OFF (`sufler.cloud_hints`).
- **Opening brief** — the hint card is not empty at meeting start: it
  shows the last meeting's topic, agreed decisions and open-task count
  from the archive — instantly, file-parse only, main graph only.
- **iPhone companion (v1, app-ios/)** — the phone records a
  meeting/note/diary entry; you pick the delivery folder once in iCloud
  Drive (folder button) — the same import folder the Mac watches.
  Nothing gets lost: an on-device queue in Documents re-sends on every
  launch. `note_*`/`diary_*` files are routed to the notes pipeline.
  Background recording with the screen locked. Build: `xcodegen
  generate` in app-ios/, sign in Xcode.
- **Take the recording by hand** — no waiting on iCloud and no dependency
  on it: a «Share recording» button under the timer hands off the latest
  file anywhere, and the recordings folder shows up in Files on the phone
  and over the cable (`UIFileSharingEnabled`). The five most recent
  recordings stay on the phone after delivery: «iCloud accepted it» is
  not «the Mac got it».
- **The queue is visible in full** — the «queued: 6» line opens a list:
  what was recorded, when, how large, how long it has been waiting.
  Anything older than a day is highlighted — normal delivery takes seconds,
  so whatever hangs longer is no longer «about to leave». Re-send with one
  button, or hand off any single recording from there.
- **Stalled-recording watchdog** — if the file's duration stops growing
  for more than three seconds (an interruption, a stolen microphone), the
  screen says so in orange instead of running a timer over silence.
- **Processing survives a stalled model** — Ollama can hang while looking
  healthy from outside: the model list answers instantly while the actual
  request waits for the timeout. A cheap generation probe runs before the
  graph pass; a stalled local Ollama is restarted automatically (loopback
  only — someone else's machine is not ours to bounce) and the pass retries.
  The restart targets whichever process actually holds the port: the app and
  the brew service coexist happily, and only the one answering matters.
- **Unfinished meetings get picked up** — a failed or abandoned run no
  longer just sits there: the next successful meeting retries the freshest
  leftover (up to three attempts, never touching a run in progress).
  A failure used to mean silence — the meeting simply never appeared.
- **A recording without speech is called that** — forty seconds of silence
  is not a "processing error" but a result: the status says so, the
  transcript stays openable, and the pipeline will not re-process silence.
  A state from a newer pipeline also survives — it used to break decoding of
  the whole status, and the meeting vanished from the window entirely.

- **Import folder (watched)** — point the app at a folder (Settings →
  Import, or `--scan` in the CLI): recordings dropped there become graph
  meetings on their own; processed files move to `done/`, failed ones
  stay visible.
- **Replacement dictionary** — STT mangles domain terms and names;
  `sufler.vocabulary` in the config fixes them declaratively
  (case-insensitive, whole words only) on live meetings, dictation,
  notes and imports alike.
- **Post-meeting hook** — `sufler.post_meeting_hook`: your command runs
  after every meeting (live or imported) with `SUFLER_TRANSCRIPT` and
  `SUFLER_STAMP` in the environment — a local take on webhooks.
- **Diary (⌥⌘J)** — speak a thought → an entry in the personal sphere
  `Дневник/YYYY-MM-DD.md` under «## HH:MM»: your voice with punctuation,
  ideas, checkbox tasks (visible in the Tasks window), the raw «as
  spoken» text; a thought about today's meeting gets a link to it —
  backlinks connect the spheres while the text stays in the diary.
  Personal entries never leak into work search.
- **Voice note** — speak a thought → the model cleans it up, adds a title,
  extracts tasks → a file in the graph (`Notes/`) + remembered in memory.
  The raw text is kept alongside ("As spoken").

- **English documents** — `sufler.language: en` switches minutes,
  summary, instant answers and graph node content to English (validated on an English
  transcript); hints speak the language of your role.
- **Night cycle in one click** — Settings installs the 04:15 launchd
  job (core revision with backups, morning brief, memory bench);
  the same button removes it.
- **Recording reminder** (opt-in) — the calendar toggle shows a system
  notification and an in-window bar when a meeting starts, with explicit
  **Start recording** and **Not now** actions. Recording never starts on its
  own. Optional launch at login uses macOS Login Items so the reminder works
  before the window is opened.
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

## Action items reach the task window

Minutes name who does what by when — the prompt asks for a checkbox, and the
model does produce the name, the task and the deadline. It just wraps the whole
line in its own markdown and loses the `[ ]` along the way:

    *   **- **Dmitry** — prepare the demo. — **Due: tomorrow**.**

The task window matches `^\s*[-*] \[( |x|X)\] +(.+)$`, so nothing above is a
task for it. Measured on a working graph: 89 minute files, **4 visible tasks**.
Every meeting produced assignments; the window stayed empty.

The format is now normalized after generation — deterministically, not by
asking the model more firmly, because it already "complies" as best it can and
drifts again on the next long answer. Only the assignments section is touched,
and only its formatting: names, wording and deadlines stay as written.

Same graph after normalization: **275 tasks**. Existing files are converted
once by `scripts/fix_action_items.py` (dry run by default).
