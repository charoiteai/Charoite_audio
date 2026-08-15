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
  with the question shown above it. The gate is "anyone but the owner"
  (word-level match against `user_name`), so answers keep firing after a
  counterpart gets recognised by name mid-meeting.
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
  the active field; the clipboard is restored, images included. In the menu
  bar, dictation, note and diary sit in a column with their shortcuts in a
  separate right-hand column: on one row they did not fit and got truncated
  exactly at the shortcut.
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
  not by the clock: silence in the room produces no new lines. **The thread is the only canvas on screen**: answers (⚡ instant, ☁ cloud) and theses (📌 decision, 💭 thought) are woven into it as lines by the daemon instead of living in panes of their own. While those panes were separate, an auto-answer arrived every half a minute, the hint pane never went empty — and the thread, the whole point of the screen, never showed at all. `⌘⇧E` (the ⏮
  toolbar button) expands the current topic from the archive: a graph search
  turns into 2-3 facts from past meetings (decision, status, who owns it)
  appended as ⏮ lines right into the topic that was asked about — the hint
  pane stays free, so a hint can be requested in parallel. **A speaker's name
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
  turned a 30-minute meeting into a 40 KB scrap. A folder
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
- **A recording without speech is called that** — forty seconds of silence
  is not a "processing error" but a result: the status says so, the
  transcript stays openable, and the pipeline will not re-process silence.
  A state from a newer pipeline also survives — it used to break decoding of
  the whole status, and the meeting vanished from the window entirely.
- **The "Recent meetings" window** — twenty meetings from the last two
  weeks: state as a colored dot, "Open" and "Transcript" on every row, a
  "Retry" button on failed ones. A ready meeting's row shows its duration
  (from transcript timecodes, cached — the file is re-read only when the
  meeting was re-processed). While one meeting is being retried, the
  "working" indicator shows only on that row: ready meetings are not
  concerned with someone else's retry. The menu-bar button is visible even
  with an empty history — the window explains its own emptiness; and it is
  called "Recent", not "All": the full archive lives in the archive folder.
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
  same minute are neither picked up nor overwritten). The old topic stays
  in aliases — search by it keeps
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
  started — not when the pipeline finished writing its status.
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
  back-to-back meetings or truncate the newer one's audio.
- **Core Audio tap — a disabled reserve** — the second native route to system
  audio: the app reads the tap with its own IOProc and hands the daemon a PCM
  stream. The scheme was proven in the field (38.9 s recorded), but the very
  cycle of creating and destroying the tap aggregate wedges CoreAudio on
  macOS 26.5: after a meeting the machine's speakers go silent until the
  audio subsystem is restarted. The code stays in the package and is enabled
  by editing one line — in case ScreenCaptureKit is unavailable.
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
  though the text was still in memory. Only the next meeting or an archive
  question clears the pane.
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
  `[x]` straight into markdown — Obsidian and the app always agree. The
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
