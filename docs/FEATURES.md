# Features

***English** · [Русский](ru/FEATURES.md) · [中文](zh/FEATURES.md)*

## During the meeting

- **A nudge to record** — the most expensive mistake in a meeting is a
  forgotten button: with no audio there is no transcript, no minutes, no graph
  nodes. With the calendar enabled (the same opt-in toggle as the brief), the
  app shows a system notification and an in-window bar: “«Weekly sync» has
  started — start recording?” with two buttons. Optional launch at login uses
  the standard macOS Login Items, so the reminder works even before the window
  is opened. **Recording never starts on its own** — only when you press it; a
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
- **A silent model on the name pass is not passed off as success.** "No names
  were spoken" and "the model never answered" produce an equally empty result,
  and one meeting shipped with "Собеседник 1..5" labels while the run was
  recorded as fully successful. The two are now told apart: if the model stayed
  silent and unnamed labels remain, the transcript gets a warning line in its
  header and the meeting status gets a `names_pending` field. The state stays
  `ready` — the graph is updated, there is nothing to redo in the pipeline —
  but it is visible that the meeting is worth rebuilding once the model is
  free. In the recent meetings list "Ready" becomes "Ready, speakers unnamed",
  right next to the "Retry" button that answers that line. The mark clears
  itself: a repeat run rewrites the transcript in full.
- **Instant answer (⚡)** — the other side's question is detected via STT
  punctuation and lead words; a ready first-person answer arrives in ~2-3 s,
  the question is visible in the status line while ⚡ is answering, and in full in the meeting's _hints.md. The gate is "anyone but the owner"
  (word-level match against `user_name`), so answers keep firing after a
  counterpart gets recognised by name mid-meeting. Short real questions
  pass the filter: an explicit question form — a "?" plus an interrogative
  opening — softens the subject threshold, so "Что с деплоем?" triggers an
  answer while bare "Что?" still does not. The question is passed to the
  model explicitly (the fast trigger hears it in the stream before it
  reaches the transcript), and a model refusal no longer mutes a repeat of
  the same question for the rest of the meeting.
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
- **Auto-theses** — 📌 checkpoints (decision, deadline, action item) and
  💭 the model's own thoughts as the conversation flows; the heavy model
  periodically reviews them (🔬). 💎 "highlights" was retired: the thread
  carries the facts now — a third stream of the same facts was duplication.
  Paraphrased repeats are filtered by a local NLI model (duplicate =
  mutual entailment; a refinement carrying a new fact is not a duplicate).
  The layer is optional: with no model in `models/nli/` it is simply off —
  see src/nli.py.
- **Déjà vu (⏮)** — when the conversation touches a "Core" (a cross-meeting
  topic from your graph), a thesis arrives: "⏮ discussed on Jul 15,
  status was …". Semantic match (bge-m3 embeddings, relative threshold),
  not literal words. Since Aug 24 the theses layer is unconditionally off
  in the app (owner's package) — déjà vu lives only in headless daemon runs.
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
   Surnames and abbreviations the STT writes differently are canonized
   from the graph: a node's name plus its `aliases:` frontmatter is the
   dictionary (People and ALL-CAPS system nodes only), the alias stem is
   replaced while the case ending is kept, and similar-but-unconfirmed
   words are never touched — they go to `logs/lexicon_candidates.md`
   with a ✔ mark when the surrounding words match the node's `отдел:`
   field or body, so confirming a new alias is one edit in the node.
2. **Graph update** — entities (People/Systems), decisions, action items,
   Cores with status + chronicle; a meeting note with wiki-links.
3. **Meeting archive** — a "date — title" folder with every document and a
   link that opens the graph in Obsidian.
4. **Summary** — a one-minute read: bottom line up front → topics →
   decisions → action items (who/what/when) → open questions → **link to
   past meetings** ("was: … (Jul 15) → today: …") → navigation deeper.
   Length is enforced by code rather than the prompt, and sections are
   sacrificed by importance: the link to past meetings first, then open
   questions, then the topic overview. Decisions and action items survive
   trimming — they are why the summary gets opened at all. Decisions are
   not re-discovered either: they go into the model as a ready block taken
   from the minutes, and if the answer comes back empty anyway, the section
   is filled from those same minutes by code.
5. **Debrief** (optional) — meeting Q&A, tasks, options for open questions,
   recommendations for the next meeting.

**Core revision** (`src/tier3.py`) — over time the extractor splits one
recurring topic into twin Cores. The revision finds such pairs — an
embedding prefilter (bge-m3) → an NLI judge — and runs itself:
incrementally after every meeting (this meeting's cores against all), and
as a full sweep via `scripts/tier3_cores.py --all-graphs --auto` (cron it
if you like — `--auto` merges only when `tier3_auto_apply: true`, else it
only marks; `--apply` remains for hands-on manual runs).
The full sweep is quadratic: three hundred cores mean forty thousand pairs and
hours of NLI, so at night it runs with `--since-last` — only cores changed since
the previous pass are judged (per-graph stamps in `logs/tier3_last_run.json`),
and everything-against-everything is checked once a week, on Sundays. The stamp
moves only after a pass that actually happened: with no NLI model or a dead
Ollama the revision returns an empty result, and moving the stamp would drop
those cores from the focus for good.
Two levels of permission, because the edits differ in price.
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
dossier carries a fingerprint of its sources (microsecond precision — an edit
landing in the same second as the scan used to go unnoticed), and an unchanged
fingerprint means the model is not called. Topics without a dossier yet come
FIRST in the night's queue: sorting by cluster size alone gave every slot to
the large topics while small new ones waited indefinitely. The per-run cap is
shared across graphs rather than applied to each. Once a week (the night into
Monday) every dossier is rebuilt: `--full` no longer stops at that cap. Writes
take the shared graph lock — the same one the meeting pipeline and the cloud
review take. Hand-written additions live in the
`## Author edits` section and survive rebuilds. To check what a query would
find: `scripts/nightly_dossier.py --find "your question"`.

**Cloud dossier review — optional** (`scripts/nightly_dossier_review.py`). The
local model builds dossiers in bulk and cheaply, but misses the links: that a
decision has been superseded, that a deadline expired, that two sources
contradict each other. Opus sees those, and runs as a second pass. With
`sufler.cloud_edit_graph: true` it edits directly; **off by default**, in which
case it writes `Служебное_ревизия_досье_<date_time>.md` ("Service: dossier
review" — file names in the graph are Russian) and a human applies the fixes.
The report is written in both modes, in sections: applied (+/− lines, number
of ⚠️ marks, links before/after, path of the backup copy), rejected (with the
reason), step failures (network, limit, exit code — not a content rejection)
and proposed-but-not-applied when editing is off. Before
anything is written the cloud's answer passes a real check, not a "looks like a
dossier" one: exactly five headings in the given order and nothing else that
starts with `#` (so a `### Author edits` smuggled in from a transcript is
rejected, not pasted), exit code 0, no shorter than 60% of the previous body
and not a single lost `[[link]]` to a source — a missing link means a dropped
fact, and the whole revision is rejected, so the prompt tells the model to mark
cancelled items with ⚠️ instead of deleting them. Links are compared the way
the graph resolves them (`[[People/Name]]`, `[[Name.md]]` and `[[name]]` are
one node). A dossier without a `## Sources` section was assembled by hand and
is never touched. Transcripts, minutes and `## Author edits` are untouched in
either mode; every edit is backed up to `Досье/.backup/<date_time>/` first
(seconds in the name: a second run never overwrites the copy taken before the
first).

**Protocol for participants** (`scripts/protocol.py`) — what you actually
send people after a meeting: the bottom line, decisions, action items, open
questions and risks, assembled from the Summary and the Minutes. Wiki-links
become plain names, thesis markers are stripped, empty sections are dropped.
`--style plain` produces text for email and chat (no markdown), `--copy` puts it
on the clipboard, `--out` writes a file. The raw transcript never enters the
protocol under any flag: mailing participants a verbatim recording of the
conversation is worse than mailing nothing.

**One meeting, one folder** (`scripts/dedup_archive.py`) — a meeting's topic
gets refined on re-runs, and the archive folder is named after the topic. While
archiving could not rename, every refinement created a second folder for the same
meeting: by Aug 3 that had happened to 21 meetings out of 62. The cause is fixed
in `meeting_archive` (the folder is renamed now); this script cleans up what piled
up: the folder matching the current topic in the graph stays, files from the extras
move into it, and the extras themselves go to `Встречи-архив/_дубли`. Without
`--apply` it only prints the plan — nothing is ever deleted.

**Merging graphs** (`scripts/merge_graphs.py <donor> <receiver>`) — stitches
a split graph back together: on Aug 3 a work meeting drove off into a brand
new graph the model honestly invented from the content. The pipeline now
prevents splits (the prompt lists known graphs), and this script heals the
ones that already happened: new files move over, name collisions get
appended into the receiver's file as a "moved from graph …" section (donor
frontmatter stripped — people and system nodes are additive, their
histories must not be lost), meeting lines migrate into the receiver's
`_MOC.md`, and the donor's `_MOC.md` becomes a "merged into …" note.
Without `--apply` it only prints the plan. Applying validates every operation
before the first move, only auto-merges Markdown collisions, keeps a recovery backup
and restores all touched files if any later step fails.

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

**Nightly loop** (`scripts/nightly.sh`, cron/launchd it): **morning brief**
(early) → Tier3 revision → **dossiers** → *(optional)* **cloud dossier review**
→ file dedup → **morning brief** again → **memory bench**. The brief is written
twice: it takes seconds and never calls the model, while the revision on a large
graph runs for hours — and one night ended before it did, leaving yesterday's
`_Сегодня.md` on screen in the morning. The early pass guarantees a brief; the
late one rewrites it on top of tidied cores and fresh dossiers.
The nightly revision merges cores
only when `sufler.tier3_auto_apply: true`; without the key it stops at
reversible marks — the right to irreversible edits lives in the config,
not in the schedule. The cloud review of the cores (`nightly_claude_cores.py`) no longer takes
"the first ones alphabetically": cores changed since the last run go first,
then the rest by how long ago they were shown, the longest-waiting first
(one slot is always theirs) — whole, never cut mid-file; what went to the
cloud is remembered in `logs/nightly_cores_seen.json`, and the night's log
states the real coverage ("cores 35 of 161"). Until 22.08, with hundreds of
fresh cores, the review saw ~4% of the corpus, the same ~4% every night. The morning brief
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

- **Update refusal is visible, not silent** — the bundle-replacement helper refuses to run while the app is alive (recording protection), but its exit 75 went to /dev/null: the update silently did not install. The refusal now leaves a marker and the next launch shows what happened and what to do. Companion recordings (iPhone/Android) can no longer silently overwrite each other on a same-second name collision; calendar events without a date no longer crash the list; dictation, import and chat statuses are localized; Ollama model pulls bypass the system proxy — the fourth and last spot of the 13.08 rake.

- **In-app update, fail-closed around recordings** — the update button
  downloads the release, verifies its sha256 and re-checks for a live
  recording right before swapping the bundle: a meeting that started while
  the update was downloading cancels the install, not the recording. The
  replacement helper independently re-checks that the app really exited
  (a 10-second timeout is not proof) and gives up otherwise; the old copy
  survives until the new one is fully in place; paths travel as arguments,
  so quotes or `$()` in an .app name stay data, not shell.
- **First run without a terminal** — the onboarding screen shows the
  readiness check (environment, config, Ollama, models, microphone, graph
  folder), and a failed item is fixed in place: a missing model is pulled by
  the app itself through the Ollama API, with percentages from its own
  stream; recipes that a button cannot fix are copied to the clipboard whole
  instead of being retyped from the screen. "Start listening" unlocks once
  no blocking items remain.
- **Dictation** (global hotkey) — speak → recognized locally → pasted into
  the active field; the clipboard is restored, images included. The text
  goes to the app — and, with the Accessibility right, the window — where
  dictation started: recognition takes seconds, and if something else is
  in front by then, the text stays in the clipboard (the previous clipboard
  content is not restored), the strip and the status say so — press ⌘V in
  the right field. Started from Charoite's own menu, the text goes wherever
  you click, as before. Passwords: if the dictation touched a password
  field at any moment (or one has the focus when the text arrives), nothing
  is pasted and nothing is shown — the text waits in the clipboard, the strip
  and the status carry only the instruction; without the Accessibility right
  a password field cannot be told apart, so the live draft strip is not
  shown at all. Every piece of the live draft reaches the strip only by a
  focus read started after the piece arrived (an inter-process request,
  milliseconds): a click into a password field mid-speech hides it with the
  next piece or with the once-a-second watch, and it does not come back
  before the dictation ends; if the app in front does not answer, the strip
  stays silent. A password field is recognised through Accessibility: an app
  that does not expose its tree (Chromium browsers with accessibility off)
  cannot be told apart — there the strip and the paste behave as in a plain
  field. In the menu
  bar, dictation, note and diary sit in a column with their shortcuts in a
  separate right-hand column: on one row they did not fit and got truncated
  exactly at the shortcut.
- **Live dictation draft** (macOS 26+) — while you speak, the system
  on-device engine shows a draft on a floating strip at the bottom of the
  screen (it never steals focus — you are dictating into someone else's
  field). The draft is not the result: on the 2026-09-02 reference it made
  12.4 % word errors against GigaAM's 2.9 % and drops domain terms, so the
  final text always comes from GigaAM after stop. The draft steps in only
  when python started but could not recognize (no model, a broken import)
  — dictation keeps working on a Mac without the model, with a status line
  saying so. The watchdog gives the recognizer 25 s plus a fifth of the
  recording (ten minutes of speech take ~23 s at 26×, up to a hundred on a
  busy machine), then asks it to quit and kills it ten seconds later; when
  it had to, the draft steps in the same way — the status line and the
  strip say whose text went in.
  Notes and diary have no strip and never
  take the draft: their text goes into the graph and memory, where accuracy
  matters more than immediacy. The app never downloads speech assets (the
  kill switch and PRIVACY.md stay true): if the dictation language is not
  installed in System Settings → Keyboard → Dictation, there is simply no
  draft.
- **Import recorded meetings** — `scripts/import_meeting.py file`
  (audio m4a/wav/mp3, text txt/md, subtitles vtt/srt from Zoom/Teams —
  speaker names preserved) → the full meeting archive: transcript,
  minutes, debrief, theses, graph; meeting date via `--date/--time`;
  the source file is kept next to the meeting materials (APFS clone).
- **The meeting thread** — the main thing on screen while people talk. It is
  not rebuilt every few minutes but **grows**: the model sees what has been
  collected and adds only what is new; nothing new — it stays silent and the
  screen does not twitch. Topics act as anchors, lines pile up under them, and
  the kind of a line is visible from its mark: `●` topic, `-` what was said,
  `⚑` a decision or deadline, `?` an open question, `⏮` what happened on this
  topic before (with a date, from the archive). Earlier topics collapse into a
  heading with a counter — a thread is for reading, not scrolling. A restated
  thought is dropped by code: both character similarity and overlap of
  meaningful words are checked. The model is called by how much talk piled up,
  not by the clock: silence in the room produces no new lines. **The thread is the only canvas on screen**: answers (⚡ instant, ☁ cloud) and theses (📌 decision, 💭 thought) are woven into it as lines by the daemon instead of living in panes of their own. While those panes were separate, an auto-answer arrived every half a minute, the hint pane never went empty — and the thread, the whole point of the screen, never showed at all. The ⏮ button and the `⌘⇧E`
  hotkey left the panel together with the theses contour (owner's package,
  Aug 24); ⏮ archive lines remain part of the thread format for headless
  runs. **A speaker's name
  appears only when the voice changes**: "Speaker 4:" on every line read like
  an interrogation protocol; now consecutive lines of the same person flow
  without the prefix. **The cloud edits the thread instead of commenting next
  to it**: refinements used to arrive as a separate "☁️ …" block you had to
  match against the canvas yourself; now the reviser returns "line → more
  precise" pairs, the line is fixed in place with the changed words
  ==highlighted== (sky-tinted in the app — cloud stays visible), and the full
  "was → became" trail goes to the meeting's log file.
- **The hint follows the thread** instead of answering for you: current
  topic → who said what → **why** this is being discussed → **what
  happened before** on this topic in the archive (with a date) → what is
  still open. Empty sections are skipped, and several topics inside one
  stretch of talk split into separate cards. The format lives in code
  (`src/llm.py`, `HINT_FORMAT`) and is the same for everyone: the role in
  the config describes context and terminology, not layout. The ready
  first-person answer is still there — on its own hotkey. A speaker is named only when the voice changes, with no reporting verbs — substance, not paraphrase.
  **On screen the hint is a card ABOVE the thread, not instead of it**
  (#255 fixed twice, then #22): the old either/or pane let one hint hide
  the thread until the meeting ended, and a Stop used to wipe the
  meeting's outcome off the screen — now the thread stays put, an auto
  hint lives on the card for at least three minutes (it used to dim with
  the next thread update — half a minute was not enough to read it), an
  aged one yields to the thread, while an answer the
  person ASKED for stays until the next hint replaces it or they dismiss
  it with the card's ✕ (a thread tick lands seconds after the answer —
  it must not eat it). A hint mid-stream is never cut by a thread event
  either; the daemon marks streams as manual/auto so the app can tell.
  The transcript and hint tokens repaint the screen in batches (feed up
  to 4 times a second, stream ~7 fps) rather than per recognized chunk:
  dense speech does not eat button responsiveness.
  While streaming, the view anchors to the top of the card so tokens do
  not scroll past the reader. If hints are toggled off, the
  empty pane says so instead of promising a thread that will never grow.

- **Live meeting context** — the topic emerges a few minutes into the
  call; the daemon distills it from the live transcript, searches the
  archive and rebuilds the «past meetings» block in the hint prompt:
  hints, instant answers and Q&A see old agreements on the CURRENT
  topic, not just the two latest meetings. Local, on by default
  (`sufler.live_context`).
- **Graph-node cross-check during a live meeting** — when a person,
  system or core with a node in the graph comes up in conversation, its
  history (top of «## Meetings»/«## History», status line) arrives as ⏮
  lines in the current thread topic: old agreements are visible while
  the talk is still going. No brain server, no LLM — name stems against
  graph files (`src/graph_nodes.py`). The automatic path is strict: a
  multi-word name must assemble fully, a single-word one must be heard
  twice (except digit codes and already-identified speakers), an
  ambiguous name stays silent, at most four nodes per meeting. The ⚡
  question path is looser: nodes mentioned in the question itself are
  added to the instant-answer prompt. The same nodes back up live
  context and the manual ⏮ when the brain server is down.
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
  Drive (folder button) — the same import folder the Mac watches. A call or
  video meeting during a recording is a pause, not a failure: on iPhone the
  microphone belongs to the call (an iOS rule), the app says so plainly,
  waits for the call to end and resumes THE SAME file; resume attempts and
  file rotation are suppressed for the duration — on 07.08 they were what
  turned a 30-minute meeting into a 40 KB scrap.
  **Listens right away (№167)**: open the app and the recording is already
  running (setting “Record as soon as the app opens”, on by default, the
  last kind you picked, once per launch, not on return from background);
  opened during a call, the app arms itself and starts on its own when the
  call releases the microphone, while the app stays open (iOS will not
  start a backgrounded app; return within 30 minutes and it starts on
  return); autostart waits for a chosen delivery folder; an App Intent “Start recording in
  Charoite” for Siri, Shortcuts and the Action button opens the app and
  starts; the PrivacyInfo.xcprivacy manifest is added. iOS never starts a
  recording from the background and no app records the call itself — that
  is the platform, not the app. A folder
  outside iCloud cannot be chosen: on-device storage looks identical in
  Files but never syncs anywhere — such a bookmark is rejected with a clear
  error, and stale local bookmarks are forgotten so the app asks to pick
  the folder again.
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
- **A forgotten recording stops itself** — on 17.08 a meeting ended, nobody
  pressed Stop, and the laptop recorded an empty room for 18 hours 25 minutes.
  Recording now ends on its own: after 5 minutes if nobody ever spoke, after 15
  if people talked and went quiet (a pause to read a document or a silent demo
  is normal in a live conversation), and after 6 hours in any case — the
  duration ceiling. Silence is measured by RECOGNIZED speech and only by new
  transcript lines, not by loudness: a fan and a keyboard are not a
  conversation, and a repeated hallucination on noise is eaten by the
  deduplicator and does not reset the silence timer. The flip side is stated
  honestly: loud background speech — a TV, a radio, a video — counts as talking
  for us, and only the ceiling stops such a recording. The number of speakers is
  deliberately not part of the rule: without diarization models every line of an
  in-person meeting carries the same label, so "one voice" means nothing. What
  the rule does use is the CHANNEL: if system audio never carried speech during
  the whole recording — only you were talking — quiet time is cut to 10 minutes
  (`alone_minutes`). That signal needs no models, but it cannot tell an
  in-person meeting from a dictaphone: set `alone_minutes: 15` and the rule
  changes nothing, `0` disables it. Two more honest limits: the signal comes
  from VAD energy, so a notification chime counts as "someone is there" (safe —
  you keep the 15 minutes), while a quiet speaker or a missing capture device
  reads as "alone" (that is the case the threshold exists for); and with
  `silence_minutes: 0` the rule stays silent by default, though an explicitly
  set `alone_minutes` still works. A
  warning arrives a minute before (a banner too, if the window is not visible),
  and any remark cancels it. The recording stops the same way the Stop button
  stops it: the file is finalized, the transcript is rebuilt, the graph is
  updated — nothing is lost, and the status afterwards names the reason. The
  first two minutes are never touched ("I started it while everyone was
  gathering"). A farewell heard in the live transcript ("bye everyone",
  "see you") shortens the wait: after one farewell the silence threshold
  drops to a minute (with an instant warning any remark cancels), and two
  farewells in a row — an exchange of goodbyes — stop the recording at
  once. The detector is deterministic and deliberately narrow: a short
  closing phrase, not a conjunction inside a sentence. Everything is
  configurable under `sufler.autostop` (`farewell_seconds`, 0 disables),
  and `autostop: false` turns it off entirely.
- **A recording without speech is called that** — forty seconds of silence
  is not a "processing error" but a result: the status says so, the
  transcript stays openable, and the pipeline will not re-process silence.
  A state from a newer pipeline also survives — it used to break decoding of
  the whole status, and the meeting vanished from the window entirely.
- **The Meetings section (former "Recent meetings" window)** — twenty meetings from the last two
  weeks: state as a colored dot, "Open" and "Transcript" on every row, a
  "Retry" button on failed ones. A ready meeting's row shows its duration
  (from transcript timecodes, cached — the file is re-read only when the
  meeting was re-processed). While one meeting is being retried, the
  "working" indicator shows only on that row: ready meetings are not
  concerned with someone else's retry. The menu-bar "Recent meetings" item
  opens this workspace section and is visible even with an empty history —
  the list explains its own emptiness; the separate window is gone (#22),
  the full archive lives in the archive folder.
  The search field in the header answers "where did we decide X": lexical
  search over the archive's summaries, minutes and debriefs (plus graph notes
  for meetings that never reached the archive), newest first, click opens the
  document. Transcripts are deliberately not scanned: by then decisions live
  in the upper layers. Library search also sees graph NODES: a person's
  name leads to their node with the whole history (name matches come
  first, by stems — "payments" finds "Payment provider"), matches inside
  node bodies follow the meetings; a node opens as a graph file.
- **Recording from the Today screen** — a capsule button in the spirit of the sufler one: start/stop with a timer and a live waveform, an honest ⌘⇧␣ shortcut and a readiness line fed by the real setup checks (Python, microphone, system audio, Ollama, models, graph) with the passed-checks count and the first problem as a chip. Yesterday's ready result no longer hides the next-recording button: “Open result” and the capsule live side by side. During processing the capsule is deliberately absent: recording and the pipeline compete for local models. First launch routes to onboarding, not straight to recording.
- **The meeting card** — a ready meeting opens on click: topic, date,
  duration (from transcript timecodes), participants, the one-line gist,
  decisions and action items. By default the card shows the **detailed
  minutes**: discussion topics, full wording of the decisions, action
  items with deadlines, open questions and risks — everything that
  previously required a trip to Obsidian. The "Detailed / Brief" switch
  brings back the Summary digest, and a protocol written while the
  meeting was still running is honestly marked as a draft. The model
  writes minutes in several markup styles (`- Topics:`, `**Topics:**`,
  `## Discussion topics`); the parser understands all of them.
  "Copy" puts the summary,
  the tasks, or everything into the clipboard — into a mail without
  opening a single file. "Open", "Transcript" and "Obsidian" buttons are
  right there. "Meeting ready" no longer means "go figure out a markdown
  file". When a cloud review ran for the meeting, the card honestly shows
  its outcome from the log: "N graph edits", with an unsaved review file
  highlighted — before, the review worked invisibly and its edits were
  only discoverable in the logs.
- **Renaming a meeting** — the pencil in the card, or
  `scripts/rename_meeting.py <stamp> "New topic"`. The topic is invented
  by the model and is sometimes off; changing it by hand meant visiting
  five places. The script carries the new topic everywhere: transcripts/,
  the archive folder (and links inside it), the copies in Documentation,
  the graph note heading, the app status. A main transcript that never got
  a topic — including the second-precision one from meetings processed
  before the pipeline fix — receives it too: such meetings used to show up
  in the list as a date instead of a topic. Fresh second-precision
  transcripts are named by the pipeline itself: seconds in the file name
  are not a topic, so the main file and its derivatives get the
  minute-form name with the slug (files of a neighbour meeting in the
  same minute are neither picked up nor overwritten). Two meetings in one
  minute (the daemon restarts after a crash within the same minute) live
  apart in the graph: the minute key goes to the earliest one, the second
  gets a second-precision key (`Встречи/2026-08-21_125812`, archive folder
  "12-58-12 — …"); one rule — `meeting_stamp.graph_key` — shared by note
  lookup, forgetting and renaming (a stamp with seconds selects the second
  one). The old topic stays in aliases — search by it keeps
  working; `[[Встречи/stamp]]` links never break, the topic is not part of
  them. Without `--yes` the script prints a plan and touches nothing.
- **Prep screen** (menu bar → Today) — help BEFORE the meeting, not
  after: the rest of today's calendar events (same opt-in access as the
  recording cue), "previously on this topic" — three archive hits for the
  next event's title (utility tails like "(weekly)" are trimmed), open
  action items and the last meeting's card. Everything gathers in one
  window a minute before the call, no tour across four windows.
- **One meeting workspace** — the macOS app now has one main window with
  Today, Meeting, Meetings, Tasks and Memory in the sidebar. Today follows
  the lifecycle from the next calendar event through recording and processing
  to the ready result. The meeting library keeps the list and card side by
  side, and a search hit opens that card instead of a raw Markdown file.
  Menu-bar actions and prep navigate to the same workspace; the separate
  memory chat remains available only as an optional pop-out.
- **Meeting library as a card feed** (August 2026 revision, screen 3 of the
  macOS mockup) — the Meetings column is grouped by day: Today · This week ·
  Earlier, with a summary line above ("8 meetings · 1 processing · 1 failed",
  every number sourced from the pipeline state). Each card carries a state
  dot (a processing meeting pulses indigo), duration, participants and
  action items as counts with a source, the gist, and a mini segment of
  reading depths — Summary · Minutes · Analysis · Transcript: a click opens
  the card straight at that depth, a missing depth is a dashed chip. A
  failed meeting says what happened in words ("Failed — source kept: Ollama
  did not answer in 300 s") and offers "Retry processing" in place; a
  recording without speech is a result, not an error. A search with no hits
  is the shared EmptyState with "Clear search". The feed is buttons in a
  scroll view rather than a List with selection — the same choice as the
  sidebar, which lost clicks inside a List (and whose own ScrollView later
  drew rows two positions below their clickable spots — the sidebar is a
  plain VStack now, the drawn row IS the clickable row). Grouping, summary and plurals are
  a pure `LibraryScreenPolicy`, tested without UI.
- **Memory screen shows where an answer came from** (August 2026 revision,
  screen 5; palette follows the meeting library by the owner's call) — a
  model answer is a white hairline-bordered card, and under the text sit
  source chips parsed from the archive-search context: meetings as filled
  indigo capsules (a click opens that meeting's card in the library), nodes
  and dossiers as outlined ones (a click opens the file). Below the chips a
  provenance line: "local · model · seconds · N meetings in context" — or
  "memory off" when the graph toggle was off for that answer, and a "⚠ weak
  graph matches" chip instead of chips when the search itself flagged low
  confidence (no invented sources). The header carries a live "Ollama
  responds · 0 network requests" chip that turns into a warning when the
  local runtime is silent. On windows ≥760 pt a right column "What memory
  knows" shows meeting/node/dossier counts and the freshest cores with a
  status line each — counted from the graph on disk, refreshed at most once
  a minute. Source parsing, labels and the meta line are a pure
  `MemoryScreenPolicy`, tested without UI.
- **Text-pair telemetry for the owner's signature** (24.08) — with the
  meeting playing through speakers, the interlocutors' echo lands in the
  microphone and the owner stayed «Собеседник N» on every live speaker
  meeting: the id cross-check is blind (the live tracker labels each
  channel independently, so the echo's mic id never matches the
  speaker's system-channel id). The daemon now DETECTS text pairs —
  a phrase whose unique words overlap ≥0.8 of a recent phrase from the
  other channel, five-plus words, an 8 s pair window, either arrival
  order — and counts them per voice. Two review rounds showed that
  auto-marking on top of this (single hit, double hit, owner guard) is
  false-positive-prone from one side of the timeline or the other, so
  the counters deliberately do NOT touch the signature yet: they feed
  the once-a-minute owner-pulse line (aggregates only — voice counts,
  top share, echoed count, text pairs, signed verdict; per-voice numbers
  never reach the disk, keeping the "nothing voice-derived on disk"
  promise). Enabling the marking is a separate decision on top of field
  data from real meetings. Phrase buffers live only in process memory.
- **Graph memory has one client** (24.08, overhaul batch D-П3) — the
  daemon's three hand-rolled POSTs to the brain server are merged into
  src/brain.py: one request body, the project-graph folder resolved in
  one place; timeouts and degradation stay with the loops (⚡ 2.5 s,
  deja-vu 8 s, deep 6 s). The owner's question in the audit label is no
  longer cut at 120 chars — the cap is 400 (№95).
- **Heavy background work coordinates instead of colliding** (24.08) — the
  night of 23→24.08 a manual test-mutation run shared the one local model
  with a live meeting and then with the nightly cycle: dossiers caught 35
  ReadTimeouts of 300 s each. The heavyweights now share one vocabulary
  (busy_signals): the mutator refuses to start while a meeting is being
  recorded or processed or the night is running (honest exit, --force to
  insist), holds an exclusive flock for the whole run — the same kernel
  mechanism as the daemon's meeting lock, so a killed process releases it
  instantly — and yields between mutants the moment a recording or the
  night starts; the night, in turn, waits for the mutation lock exactly
  like it waits for meeting processing, and refreshes its running status
  on every step so a long night never turns invisible.
- **The hint layer explains its own silence** (24.08) — three meetings in
  a row the auto-hint layer was silent while minutes and deja-vu worked,
  and a dead loop was indistinguishable from "nothing to say". The daemon
  now keeps the layer observable and self-healing: a once-a-minute
  hint-pulse line in the error log (layer on/off, new conversation chars,
  last generation outcome, failure streak), a cmd-in line for every panel
  command received (payload commands are logged as word + length only —
  owner content never enters the error log), a guard in the main loop
  that restarts a dead hint thread (up to three times, then an honest
  "falls repeatedly" status, repeated every five minutes while down), and
  a stall detector that reports a thread that is alive but has not ticked
  for two cycles — the hung-in-generation case a liveness check cannot
  see. State only, no transcript content; deploys itself on the next
  meeting start since the daemon lives exactly one meeting.
- **A copilot panel without extra words** (owner's package, Aug 24; the
  same evening batch G-П1 of the overhaul map removed the dead thesesOn
  toggle itself — the layer is silenced by the load-bearing quiet sync) — the
  ⚡ answer to the other side's question is a single highlighted thread line
  (semibold, action color) with no question line: the question stays in the
  `_hints.md` audit, the canvas does not need it. The theses contour left
  the panel (the chip and the ⏮ button; the daemon layer is muted
  unconditionally), the manual request button was renamed to "Digest" —
  the "Hints" layer chip lives next to it and two identical labels read as
  a duplicate. The digest writes tighter: up to two lines per topic,
  12 words each.
- **The hint engine never dies silently** (Aug 24) — the auto-hint loop
  wraps its whole iteration step in try (an exception before the inner try
  used to kill the thread forever: the Aug 24 meeting went without a single
  auto hint while the heartbeat looked alive), three failures in a row are
  reported as a status; all eight holders of the hint lock (auto, manual, ⚡, minutes, thread, ⏮,
  deep analysis, archive topic) acquire it with a waiting ceiling — a wedged
  neighbour no longer freezes the rest forever, the person gets "the hinter
  is busy" instead of silence; the manual request's "yield" signal is
  cleared on timeout too, otherwise auto hints would forever yield to a
  manual request that no longer exists.
- **Calendar inside the meeting library** — the week strip lives on top of
  the Meetings list: dots mark days that have recordings, one click turns
  the list into that day's feed — recordings as usual selectable rows plus
  dimmed rows for calendar events that never got recorded ("14:00 · not
  recorded", "now", "upcoming"). A recording attaches to an event when it
  started inside the event's window (fifteen minutes early still counts,
  and back-to-back meetings never share one recording); events that have a
  recording are not shown twice — the recording speaks for the meeting.
  An active search takes priority over the day filter, without calendar
  access the strip still navigates by recordings alone, and connecting is
  one small button on the strip. An empty feed says that calendar events are
  unavailable instead of claiming the day had none; EventKit changes refresh
  an already open day without reopening the section. One section answers both "what was
  decided?" and "what happened on Tuesday?" — a separate Calendar screen
  no longer exists.
- **Meeting actions without Terminal** — the card copies a participant-safe
  protocol through `protocol.py`, opens the transcript for correction,
  rebuilds the result and previews every trace before the destructive
  `forget_meeting.py` confirmation. The established scripts remain the one
  implementation of the privacy rules. Destructive actions are tinted red,
  renaming can be cancelled (cross or Esc), the messenger-style protocol is
  free of markdown markup, and the card shows when the meeting itself
  started — not when the pipeline finished writing its status. "Rebuild
  result" reports what actually happened: started (if the recordings are
  still kept, the transcript is re-recognized and the previous version
  goes to transcripts/.prev; a transcript you edited by hand is never
  re-recognized — the minutes are rebuilt from your text when they were not
  edited by hand themselves, otherwise only re-stamped; a second click with
  nothing changed does nothing; the hash of the last machine write lives in
  live.json, and `CHAROITE_FORCE_STT=1` on the command-line rebuild
  re-recognizes anyway, edits going to .prev), a rebuild or retry of another meeting is
  already running, no transcript on disk, or failed to launch (a missing
  python is caught before the process starts) — it used to say "started"
  on every click, including the silent refusals; the item stays greyed
  out for the whole run of this meeting's rebuild, the message is reset
  when another meeting is opened, and a process that died after launch
  turns the line into the failure text.
- **Portable meeting card** — each archive folder receives a recoverable
  `meeting.meta.json` with stable, language-independent keys for participants,
  gist, decisions, action items and open questions. Markdown remains the
  source of truth; the manifest is regenerated from it. macOS, iOS and Android
  show the same structured card and fall back to legacy graph notes when an
  older meeting has no manifest.
- **Action items stay attached to their meeting** — the result card and prep
  screen show live checkboxes from that meeting's `Минутки.md`; checking one
  writes `[x]` back to Markdown. “All tasks” opens the Tasks workspace already
  filtered to the meeting, while every task group can navigate back to its
  result card. Search, completed-item filtering and visible-list copy work in
  the same screen. If Obsidian moves lines between scan and click, Charoite
  relocates one unambiguous item or refuses the write instead of completing a
  neighbouring task. A newly finished pipeline refreshes the list itself.

- **Rebuild always finds its recordings** — the meeting name is produced by
  a single module across the pipeline: the daemon names channel recordings,
  rebuild looks them up, and an app-initiated retry resolves the
  minute-precision titled name to the seconds-precision stamp of the
  recordings. Both channels are resolved together: if mic and system audio
  point to different meetings in the same minute, rebuild refuses them instead
  of producing a mixed transcript. The silent format drift of 28.07 cost every meeting its final
  transcript — the naming contract is now held by end-to-end tests.
- **Models sized to your Mac** — the first-run wizard reads the machine's
  memory and offers three sets ("Full", "Balanced", "Light") with model names
  and the recommended one marked; the chosen set is written to the config and
  downloaded with one button. Presets used to live as a config comment and
  people had to find their own line — a silent mistake: an oversized model
  does not fail, it swaps, and the product merely feels slow.
- **Python runtime inside the app** — Charoite.app ships a portable CPython
  with the runtime dependencies (346 MB; heavy optional STT presets are left
  out). `git clone`, `venv` and `pip` disappear from the install: the terminal
  is only needed for Ollama. Running from source is unchanged — the app uses
  the embedded runtime when present and the neighbouring `.venv` otherwise.
- **Setup without a terminal or YAML** — the first-run wizard asks for your
  name and the graph folder and writes them into the config itself (the folder
  is picked in a panel); the voice-separation model installs with a button,
  running the same script that used to require a console. Editing
  `config.yaml` by hand is no longer needed.
- **System audio out of the box (ScreenCaptureKit)** — the app captures
  meeting audio with macOS itself: no driver, no admin password, no
  Multi-Output Device. One system permission on the first recording and that's
  it. No aggregate devices are created at all, so there is nothing to wedge
  CoreAudio (taps did it four times). On macOS 15+ the microphone arrives in
  the same stream and PortAudio never opens — taking the whole "dead stream
  hangs on close" class of failures with it. BlackHole remains a fallback.
  Start and Stop are serialized through an explicit lifecycle: repeated Start
  commands are single-flight, and the next meeting cannot begin until the
  previous capture has closed. Every capture writes to a session-specific PCM
  directory, so even a delayed ScreenCaptureKit callback cannot mix two
  back-to-back meetings or truncate the newer one's audio. A stream that
  stops on its own — sleep, a display change while docking, a broken
  connection to the capture service (−3805), "Stop" in the system screen
  recording indicator (−3817) — is recreated automatically: onto the same
  files, with a 2→32 s backoff, up to five attempts (usually ~1.5 minutes,
  about two in the worst case with five 10-second build timeouts; the app
  watchdog does not count audio silence as a failure while a recreation is
  in progress, while the daemon itself and the STT pulse stay under its
  watch); the daemon tails those files and resumes from its last
  position without a single change on its side. Frames are counted per
  stream, not on a shared counter; the system calls that build a stream are
  capped at 10 s, so a hung capture service cannot hold "Stop" hostage. A
  second "Stop" by the person within two minutes is respected: the meeting
  is closed the same way the Stop button closes it (recording and graph
  preserved) rather than left running without audio. If recovery
  fails, the status line and a system notification say "meeting audio lost"
  and the recording restarts immediately along the watchdog's path (a fresh
  capture, falling back to BlackHole if ScreenCaptureKit is still gone; no
  more than two such restarts per meeting — a third loss closes the meeting
  along the Stop button's path, with the recording preserved and the reason
  in the status line) instead of silence until the end of the meeting (previously
  `didStopWithError` was only logged, and on macOS 15 the microphone left
  with the stream). Verified live on 2026-08-23: SIGKILL of `replayd`
  mid-capture → −3805 → the stream was recreated in ~4 s (2 s pause + build
  + one second of frame check) and frames resumed.
- **Core Audio tap — removed (2026-09-02)** — the second native route to
  system audio (the app reads the tap with its own IOProc and hands the
  daemon a PCM stream) was proven in the field (38.9 s recorded), but the
  very cycle of creating and destroying the tap aggregate wedges CoreAudio
  on macOS 26.5: after a meeting the machine's speakers go silent until the
  audio subsystem is restarted. Disabled on 2026-08-07, the code is now gone
  from the package — a reserve that mutes the machine is not a reserve. What
  stays is the cleanup: aggregates left behind by those versions or by an
  app crash are destroyed at launch and at quit, because a single orphan
  hung CoreAudio for the whole machine. The fallback when ScreenCaptureKit
  is unavailable remains BlackHole.
- **The second window renders like the first** — the sidebar column width is
  set explicitly, so windows of the same scene no longer diverge in layout:
  the second window used to open with a collapsed sidebar, section labels
  vanished and the record button and recent-meetings column slid off the edge.
  Panes now shrink by priority instead of being clipped by the window.
- **The meeting toolbar never hides buttons** — when width runs short the
  header scrolls horizontally, and meeting actions sit right after the record
  button: they used to slide off the right edge the moment recording started.
  Button labels never shrink or get clipped.
- **A single button scale** — seven roles (prominent, regular, quiet, link,
  destructive, filled destructive — confirmation only, icon) and three sizes instead
  of four system styles in 48 places. Exactly one prominent button per pane;
  deletion is red, not the overdue orange; a button never changes size or
  position in any state. Spec: docs/design/BUTTONS_2026-08.md.
- **Stopping a meeting no longer wipes its outcome** — the thread and the
  hint stay on screen after the recording stops: that is the minute when they
  are read to the end, copied into chats and checked against action items.
  The pane used to be tied to whether recording was running, so Stop instantly
  replaced the conversation with an invitation to ask about the archive, even
  though the text was still in memory. Only the next meeting clears the pane;
  the hint card above the thread fades by its own rules (see the hint card
  paragraph) — the archive question flow moved to the Memory section (#22).
- **A dead channel no longer takes the meeting with it** — when a source stops
  delivering frames, the watchdog tries to recreate its stream, but does so off
  to the side and with a time limit: closing a dead PortAudio stream can hang
  forever, and the whole pipeline used to hang with it — a perfectly healthy
  microphone stopped writing at the same second. Now a hopeless channel is
  dropped, the status says so, and the meeting keeps recording on the rest.
  Channel start-up is per-channel too: one failing source no longer leaves the
  meeting with no recording at all. Incident of Aug 6 — four recordings in a
  row, 31 seconds each.

- **Diagnostics respect privacy** — `doctor` asks privacy for the LLM
  address (honouring both the remote-address ban and the kill switch), and
  the post-meeting hook no longer receives the Anthropic key in its
  environment: cloud goes through the subscription only. Every Claude process
  now asks privacy in the same function that launches it; the AST guard proves
  that the gate controls that exact call, not an unrelated branch in the file.

- **Import folder (watched)** — a repeat of an already-imported meeting is
  a success, not a failure (the file moves to `done/` instead of being
  rescanned every two minutes forever), and meetings renamed by the
  pipeline to `<stamp>_Topic.md` count as repeats too. A recording with no
  speech saves its transcript and finishes immediately — a three-second
  scrap no longer drives the LLM pipeline across the whole backlog.
- **Version number in plain sight** — Mac: at the bottom of the sidebar
  (“Charoite 0.70.1”); iPhone: at the bottom of the recording settings with
  the build number. The iPhone companion rides the same release train as
  the Mac (release-please bumps `app-ios/project.yml`).
- **“External recording” tab** — a phone voice memo, someone else's call
  recording, a Zoom export: drop the file (or pick it) — it is copied into
  the import folder, the original is left alone, and it goes through the
  same pipeline as a live meeting. The processed meeting shows up in the meetings list with a
  status, like a daemon-recorded one (before 0.71 the import wrote no status and the
  meeting lived only on this tab — field report 05.09). The list shows what waits, what was
  built (stamp, a “Transcript” button) and when the copy goes away: a
  processed copy in `done/` lives `audio.import_keep_days` (unset: same as `record_keep_days`)
  and is deleted together with the audio “Исходник” in the meeting archive;
  text sources (txt/vtt) in the archive stay. A failed file gets a
  `.<name>.import-error` marker, stays put, is never deleted and is no
  longer pushed through STT every two minutes — retry with the button (or
  `--scan --retry-failed`). Retention runs after every scan and every six
  hours regardless of the watch toggle; copies that reached `done/` before
  this version get their days from the first sweep that sees them. The
  default equals `audio.record_keep_days`.
  Point the app at a folder (Settings →
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

- **Chinese recognition without Whisper** — `stt.backend: sensevoice` runs
  SenseVoice Small through the same sherpa-onnx that is already installed for
  diarization: no new dependency, 228 MB, and text normalization on, so numbers
  and times arrive as digits rather than spelled out. Whisper stays as the
  multilingual fallback. Install: `scripts/get_models.py --stt sensevoice`;
  measure with `scripts/stt_bench.py --compare` (synthetic speech — a floor,
  not a benchmark).
- **Documents in your language** — `sufler.language: ru|en|zh` switches minutes,
  summary, instant answers and graph node content; hints speak the language of
  your role. The archive summary used to be the exception: it was written in
  Russian whatever the setting, so an English meeting got English minutes and a
  Russian digest on top of them. Reading is separate from writing: new documents
  follow the config, existing ones are parsed in all three languages at once —
  otherwise switching the language would break the archive retroactively, and
  old meetings would stop opening. The meeting card reads all three too: before,
  an English meeting showed empty «Decisions» and «Action items» while the file
  had both.
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
  A "Mine" section comes first (the bold assignee matches `user_name`),
  even for assignments from weeks ago; open items collapse into a
  "Stale" section once they go quiet — no due date and older than 14
  days, or a due date a week or more past (a fresh overdue stays
  visible in its bucket) — the screen gets cleaned, the files stay
  untouched. Due dates carry no year, so the year is taken from the
  meeting date: "by 15.03" said in September means next March, not 170
  days overdue, and "by 20.02" from a February meeting is overdue in
  September, not next year; a date up to a week before the meeting is a
  just-missed deadline brought to the table, not next year. The meeting
  card and Prep read the same date the same way; notes without a
  meeting date in their path keep the older today-based guess. The
  list runs from the latest meeting to the earliest and every group
  carries its date: the order comes from the meeting time in the archive
  name, not the file mtime — the nightly review touches old folders and
  used to float them to the top as if they were fresh.
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

The first live day added two missing wires. Normalization ran only on the
manual Protocol button, while the auto-saved draft — the document people
actually read, since nothing finalizes it automatically — skipped it. And a
bare heading line ("Open questions:") did not close the assignments section,
so the lists after it leaked into the task window as false tasks. Both fixed:
every draft write passes through the same normalization, and a bare heading
line closes the section only when it names a known minutes section (open
questions, risks, decisions…) — a trailing colon alone is not enough, because
inside a live document that shape also belongs to wrapped lines and labels.

Follow-ups from the same day closed the remaining gaps: the third write path —
the MCP Minutes tool — now runs the same normalization before writing (it used
to skip it, so its assignments never reached the task window); the deadline
prettifier only touches the tail of an item after a dash, leaving live wording
like "discuss the deadline: tomorrow" untouched; after a transcript rebuild the
minutes are rebuilt from the final transcript when nobody has touched them (the
daemon — and, after the first rebuild, the rebuild itself — leaves a hash of the
last automated write in live.json; a file that still matches it is treated as
auto-text and is regenerated with the recovered names, the previous version
going to transcripts/.prev; a 13-minute meeting costs about 13 s on the local
model, and the call waits up to ten minutes for a live meeting to end first),
a meeting that never got a draft receives minutes if its speech is long
enough, a final transcript too short for the model keeps the draft, the
sidecar with the hash is found even after the meeting was retitled and also
carries the meeting's exact seconds stamp (`stamp`, written by the daemon at
stop and by the retitle), so a rebuild of a retitled meeting looks for its
recordings by that stamp instead of guessing by the minute and can no longer
pick up a same-minute neighbour's recording when its own are gone; a title
whose sidecar name is already taken by a stranger's orphan is refused by both
the automatic retitle (the file keeps its name, the title goes to the header
only) and `rename_meeting.py` (nothing is renamed, exit code 1), while a twin
that carries the meeting's
own seconds stamp reunites the pair, and an orphan sidecar with the
candidate's stamp counts as evidence of a neighbour when recordings are
resolved by minute, while
hand-edited minutes are only restamped — the draft marker is removed and
"Speaker N" labels are replaced with the recovered names; and when native
system-audio capture fails
to start, the app now says out loud that the meeting is being recorded via
BlackHole instead of falling back silently. Two more hardening steps followed:
live-session names are matched only to neutral rebuild labels (the owner's
own label can no longer inherit somebody else's name in the final
transcript), and the menu-bar icon itself turns into a warning symbol when
the pipeline goes critical — the one place that is always visible.
