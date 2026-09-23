# Architecture

***English** · [Русский](ru/ARCHITECTURE.md) · [中文](zh/ARCHITECTURE.md)*

## Overview

```
mic ───────┐                        ┌─ live transcript (per-voice paragraphs)
           ├─ AudioHub ─ STT ─ daemon ┼─ loops: ⚡ answers · theses · minutes
system audio ┘  (3s chunks)         │   · déjà vu · names · dialogue markup
(ScreenCaptureKit; BlackHole is the fallback)
                                    └─ NDJSON stdout ←→ stdin commands (UI)

Stop → recording rebuild → graph update → archive + Summary → [Claude debrief]
```

Everything runs on your machine; network calls go to localhost only
(Ollama). The cloud layer is a separate, off-by-default option.

## The daemon (src/daemon.py)

One process, thread loops around a shared locked `Transcript`: STT loop,
instant answers, auto-theses/hints, live minutes, déjà vu over Cores, name
resolution, dialogue markup, the cloud loop, heartbeat. Events stream to
stdout as line-JSON (`{"type": "transcript"|"thesis"|"hint"|…}`); commands
arrive on stdin (`hint`, `ask <question>`, `summary`, `stop`). Any UI can
sit on top of this protocol; a second instance is blocked via flock.
`Transcript` lives in dependency-light `src/transcript.py`; runtime modules never import `main.py`.

Daemon statuses carry a failure flag (`{"type": "status", "error": true}`):
the app renders those as errors and a plain status clears the flag. A model
failure never becomes hint text — only a status — so the last good hint
stays on screen.

### Channel labels and the owner — one source (src/channel_labels.py)

Who speaks into the microphone and who owns the meeting is answered by one
`ChannelLabels` object built once from the config: the raw channel label
(`mic_raw`, «Я» or the name unless it collides with the neutral label — the
`mic_label_for` rule is shared with `AudioHub`), the owner's signature
(`mic_signed`: the name, «Я» for an empty name, empty on a collision) and the name for the
word match. Before phase 1 these were three sources that diverged: with the
name «Собеседник 2» a microphone chunk counted as the microphone in the
speech counters but as a stranger in the signature (audit 30.08). Party D-П2.

### The live meeting outranks the background (src/live_gate.py)

There is one local model and several claimants: rebuilding an earlier
recording, graph extraction, the nightly cycle — and the live meeting.
Ollama with the MLX runner answers `503` within a quarter of a second on a
busy model instead of queueing; without a guard, rebuilding an 18-hour
recording (18.08) left a meeting without hints for 45 minutes. Three rules:

- **"A meeting is on" = the daemon lock** `logs/daemon.lock`: the daemon
  lives exactly as long as the recording. Background work (`graph_updater`,
  `rebuild_transcript`, nightly `wait_for_idle`/dossiers) tests it with a
  non-blocking `flock` and waits while it is held — between chunks of a
  long extraction too. The rebuild waits as long as needed (as a whole,
  STT and diarization included); the night waits with a cap (a morning
  meeting must not eat the night). Rebuilds run one at a time per machine
  (`logs/rebuild.lock`): an orphan released by the gate and the fresh
  recording after "Stop" never start together. "A meeting is on" means only
  an honest `flock` refusal caused by someone else's lock; a missing file,
  missing permissions or a volume without `flock` never stall the background.
- **Busy ≠ dead.** `llm.stream`/`complete` retry `503/429` with growing
  pauses within the caller's budget (live loops up to 30 s, graph extraction
  up to 10 min); `llm_health.probe` distinguishes `BUSY` and never restarts
  the server under someone else's generation.
- **Our own generation in flight is a fact, not a guess.** For the duration of
  every request to the local model the transport (`llm._open_stream`,
  `llm._post_busy`) holds a lease file `data/llm_inflight/<pid>-<id>.json`
  under `flock` (`model_lease`): the kernel releases it the moment the
  process dies, so no pid or mtime heuristics. One file per call, not per
  process: the daemon runs several generations from one pid. Liveness has a
  single criterion, `deadline` = the last moment the work was known alive
  plus a stall threshold derived from the read timeout of that same request
  (the transport itself aborts silence longer than its timeout; the threshold
  sits just above it, otherwise a live document prefill would count as a
  hang); for streams a data line rolls the deadline, `: keepalive` comments
  do not. The file protocol belongs to the writer: a lease is never visible
  without its lock and is never rewritten in place — every publication is a
  fresh locked inode renamed over the name; the reader judges liveness by the
  lock alone (locked but unreadable means alive) and never deletes orphans
  (the next writer sweeps them). Before restarting, `llm_health` reads the
  leases for this server: a live, non-stalled one means the server is busy
  with our work and the caller queues behind it; a stalled or absent one
  means restart as before; an unreadable sensor is logged once — restart
  proceeds as before if the sensor never worked, and is held until a manual
  restart if it worked and then broke. The guard lives inside `_restart*`
  itself together with the ban on touching a non-loopback address, so every
  path to a kill inherits both; the manual emergency restart over live leases
  is `scripts/doctor.py --restart-llm`, and success means the probe answered. Leases on the cloud gateway never hold a
  local restart (matched by server address); waiting in the busy queue holds
  no lease — that queue heals itself.
- **An error inside a stream is an error.** An `{"error": …}` line inside a
  200 response, or a stream that ends without its terminator, raises: a
  truncated set of minutes is never passed off as complete.

### The hint outranks the thread (inside the daemon)

Since 30.08 the dialogue markup walks under the arbiter too: it used to be
the only loop taking the model past the lock — 900 tokens every six seconds
while the hint and ⚡ queued behind it; now it takes the lock quietly for a
second, lets the paragraph wait otherwise, and a manual question interrupts
the markup even mid-stream. Dropped frames of the fast
trigger are counted and reported once a minute as a status error instead
of vanishing silently.

Outside, processes compete for the model; inside the daemon, its own
loops do. Incident of 26.08: the auto-theses loop (the "thread") ran the
main 35b model in the live profile and held the Ollama connection —
hints starved from within, with an empty gate and a live meeting.
Rules after the post-mortem:

- **Background never takes the big model.** The thread runs strictly on
  `llm.small`, with no retries on the main model: two generations on one
  GPU choke the hint engine. If `llm.small_model` is not configured, the
  daemon says so with a line in the err log at startup — a silent
  fallback to the main model is forbidden.
- **A near-zero-wait slot.** The thread takes the hint slot with a 1 s
  timeout: if busy, it skips the beat instead of queueing ahead of a
  hint. After four consecutive misses it makes one attempt with a 20 s
  wait (a burst), so the thread does not starve forever on a dense
  meeting; a failed burst counts as a regular miss.
- **No dead loops.** deep_loop is removed: the app has been sending
  `set theses off quiet` since #394, so the loop never ran in a live
  session — code that "seems to do something" is worse than none.

### What repaints in the app

The local model and the recording share the machine with the interface
itself, so the UI has its own resource contract. Measured on 24.08: the
app held 37% CPU on average over 4.5 days of uptime — without a single
frame of animation, purely from redundant repaints. Two rules came out
of that post-mortem:

- **A ticking value lives in its own view.** The recording clock's
  second is `RecordingClock` (a `TimelineView` driven by the start
  date), not an `@Published` in the service: publishing once per second
  redrew the whole day screen with its ten subscribers for the entire
  meeting. Duration is derived from the start date, so a sleeping
  laptop does not eat it, and an invisible view does not tick at all.
- **A publish gate compares what the consumer reads.** The two-second
  processing-status poll published identical values and woke
  subscribers for nothing — an equality gate is mandatory. But
  comparing the raw snapshot is not enough: "processing" turns into
  "error" by the CLOCK (thirty minutes of silence) while the file stays
  untouched. The gate compares the resolved state — otherwise a hung
  pipeline silently stays an eternal spinner with no Retry button (both
  cases came from DeepSeek rounds on #433: first on the meeting card,
  then on the error badge).

### The cloud layer: what each loop pays

Four loops call the cloud through the headless CLI: the in-conversation
answer, the post-meeting review, the nightly dossier revision and the
nightly cores revision. Their shared rules live in `src/cloud.py` (model
per step, call isolation, proxy); permission comes from `src/privacy.py`.
An audit on 26.08 (two independent reviewers) confirmed the frame and
closed four seams:

- **A CLI error is not an answer.** The live loop took stdout and, when
  empty, substituted stderr: "Unknown model" and "403" reached the
  meeting canvas and the audit trail as cloud answers. The return code is
  now checked, a failure goes to the status as an error and never into
  the audit.
- **The worker no longer dies silently.** A missing `claude` raised
  ENOENT past `finally`: the graph snapshot was orphaned, the log stopped
  after its first line, and the meeting simply never got a review. That
  is now an ordinary failure path with the reason written down.
- **One lock for everyone who writes the graph.** `cloud.lock` was taken
  only by the meeting review; the nightly dossier revision edited the
  same files without it, and the neighbour's boundary check quarantined
  its edits while the run reported "✓ applied". The lock moved to
  `src/file_locks.py` and is shared.
- **Symlinks are denied for reading.** A symlink's target lives outside
  the graph, and `Read(/**)` covers it lexically. A live run showed the
  CLI resolves the path itself and refuses to read outward — but the
  read-only mode had no deny rules at all, so the boundary rested solely
  on an external program's behaviour.

### What the night promises the morning

The nightly run (`scripts/nightly.sh`, 04:15) grooms the graph while nobody
is at the machine: brief, cores revision, dossiers, cloud revisions, file
dedup, memory bench. An audit on 26.08 (two independent reviewers) checked
the zone's promises and closed the gaps:

- **The night ends at night.** The `CHAROITE_NIGHTLY_UNTIL` ceiling is now
  visible to the tail steps too — dedup and the memory bench ran past it and
  woke the model in the morning. Waiting for a live meeting is capped by
  what is left of the night: a flat hour of waiting used to stretch the run
  past the ceiling.
- **A live meeting outranks the night — on the heaviest step too.** Judging
  core pairs holds the embedder and the NLI model; the gate existed only for
  dossiers and cloud revisions, so the cores revision kept sharing the model
  with the live prompter. It now yields like the rest.
- **The status lands where it is looked for.** The data root is normalised
  once at the start: a «~» or trailing spaces in `CHAROITE_ROOT` sent the
  status and the idle wait into a literal directory while the python layer
  looked at the real one — the app saw no night at all.
  There is deliberately no guard against a second MANUAL run: both attempts to
  build one (a pid directory and `flock` through the system python3) produced a
  Critical on their first review round, and the cost of a rare double start is
  lower than a brittle watchman on the night's very first line.
- **A signal actually stops it.** The `trap` had no `exit`, so the run
  continued after `kill -TERM` and the "interrupted" state was overwritten by
  the final "ok" — that state was unreachable at all.
- **"Ok" only when work happened.** A night with no graph at all, and a cores
  revision that ran empty (no NLI model, Ollama down), no longer report green:
  the revision has its own exit code 2, a broken cloud CLI exits 3 (the night is marked failed in the status, rc untouched: a CLI update under our feet, audit 05.09), missing graphs are marked in the status.
- **The cores revision answers for every graph and names its reason.** The
  step succeeds only when every graph was judged or had nothing to judge (a
  single core is not a failure): one passing graph used to be enough, and the
  main graph went unrevised for a month without anyone seeing it (23.09).
  Exit code 2 means some graph could not be judged, and the log says why — an
  embedder refusal with the response code and body, a judge refusal, an NLI
  model that did not come up — instead of the generic "no NLI model or Ollama
  is down". Exit code 4 means the night ceiling cut the revision short; the
  status shows «ревизия-ядер(поздно)», and the unjudged cores and the pairs
  already judged are stored next to the stamp, so the next night continues
  where this one stopped instead of re-judging the same top pairs. Batches for the embedder are cut by the
  `llm.embed` door (at most 64 texts and 64,000 characters per request):
  Ollama dropped the connection on 808 cores in one request. The list of
  unjudged cores is kept whole.
- **The night is tested with the interpreter launchd uses.** The agent runs
  `/bin/bash`, which is 3.2 on macOS, where expanding an empty array under
  `set -u` kills the script: a clean night was recorded as failed. Tests ran
  the Homebrew bash 5.x from PATH and never saw it; `test_nightly_exit` now
  runs `/bin/bash`.
- **A person's edit does not vanish.** A core changed during the long pair
  judging is not overwritten from the in-memory snapshot — the pair waits for
  the next night. A duplicate's handwritten "Суть" moves into the canonical
  core instead of living only in a rotating backup. Dedup re-checks the digest
  before substituting: a file rewritten by the pipeline between the scan and
  the link is skipped.

Sleep is not a failure: the night budget runs on wall-clock time, which sleep
does not stop, while `monotonic` stands still — the difference lands in
`nightly.json` as `slept_s`, and a night that lost only «(late)» steps to ten
or more minutes of sleep ends as `slept`, not `failed` (30.08: a run from
04:20 to 09:59 on a sleeping laptop). A full night needs an awake machine —
that is the owner's power setting; `caffeinate` on battery with the lid closed
does not help.

### Cloud chat (llm.engine: cloud)

A third engine next to Ollama and mlx-server: hints, theses, the thread and
minutes are computed by an external OpenAI-compatible gateway, and the laptop
stops holding a large model. The transport is the same as mlx-server's
(`/v1/chat/completions`, SSE) — only an authorization header was added, so the
code path is shared.

- **Two keys turn it on.** `llm.engine: cloud` says where, `sufler.cloud_engine:
  true` grants permission. Either alone is not enough — an address without
  permission and permission without an address both leave the local model in
  charge — and `CHAROITE_NO_CLOUD` overrides both. The other cloud toggles work
  the same way; this one differs in volume: the whole stream of the conversation
  leaves the machine, not an occasional slice.
- **The key never lives in the config.** `llm.cloud_key_file` (default
  `~/.config/charoite/llm_key`, mode 600): config.yaml ends up in backups and
  screenshots, and a key is money and access. It is never put in the request
  body, never printed to logs or errors, and the address is https-only.
- **A safety net for a dropped network.** A network error, a 5xx or a broken
  gateway response (non-JSON, HTML instead of a stream) before the first emitted
  token falls back to the local model with a line in the err log; 401/403/400
  raise loudly instead — otherwise you work locally for a month without knowing.
  After the first token there is no fallback: a hint started by the cloud and
  finished locally would splice two different thoughts. Non-streaming calls (the
  post-meeting pass, minutes, archive summaries) fall back the same way — their
  answer is atomic, so there is nothing to splice. The net lands on whichever
  local engine the person actually has: `cloud_fallback_engine`, defaulting to
  mlx-server when `mlx_model` is set and to Ollama otherwise. The cloud model
  name is not forwarded to it: the local engine has its own from the config,
  and a foreign one would return a 404.
- **The health probe knows about the cloud.** `llm_health` does not treat the
  gateway as a local server: there is nothing to restart, and a failed probe no
  longer blocks the post-meeting pass — the call itself has retries and a net.
  Otherwise, on a cloud install (where the local Ollama only holds bge-m3) the
  restart would kill the embedder and no meeting would ever reach the graph.
  Diagnostics, the probe and the client all ask the same pair of keys: an
  address without permission means local work, and the doctor must fix the
  local model rather than suggest checking a gateway.
- **A silent gateway is told apart from a thinking one.** The
  first-token deadline lives inside the stream parser: it cannot be checked
  from outside, because keepalive lines never return control and the socket
  timeout resets on every byte. Total silence is cut off in half a minute,
  while a keepalive stream is tolerated four times longer — a gateway sending
  signs of life is usually thinking over a long prompt, and cutting it off
  would silently swap the model for the local one.
- **The key never reaches the screen.** A 401 body echoes the key back, and
  error bodies travel into the hint card, the transcript file and MCP replies —
  so the key is stripped where a body becomes an exception.
- **What stays on the machine with any engine:** speech recognition,
  diarization, search and dedup embeddings, NLI. A chat gateway has no cloud
  equivalent for these, and "fully in the cloud" is a different conversation —
  one about audio.

## Diarization: two passes

1. **Live**: each chunk is embedded (ERes2Net, 512-dim) → a voice tracker
   with hysteresis (0.45 threshold, a grey zone, a relative switch rule, new
   voices confirmed by two agreeing chunks). Embeddings live in RAM only.
2. **Offline after stop** (src/rebuild_transcript.py): the full recording is
   re-diarized per channel; speaker echo in the mic is cut by overlap,
   voices shorter than 10 s merge into neighbours, segments are
   re-transcribed, names are assigned by the LLM (the owner = the longest
   voice on their own mic). The live version is kept as a draft.

## Post-meeting pipeline (src/graph_updater.py)

**A derivative has a passport.** Minutes, debrief and theses derive from the
transcript, and each keeps a pair of keys in the `.md.live.json` sidecar: the
bytes of the last machine write (`<kind>_sha256`) and the hash of the source
(`<kind>_source_sha256`) — speech plus the incomplete-recording note
(`meeting_source.MeetingSource`, see the №316/№317 paragraph below). Speech
excludes the title and the "Co-thinking" tail (`transcript.speech_of`): a
retitle rewrites the first line, and a hash with the title made the minutes
"built from other speech" on the very first retitle. Freshness is decided in one place,
`live_sidecar.derivative_state`: no file — build; machine-owned and same
speech — do not call the model; machine-owned and speech changed — rebuild
(previous version in `.prev/`); bytes not machine — edited by a human, leave
alone; no passport — leave alone too: that is absence of knowledge, not
knowledge of a human, and the old corpus (232 of 302 meetings without a
sidecar) is not locked as "human"; passports are issued from now on. Minutes
are built by one pipeline on every path (`finalize_minutes` +
`record_minutes_passport`); the debrief checks ownership before calling the
model and writes under a "file unchanged under our hands" gate. The debrief
file name follows one rule derived from the transcript stem
(`meeting_stamp.derivative_path`), not from the title the model produced in
this run: a second formula left two debriefs per meeting for 69 of ~300 in
the live corpus, and the archive took whichever sorted last. The "when to
build" policies are named in one place (`live_sidecar.POLICY_LIVE` /
`POLICY_RETRO`): the live path after a meeting refreshes the debrief always,
except when edited by a human — its source is speech plus graph; the retro
sweep builds only what is ours and stale, there is no mass backfill. A read
error is ignorance (UNKNOWN), not "edited by a human": otherwise a transient
permission failure would stop the live path forever. For a meeting with live
co-thinking the archive builds the theses from the `> HH:MM 📌 …` lines and
the model is not paid for them; model-made retro theses exist only for
meetings without the live loop (import, recovery). Previous versions of every
derivative live in `.prev/` next to the transcript, not in the graph. The
mtime criterion was rejected by measurement: minutes are older than the
transcript by time for 204 of 302 meetings — time is moved by retitle,
co-thinking and revision, not by speech. The import tail runs the retro pass
for its own transcript only and addresses it by its final name
(`find_final_transcript`: graph_updater has already renamed the file after
its title by then); the full sweep is manual, one line per meeting: what was
built, what was skipped and why.

1. The LLM extracts JSON from the transcript: title (2-3 words),
   participants, topics, decisions, action items, entities, Cores.
2. Graph update: a meeting note with `[[Folder/Name|Name]]` links, upserts
   of People/Systems nodes (dated facts, history never erased), Cores —
   "Status" is rewritten, "Chronicle" accumulates. Every chronicle line
   carries provenance: who said it, at what time, verbatim quote. The quote
   is verified against the transcript: exact word-level match first; if the
   model paraphrased, a fuzzy search finds the closest transcript window
   (difflib, 0.75 threshold) and the graph gets a slice of the TRANSCRIPT
   itself, never the model's wording; anything below the threshold is
   dropped as fabrication.
3. Archive (src/meeting_archive.py): a "date — title" folder, human file
   names, Q&A assembled from the hints log, the Summary generated with
   historical context (Cores + two previous summaries; the future never
   leaks into the past — cut off by meeting date).
4. Optionally the cloud Claude cross-checks minutes against the transcript
   and enriches the graph with links visible only from history. What the
   review refutes does not live on: withdrawn action items move from the
   minutes' tasks into "Withdrawn by the review" with the reason, wrong
   decisions in the note are marked ⛔ in place, and Charoite's memory of the
   meeting is resent without them (facts used to reach memory before the
   review, and the review never caught up with them). A speaker label the
   rebuild named after the wrong person (strict "Name fixes" section:
   label → name with the grounds) is restamped in the transcript's block
   headers and participants line and in the minutes' participants line,
   with the previous versions in `.prev/` — only in edit mode with a
   verified transfer, where the cloud also moves the meeting out of the
   wrong person's node; in read-only mode nothing is renamed and the log
   points to the review's section for a human.

Each phase is published atomically under `logs/meeting-status/`: the macOS
app shows real progress, the cloud review stage (running, retrying, ok,
failed) lives in the same status as the `review` field without moving
readiness, keeps failures linked to the source transcript, and
announces readiness only after the exact meeting note exists. The status file
is named by the stamp of the ORIGINAL transcript, and that key is stored in
the status itself: a new process (the graph step, a retry by the retitled
path) finds the record of the same meeting by where its path resolves today
and reuses the key; records with a dead path yield to live ones. The key
used to be the file stem: a failure after retitle wrote «error» under the
old stem while the retry ran under the new one, and the stale status kept
the meeting in the retry queue forever, rebuilding it from the recordings
every time (audit 30.08). Before overwriting a transcript the
rebuild keeps its current version in `transcripts/.prev/<name>` (one
generation): the live draft `_live.md` is written once, and manual edits
between two rebuilds used to vanish. Meeting notes, archive files and
statuses go through `safe_write` (tmp + replace), so an interrupted write
never leaves an empty file. Resolving the final transcript from the
original name takes two passes: real candidates under both stamps first
(seconds, then minute — a seconds-stamped recording is retitled under its
minute), and only then the `_live`-copy rescue; a neighbour meeting's
seconds stamp within the same minute is neither a candidate nor a copy
source. Otherwise the copy rescued on the seconds pass would shadow the
minute-stamped final, and a completed meeting was marked as error (first
live meeting, 31.08).

## The knowledge graph (an Obsidian folder)

```
<graph_dir>/
  Meetings/…       ← episodes (raw material, never lost)
  People/ Systems/ ← entities with backlinks
  Cores/           ← cross-meeting topics: Status + Chronicle
  Notes/           ← voice notes
  Meeting-archive/ ← the reading layer (Finder-friendly)
  _MOC.md          ← the map of content
```

This is the three-layer "episodes → entities → communities" scheme (as in
Graphiti/Zep) on plain markdown: grep, Obsidian, git and any editor just
work. Superseded facts are dated, not deleted.

**Picking the graph.** Every sphere of life gets its own graph next to the
others; the «проект» field from the extraction decides where a meeting lands.
The model does not choose blind: the prompt carries the list of existing
graphs (sibling folders holding a `_MOC.md`) and names the work default
explicitly, and the answer is matched against known names ignoring case and
separators — «Project Alpha» and «Project_Alpha» are one graph, not two. A new
graph is created only for a clearly non-work topic; on a work meeting that is
a mis-pick, so the log records which graphs were known at the time.

**Graph hygiene as memory (memory audit, 07.09).** One resolver,
`src/graph_links.py`, decides whether a `[[link]]` is alive for the
nightly doctor, the cloud review and clean-ups alike: full path, note
name, an `aliases:` entry from the node header (as Obsidian does), or an
attachment on disk. `scripts/graph_doctor.py` reports broken links
separately for active folders and for `Встречи-архив`, which the
pipeline never re-reads; the warning threshold applies to active links
only, and near-duplicates are also found across word order («Иван
Петров» / «Петров Иван»). The pipeline folds word order for people,
resolves the instrumental case («с Сашей») the way it resolves the
vocative, strips a diarization label glued to a real name («Саша
(Speaker 1)») and follows a redirect stub to its canon instead of
appending a meeting into the stub. When the cloud review's edits are
carried from the sandbox into the graph, every link target is checked
against the live graph and the nodes this run created: a link to a node
that exists in neither becomes plain text, the log names the file and the
targets, and the removed targets accumulate in `logs/graph_unlinked.log`
as candidates for a node or an alias — before this, such links landed in
the graph broken.
Dead links are stripped AFTER every write of the run, from the graph as it
now stands: a target is alive if the file is there. An edit's fate cannot be
predicted — a write fails after the decision — so the «will this node land»
probe is gone, and a failure of the stripping pass goes into the verdict as
«dead links remain». The cost: a file that lost a link is written twice, and
the window between the writes is what the doctor catches.
An entity whose name matches several nodes, or differs from an existing
node by one character, does not become a node: the name stays as text in
the meeting note and the candidates go to `_Кандидаты.md` at the graph
root for a human. Holding a node for one meeting is right; holding it at
every meeting until someone opens that list means losing the entity as
memory — in three days one system was held six times and nobody looked.
So the same held pair «name → candidates» in a SECOND meeting is counted
on its line, and a typo with a single candidate gets an `aliases:` entry
in that node automatically — the meeting lands in the node right away,
with a «by repeat» trace in its «## Встречи» and in the log. Merging without
a human is allowed only on a shared number in the name («Kwen 32B» → «Qwen
32B»); a name without digits («Препрод» → «Препрот», «Реестр Витрен» →
«Реестр Витрин») only gets the counter. Undoing a wrong merge means removing
the alias from the front matter and the meeting lines from the node — the
trace is there for that. The machine leaves its own trace next to the
alias — `auto_aliases:` in the node's front matter — and reads the veto
from there, not from the journal: a name that is in `auto_aliases:` but no
longer in `aliases:` was removed by a human, and the machine never merges
that pair again (the journal line only notes «псевдоним снимал человек»).
Remove the name from `auto_aliases:` too, and the machine may merge again —
at the very next meeting, since the repeats are already counted in the
journal. Both list fields travel with the node when graphs are merged or a
tier3 duplicate is folded into its canon (`frontmatter.carry_list_fields`),
so a veto survives a move. `_Кандидаты.md` is a report and an event log: repeats are
counted by its lines («the same pair» is name, reason and the same set of
candidates), but no state hides in their tails — a state kept as a
substring of a human-edited file was lost on cleanup and lied on a zero
write. A resolved path is written only through the update gate
(`rewrite_file`): a node that vanished or changed under our hands between
the verdict and the write is a log event with a reason value, not a new
node with the parser's type and not an overwrite.
A real ambiguity (several candidates) only gets the counter: the machine
must not merge it.
A topic that already lives as a node of another kind (`Системы/X`) and is
then named a core gets a parallel `Ядра/X` — the two cannot be merged, their
structures differ — and from now on both carry a «see also» line under
`## Связи` pointing at each other, written once and idempotent on retry, so
a later merge by the reviewer or by the entity verdict has a visible cause
instead of a mystery in `canon_link`.
Why a write did not happen is a VALUE, not a text: «changed under our hands»,
«the file is gone» and «could not reach the file» are three kinds carried by
the signal itself, with the system's own detail in a separate field rather than
glued onto the end of a string. Each case demands something different: a
vanished file has nothing to fix and nothing to be blamed for, an unreachable
one stayed as it was and must be reported out loud, and a lost race means
someone else wrote over it. The reason text is for a human reading the log and
changed twice within one review round — matching against it from another module
would break silently. One place builds the blame phrase for BOTH bridge calls:
written out by hand in each handler, it drifted apart between neighbouring
calls into the very same bridge within a single commit. Every other site prints
the reason text as it is and invents no culprit.
Text on its way into the graph is read STRICTLY and must not bring
unreadable characters with it: a sandbox file that does not decode as UTF-8
goes to quarantine with its own verdict line, and so does an edit that adds
«�» to a line the node did not have — a node where such a character
already lived can still be edited. The same on the bridge side: the minutes
are rewritten after a strict read, an item or a withdrawal reason carrying
the character never reaches them, a name carrying it never reaches speaker
headings or a People node, and a revision that is itself not UTF-8 is not
copied into the graph (the meeting is still archived). Before this the
transfer read the file with replacement and wrote the result back, so a
single truncated byte stayed in the node forever.
When a duplicate node is turned into a redirect stub, the body it displaces
is copied next to the run quarantine but outside its rotation
(`cloud_quarantine/вытеснено/<run>/`, the last 100 runs are kept), and the
stub lands only if its canon — together with the text of the stub itself —
holds the duplicate's facts: at least a third of its content lines and no
fewer than two; headings and one-word lines such as «Решено» do not count,
a canon edited in the same run is measured the same way, and a node that is
already a stub has nothing to lose. Until 13.09 the old body survived only
in one run's snapshot. A worker that takes the graph lock after a neighbour
(or gives up waiting for it) does not start a second paid pass when the
review file changed while it waited, is not older than the transcript and
the neighbour closed the review stage with «ok» (`--force` overrides).

## Dossiers: a floor between search and the graph

Asked "so where does this topic stand", search returns a dozen scattered
fragments and the model reassembles the answer from scratch every time. A
dossier is that answer already written: current state, chronology, decisions,
open questions, who is involved — every point linked to its source node.
Search consults the dossier index **first** and only goes into the graph for
details.

**How it is built.** A cluster is a core plus its 1-hop neighbourhood along
`[[backlinks]]`: adjacent cores, meetings, documents. Topic boundaries come
from the links a human already drew; no graph clustering algorithm is needed.
At night a local model writes a five-section summary per cluster.

**Incremental.** Each dossier carries a fingerprint of its composition — the
source list and their modification times. Unchanged fingerprint means the
topic did not move, so the model is not called. On a typical night a handful
of topics out of dozens get rebuilt. A weekly full pass (`--full`) is still
useful: incremental updates gradually blur cluster boundaries.

**Index lookup** (`Dossiers/_index.json`) is lexical, over word stems with
prefix matching, so `qwen` finds `qwen3-32b`. No embeddings and no running
Ollama required; semantics is layered on top. The dossier body in an answer
comes from the search index snapshot, not from disk: a summary the walk has
not seen yet stays out of the answer until the next walk.

**Derived is not on a par with primary.** Every document in the search index
has a role derived from its path once, at read time: primary (meeting, node,
document), dossier (the top-level summaries folder) or service (pointer,
candidates, report — a file with a service prefix in any folder). Consumers
read the role, not the path string. Only primaries cast incoming-link votes
for the hub boost: a summary retells the very nodes it would vote for, and the
boost went to whoever it happened to mention (16 % of votes in the working
graph). A dossier stays a link target and the «📁» section, but takes no slot
in "Found in the graph" and no hop from a node — not even through a redirect
stub whose arrow points at a summary: the stub resolver never accepts a
derived document as a replacement. The generation publishes ready slices (the
primary documents, a dossier map keyed by normalised key) — consumers take
their slice instead of filtering the common list; vectors are computed for
primaries only. Service files stay out of the index but inside the answer's
coverage, and the coverage words come from one formatter shared by the facade,
the memory block header and the thread status: "searched without: … service
files outside the index: N". Vote statistics by role are a property of the
snapshot (`memory_bench --stats`).

**An optional cloud pass.** The local model retells faithfully but misses
links: that one decision supersedes another, that a deadline has expired,
that two nodes disagree. Opus sees those. With
`sufler.cloud_edit_graph: true` it edits dossiers itself at night; off (the
default) it writes a report and a human applies the fixes. Transcripts,
minutes and the "## Author edits" section are never touched; every edit is
backed up first.

From 2025-2026 practice this takes: the community-summaries idea (GraphRAG),
incremental update without a full rebuild and dual-level retrieval
(LightRAG), and event-driven invalidation rather than scheduled (Graphiti).
A recursive abstraction tree (RAPTOR) proved unnecessary — the hierarchy is
already expressed by links.

## Why these models

Benchmarks and sources — [MODELS.md](MODELS.md). Key points: the main model
stays in the 30B class (the floor for graph extraction), the light model
lives in RAM alongside it, `num_ctx` is always explicit.

## Memory model in one page

- **Files are the source of truth.** No graph DB or vector store as the
  primary carrier: plain Markdown the user owns. Every chronicle fact
  carries provenance (who, when, verbatim transcript quote).
- **One LLM gateway — src/llm.py.** Every chat and embedding call in the
  python pipeline goes through it; no module speaks the wire format itself.
  The model always comes from the config: the 14.08 audit found four modules
  still calling a hardcoded model long after the config had moved on. The
  gateway speaks two engines, picked by `llm.engine`: `ollama` (the default)
  and `mlx-server` — the OpenAI-compatible `mlx_lm.server`, whose prefix
  cache turns the live thread's prefill from ~30 s into ~0.3 s on a long
  meeting (measured 2026-08-14). Embeddings stay on Ollama under either
  engine (mlx_lm.server serves none). Measured head-to-head on 2026-08-15:
  the new transport kept anchors high but reproduced a JSON-chunk failure
  on the long meeting (no strict JSON mode there) and won no time for the
  live thread — its prompt is small by construction — so the production
  default stays `ollama`; the engine remains a config option for
  long-document Q&A (details in MODELS.md).
- **One embedder — bge-m3** (Ollama): semantic search and the core-revision
  prefilter. There is deliberately no second embedding model.
- **Precision — local NLI** (src/nli.py, ONNX): thesis dedup and the
  core-revision judge. Only in latency-tolerant loops; live hints and
  déjà vu run on cheap stemming.
- **Cloud — opt-in post-meeting enrichment only**, via subscription (no API
  key in the environment). Meeting data never leaves the machine by
  default; no cloud memory SaaS, none planned.
- **MCP server** (src/mcp_server.py) exposes the archive and the live
  meeting as Claude Code tools. It supports both branches of the `mcp`
  package: 2.0 moved the class (`mcp.server.fastmcp.FastMCP` →
  `mcp.server.MCPServer`) and pyproject allows either — an install must
  not silently produce a server that dies on import.

## Surviving a crash

The daemon is a child process of the app, so it dies in ways a stop button
never covers: the app is relaunched, the watchdog fires, the OS kills it under
memory pressure. Three mechanisms keep a meeting from disappearing with it.

**One timestamp per process.** The transcript filename and the raw-audio
filename come from a single stamp with seconds, and neither is ever written
over an existing file. Recording sinks open with `"xb"`, so a collision is a
visible error instead of a silent truncation — the auto-restart fires two
seconds after a crash, i.e. almost always inside the same minute.

**Explicit handover of the recording.** On a normal stop the daemon converts
`.pcm` → `.wav` through a `.part` file and publishes the result with an atomic
rename. `rebuild_transcript` waits while a `.part` exists and only converts the
`.pcm` itself when the daemon's lock is free. Age of the file decides nothing:
a three-hour meeting is 345 MB per channel, and its mtime freezes at `stop()`
long before the conversion finishes.

**Catch-up on start.** Any `.pcm` that still has a transcript beside it and
does not belong to the current meeting gets its rebuild launched when the
daemon starts — before retention runs, so cleanup never removes the only copy
of a meeting nobody has processed yet.

A broken stdout pipe (the app quit or restarted) sets the same stop event the
UI would: the daemon finishes normally with graph and minutes written, instead
of losing the STT thread silently while heartbeats keep the watchdog calm.

**The audio consumer thread lives as long as the recording does.** `AudioHub`
has one loop — a `_tick` pass: each channel's block to the file and the STT
buffer, then the channel watchdog; the guard sits on each unit of work and the
watchdog runs last and always — a bad block on one channel neither starves its
neighbours nor cancels the watchdog. The watchdog used to sit bare in the
loop, and any exception in its pass killed the thread: the rest of the meeting
was not written, and the app saw it only as "audio input stalled" up to 100 s
later, answering with a restart of the whole meeting. A failure is not
silent: a stderr line and a status to the owner, at most once per 30 s per
exception type; no channel loss is declared — the channels are alive, the hub
failed. The health snapshot carries `pump_alive` and `pump_failures` (into
`stt_progress` through a JSON gate and into `hb`); the app has no reader for
them yet — they are diagnostics, and an app reaction without restarting the
recording is a separate card. The `AudioHub` constructor does no I/O:
`discover_captures` finds the
devices, the production path is `AudioHub.for_meeting`; tests call the real
constructor, so production code no longer defends against its own test
harness (input round DS and GLM on №311).

**A lost channel leaves a trace in the meeting documents, not only in the
status.** Before №234 the event "the counterpart channel (or the microphone)
stopped writing" lived as one `on_status` string: sticky status, notification,
`capture.log`; the channel coming back left no trace in any file, and a reader
of the transcript a day later saw "the counterpart went silent at 14:32"
indistinguishable from a pause in the conversation. Measured over 14–18.09: 12
of 36 recordings had system-audio losses, up to 21 "lost → watchdog restarted
→ lost" cycles per meeting. Now the hub has one point where a channel state
transition becomes a structured event (`ChannelEvent`: lost / back / gap on a
quiet restart / never returned before the end; the true boundary is the
channel's last frame, not the moment of the cry, which lags by the watchdog
threshold and the restarts; a machine cause class instead of substring
parsing). The trace has one owner — the daemon (`channel_trace`): the event
goes to the sidecar (key `channel_events`, merged — the stop-time sidecar dump
no longer overwrites the whole file), as a line into the transcript's
"Co-thinking" tail (not speech: derivative hashes do not move, the rebuild
carries the tail over, the archive does not turn the line into a thesis) and
as a `system` thread line without dedup and without cloud editing. There is no
duration threshold — an episode that looks short in the report is a real hole;
coalescing is by count: after six lines per channel only the stop summary goes
into the documents ("recording incomplete: no system audio 14:32–14:35,
14:50–until the end"). The string status contract was closed in №310, below.

**Stickiness is set by the writer; layers in the app are owned (№310).** The
daemon classified hub status lines by substring against three and two Russian
markers (`is_sticky_status`, `is_sticky_clear`, `is_recording_failure`): a
reworded line or a fourth marker changed stickiness silently, and the channel
"back" notice left the hub through a second door, `_emit`, where no marking
would have applied at all. Now a hub status is `stt_runtime.Status`, a `str`
subclass with `sticky` (True sets a layer, False clears it, None is an
ordinary status), `error` and `topic`; the fields are set where the line is
born (`_announce_losses`, `_announce_back`, the four disk failures), a single
door `_say` leads to `on_status`, and one function of the contract module,
`status_event`, builds the JSON: `error` always, `sticky` and `topic` only when
set. No substring classifiers remain in `src/`. In the app, instead of one
`String?` shared by three writers, `stickyLayers` are keyed by topic:
`channel_loss` (daemon), `capture` (the microphone did not join the
ScreenCaptureKit stream), `notifications` (notification permission denied);
each writer clears only its own layer, the daemon's clear no longer wipes "no
microphone permission", the on-screen line joins all live layers in priority
order with " · ", and Start clears them all. On the wire the boolean `sticky`
stays next to `topic`: app 0.82 reads only the boolean, and a new daemon
without a topic lands in the channel-loss layer.

**The incomplete-recording note is a fact about the recording, not speech; a
derivative has one source.** Before №316/№317 a derivative (minutes, debrief,
theses) had a single input — a text string — and every consumer decided for
itself what to mix in: `_fit` folded, the daemon's draft cut a window,
`debrief_excerpt` took the head and tail of the file, `speech_of` cut the
section off. The fact "the counterparts were absent for 40 minutes" reached
the model either as speech (MCP minutes read the whole file with the tail) or
not at all (everyone else) — and the 18.09 minutes came out looking complete.
The episode summary was written only by the daemon at stop: when the daemon
died there was no line, although every event was in the sidecar. Now the
source of a derivative is one object,
`meeting_source.MeetingSource(speech, recording_note)`: speech
(`transcript.speech_of`) is what folds and windows cut and what `fact_check`
verifies quotes against; the note (`channel_trace.recording_note` — the same
string the daemon writes at stop, `summary_of`) goes into the prompt as a
separate block outside the transcript tag and AFTER every cut
(`LLM.recording_block`, ru/en/zh, with the rule "never quote it as speech; do
not read the silence of a lost channel as agreement"), into the document as a
mechanical line (`meeting_source.with_note` — asking the model to "mention the
gap under Risks" is unverifiable), and the passport hashes speech plus note
(`sha()`): minutes built without the note go stale exactly once when the note
appears, and the passport writer and the freshness reader are one function.
Every prompt builder (the daemon's draft and "Minutes", MCP minutes, the
rebuild, the `graph_updater` debrief, the retro pass) keeps the block after
its own cut; the debrief takes its window from the end of SPEECH and receives
the live tail theses as a separate "model's notes, not speech" block. The
rebuild restores the summary from the sidecar into the "Co-thinking" tail
through the tail's format owner (`transcript.append_note`; no section —
creates one), with no duplicate on a second run.

**Why a phone recording stopped is a value that travels with the file.** On
07.09 one meeting arrived on the Mac as three pieces with no call in between,
and the reason was nowhere on disk: the companion's `stop()` released the
session and handed the file over, `lastStopReason` was a localized string for
the screen that died with the process, and the Mac saw three ordinary
meetings. Before №200 the stop paths had no reason to name: the codec series,
the post-call budget, the stall watchdog and the media reset each wrote their
own string. Now the only function where `isRecording` becomes false takes a
mandatory `stop(reason:)` — the compiler is the gate, no new stop path can
close a file without naming why — and the recorder writes a manifest
`<file>.json` next to the audio (`Recorder.StopRecord`: kind stop|rotate, a
reason from a closed enum where every value exists only together with the code
point that sets it — `disk_full` is not in the list because nothing detects
it, `at`, seconds, the series stem so that segments of one meeting know each
other, `finalized_ok` set by the finalization delegate for ITS file, not the
current recorder's). The unit of the queue is the pair: `Inbox` moves, rescues,
publishes (manifest first under `.part`, audio last, both under the same
uniquified name so a name collision in iCloud cannot glue the manifest to last
week's meeting), retires and deletes audio and manifest together. An orphan in
`current/` without a manifest gets `no_stop` with the last frame's mtime — the
fact "nothing closed this file", not the guess "the process was killed"; a
manifest that `stop()` managed to write before the process died is kept. On
the Mac `import_meeting` converts the manifest into an event of the same trace
that owns lost channels (`channel_trace.phone_event`, kind `stopped`, the
reason in `reason` — `cause` belongs to the hub's dictionary) before
`graph_updater` and before the "no speech" exit, so the derivative passport
hashes speech plus the note on the first generation; the wording and the
trace-line classifier live in `channel_trace` only, and the document gets the
same summary line that every tail reader already knows (`note_in_tail`, the
rebuild, `meeting_source`), not a second kind of line. A manual stop produces
no event. Both involuntary stops enter the incomplete-recording summary — the
terminal one ("cut off 19:02 (no stop was recorded)") and the rotation
("interrupted 19:02 (microphone did not come back after the call) —
continued, if recording resumed, in the next file"): the phone promises the
continuation at the moment it closes the file and cannot know whether the
restart after the call actually happened, so the wording states the observed
fact and not the future (output round DS). The tail line of the document is a derivative of the sidecar with a single
writer under a compare-and-set gate (`channel_trace.tail_with_summary` over
`safe_write.rewrite_file`), shared by import, reconciliation and the rebuild —
the reconciliation walks files the rebuild may be rewriting from another
process (round 2, DS and GLM). The fact is consumed by
reconciliation, not by a one-shot read at import (output round GLM):
`reconcile_manifests` runs on every scan — a manifest that arrived after its
audio had already moved to `done/` is reunited with it through the import
sidecar's `source` field, a manifest lying in `done/` whose transcript lacks
the event writes it (idempotent), an orphan waits an hour in the import
folder before it is swept, because iCloud delivers a kilobyte of JSON long
before an hour of audio. The manifest dies with the audio in retention.

**The archive summary is a derivative with a passport; its source is what the
model was fed, not the speech.** `Саммари.md` — the one-minute digest, the first
file people open — was built once (`_gen_summary` skipped any non-empty file)
and never refreshed, while the cloud review kept rewriting the minutes it was
built from (№238/№239): on 19.09, 74 of 298 archive folders held a summary
older than its minutes. The input round (DS and GLM) converged on one
mechanism and caught three holes in the plan: the summary's source cannot be
"speech + note" like the other derivatives — speech never changes after the
meeting, so the passport would stay FRESH forever; a second write seam in
`meeting_archive` would fork the gates of `retro_fill._write_derivative`; and
`rename_meeting` rewrote the summary's bytes with a bare `replace` (the folder
name lives in its H1 and links), so with a passport every renamed meeting
would freeze in HUMAN forever. Now the source of the `summary` kind is the
canon of inputs (`summary_source_sha`: the capped materials from minutes,
theses, debrief and the transcript tail, the extracted decisions, the
recording note — never the prompt template words or the config language: the
config says what language NEW documents get and may not rewrite old ones);
`live_sidecar` owns both halves of the passport contract — `derivative_state`
decides, `write_derivative` (moved from `retro_fill`, one seam for every kind:
snapshot → `.prev/` → expect gate → attest, and no passport for a transcript
that does not exist) writes, `retouch` re-stamps the bytes hash after a
mechanical rewrite while keeping the source. One policy for the live path and
the retro pass — MISSING/STALE: a new meeting is MISSING, a review delivery
ages the summary through the minutes (STALE), and no automatic path touches
UNKNOWN — not knowing about 298 legacy files is no reason to rewrite them with
the model on first touch. A FRESH rebuild is excluded for summaries —
everything that affects the output is already in the hash. An empty file is
MISSING for every kind — the oracle, not each writer, says so. The outcome
travels by return value along the whole chain of seams (`write_derivative` →
`summary_pass` → `archive_meeting` → caller) as a value (`SummaryOutcome`:
adopted / built / kept / skipped / failed / refused), never re-derived from the
disk one level up — the retro report used to print "skipped: fresh" about a
file the machine had just rewritten. The pass mode travels the same way as an
enum (`SummaryMode`: auto / adopt / rebuild) with one translator into a policy
next to the policies — not as a "policy + flag" pair: an empty policy is falsy
in Python, and a default substituted by truthiness on the seam silently turned
"build nothing" into the default. Legacy summaries without a passport are
handled only by explicit commands: `retro_fill --summary=adopt` gives a
passport (plus a `summary_adopted` mark) to the sound ones without the model —
materials not newer than the file, our own document structure, and the
recording note either empty or already in the text — and builds nothing;
`--summary=rebuild` adopts first, then rebuilds the rest (224 sound and 74
stale of 298 on 19.09); the pass prints a tally of outcomes at the end.
Adoption runs inside `archive_meeting` after the material copies are
refreshed, on the same folder and the same canon snapshot as the build. The
recording note reaches the summary as a fact block after the materials and as
a line in the document (№317), not as a stray line inside a minutes excerpt.
The manifest carries no copy of the passport state — readers ask
`summary_state()`.

**System health has one rollup and two surfaces, not four private
verdicts.** Before №139 every health signal had its own surface and its own
lifetime: recording problems lived in `SuflerService` and only during a
meeting; the last processing error was a line inside the menu; the nightly
pass was visible only on the "Today" screen, and its service was created
lazily when that screen appeared — on 19.09 `logs/nightly.json` said `slept`
with four steps skipped and nobody had read it; Ollama was probed a second
time from inside the menu view with the result stored in a view `@State`,
unlocalized and hidden for a day behind "Meeting ready"; the daemon's
`pump_alive`/`pump_failures` gauges arrived in every heartbeat since №311 and
died at the decoder boundary. The input round (DS and GLM) converged on one
mechanism and rejected two: extending the meeting-scoped sticky layers
(they are cleared on every start and have no lifetime outside a meeting) and
a new service with a `HealthReportable` protocol (a ceremony for five writers
and one reader). Now `HealthRollup.rollup(recording:isRecording:processingError:
ollama:nightly:)` is a pure function next to `PipelineHealthPresentation` —
the layer that already owns "one presentation for many surfaces" — and the
menu-bar icon and the menu status line read one verdict. The owners of the
facts stay where they were: `PipelineHealthMonitor` (now also decoding the
pump gauges: a dead pump is a stopped recording — critical; consecutive
failed passes — a warning, №313), `MeetingProcessingService`,
`OllamaRuntimeService` (a real `.unknown` state before the first probe — the
old default `.running` made the icon assert a fact nobody had checked),
`NightlyStatusService` (title as a pure function; `.never` without a launchd
agent is "not configured" and not a problem for the icon, `.never` with an
agent is a night that never ran). Freshness belongs to the owners through one
scheduler, `HealthClock` (first read after launch, off the icon's render
path, then one interval for everyone; the menu opening only triggers the same
tick) — two signals with different cadences on one icon, and a view deciding
when to probe, were the output round's Critical. Who colours which surface is
one policy, `HealthPresentation`: the icon takes the worst tier, the dot next
to "Recording" takes only the recording's tier, the menu line is ordered work
→ problem → "Meeting ready" → idle, and the processing error is the owner's
headline (`errorHeadline`), not a third dictionary. The rank is pinned by a table test: **red is
reserved for data loss during a live recording** (disk failure, dead pump);
a processing error, a silent Ollama, a slept night are yellow — the source is
kept, an hour of pipeline or a night is lost, the recording is not. The dot
next to "Recording" is the recording's health, not a REC light: a healthy
recording used to be red, a degraded one yellow — the reflex "drop everything
and look at the recording" was being trained on the wrong colour. No new
daemon, watchdog, timer for Ollama or notification stream: the verdict №68
stands — the rollup folds signals that already exist. Free-disk pre-flight is
a new signal and a separate card (№319).

## Layer boundaries (docs/design/layout.json)

`src/` is a flat folder of 64 modules, and the layers in it (core → llm →
graph / cloud / audio → meeting → app) exist by the facts of the imports, but
until №320 nothing held them except the author's memory and a one-off
measurement. Three things break when files move, not one: imports, who
launches whom by path (four files of the Swift app name `src/daemon.py`,
among them the launch itself and the check whether the code is embedded;
`code_root()` derives the code root from its own location), and where a file
lies. So the single source of truth is a machine-readable layout —
`docs/design/layout.json`: the module → layer table, the direction of the
arrows, an allowlist of the edges that still point upward (each with the card
that removes it, regenerated by `scripts/layout_map.py --regen`, never counted
by hand — the current number lives in `docs/design/layout.md`), and the manual
entry points with a reason. Two sets are kept apart: entry points are the
executable files (`scripts/*`, `app/*.sh`, `src/*.py` with a real `__main__`
guard by AST), each either named by code — Swift, shell, CI workflows,
pre-commit hooks, python subprocesses — or declared manual; named paths are
whatever code and prose (docs, configs) call by path, and every one of them
must exist — a hint to the human or a README step must not lie after a move,
but a library named by a hint is not an entry point. Mentions are read from
code, not prose: python string literals via the AST without docstrings, Swift
and shell without comments. One inventory feeds every measurement: one walk
over the files under git, one table that classifies each file (code, prose,
tests, dated reviews, out of scope), one read and one parse per file — a file
that fails to parse is a listed problem, never a traceback and never a silent
skip; a candidate entry point by location is code by construction (python
becomes an entry point only with a real `__main__` guard, a shell script by
itself), and a table rule that disagrees with a candidate is a red line, not a
silent priority; tests
are outside the measurement (they build synthetic trees), and dated snapshots
(reviews, release notes, posts) describe the code of their day and are not
checked. `--regen` never writes an artifact the loader would reject. The
fields of the artifact are declared once, in code (`_SCHEMA` in
`scripts/layout_map.py`), each with its type and a class that says who writes
it: `measured` — the measurement, on every `--regen` (edge pairs, the stamp,
which executables carry a contract); `seed` — the machine, once, when a record
is born (a run contract guessed from the code), after which the field is a
human decision; `decision` — only a human. The declaration is closed: a field
it does not name is a load error, so a second carrier of one fact cannot
appear in the JSON without a code change a reviewer reads — the free-text
`notes` block went stale exactly that way and was removed. Debt is printed,
not stored: the map groups the upward edges and the entry points without a
probe by the card that removes them (`ticket`, `№…` at the start), and
`--regen` prints only what changed; no gate reads that number. The gate is `tests/test_import_boundaries.py`,
the same class as the other AST guards in `tests/`: every module has a layer,
every upward edge is in the allowlist, every allowlist entry still exists,
every entry point is declared and present on disk — and the reverse: a declared
point nobody calls fails too, and so does a code file outside the scanned
area that names an entry point. Lazy imports inside functions count. The map
`docs/design/layout.md` is generated from the same file, checked for freshness
by the same gate, and never edited by hand. Not import-linter: it needs an importable package, and a flat folder of
modules importing each other by short names is not one. Later phases move the
data root out of `charoite_paths` (№321), cut `graph_updater` (№322) and only
then move files into `packages/` (№323).

## Stopping a recording

Stop is not one action but a wait: the daemon has to flush audio, run the
post-meeting pipeline and release its lock, and the app must not open a new
meeting until the old process is actually gone. That wait used to live in five
scattered flags, which is how a daemon surviving `SIGKILL` could leave the app
in "stopping" forever, with the Stop button doing nothing.

The transitions now live in one pure type, `ShutdownMachine` — phases
(`idle`, `waitingDaemon`, `stuck`, `done`), events (Stop pressed, daemon
exited, poll tick, kill timeout) and actions (close the capture, poll again,
report, force-kill, finish). It has no reference to the service, so every arc
is testable without a running process.

Timings: `terminate()` at 8 seconds, `SIGKILL` at 12, a backup timer at 13,
then polling twice a second. After 30 waits the phase becomes `stuck` — the
app says so in plain words and keeps polling every 5 seconds, and a second
press of Stop is a request to force-kill rather than a no-op.

One rule holds the design together: events go into the machine and actions
come out through a single entry point in the service. Twice during review the
same defect appeared — an event declared in the machine, covered by a green
test, and never actually sent by the service. A test like that pins down
behaviour the system does not have, which is worse than no test at all.

## Two kinds of duplicates

The graph accumulates duplicates of two different natures, and they are handled
by two different mechanisms — mixing them up leads to fixing the wrong thing.

**Conceptual duplicates of cores** — the same topic split into twins by the
extractor across meetings ("API access setup" and "getting a token"). Word
overlap is zero, so only meaning finds them: bge-m3 selects candidates, NLI
judges each pair, and `tier3` merges the chronicles under
`sufler.tier3_auto_apply`. This runs nightly and incrementally after each
meeting.

**An archive folder never links a note this graph does not have.** The
`Граф.md` file inside the folder points Obsidian at the meeting note, but the
folder and the note part ways as a matter of course: the meeting's sphere is
decided from the transcript after the run has already started, so a personal
meeting leaves as a note into its own graph while the earlier pass leaves the
folder behind in the work one. A note that failed to land parts the same way.
The link is then dead forever: stripping dead links runs before this folder is
written and never sees it. So the link and the Finder shortcut are written only
when the note exists; otherwise the file keeps a sentence telling the reader
where to look, and a shortcut from an earlier run is removed. Measured on the
work graph on 16.09: 20 folders out of 278 led nowhere, six of them recent.

**Byte-identical copies of files** — the pipeline deliberately writes meeting
documents twice: the original into `Документация/Стенограммы встреч`, a copy
into `Встречи-архив/<date — title>` so the folder opens from Finder. On a
working graph that is 173 groups and 6.4 MB — duplicated iCloud sync and
duplicated weight on the phone. `scripts/dedup_graph.py` replaces the copy with
a hard link under `sufler.dedup_files`: both paths keep working, the bytes are
stored once. Search does not wait for the nightly job — it hashes content while
scanning and keeps the first copy, so the model never receives the same text
twice in one context.

**iCloud conflict copies** — `Минутки 2.md … Минутки 12.md` next to
`Минутки.md`. Our code never makes such names; they appeared exactly where the
archiver rewrote a meeting document *in place* (`copy2` over the existing path)
on every pass, changed or not — 7 594 of them on 23.09, so the Tasks tab showed
one action item up to ten times. Writers of meeting documents now go through
`safe_write.copy_if_changed` / `write_text_if_changed`: identical bytes are not
written at all, changed ones go through a temporary file and `replace`, and a
material copy keeps its source's times (freshness of the archive is read by
mtime). The archive folder has one writer: the cloud review hands its file to
`archive_meeting(extra=…)` instead of copying it on top. The second rule of
`scripts/dedup_graph.py` moves a copy that is byte-identical to its neighbour
out of the graph into `backups/<graph>/dedup_copies/<run>/` with a manifest
(`--apply-copies` or `sufler.dedup_copies`, off by default and independent of
`dedup_files`); differing copies are only reported. `graph_doctor` counts them,
so a regression shows up in the morning brief.

## How search actually works

Two independent signals fused by RRF: lexical (stemming, IDF, query coverage,
freshness) and semantic (bge-m3 through the local Ollama). Neither is enough
alone — lexical catches internal identifiers a vector never will, semantic
closes the vocabulary gap when the question uses different words than the note.

**The daemon's memory during a meeting is the same design in Python**
(`src/graph_search.py`). Until №250 the live contours asked a separate memory
server over HTTP, and on a working graph it answered in 2.6–22 s — the instant
answer has a 2.5 s budget, so in practice it ran without memory. Now the index
lives in the daemon process: files of the project graph without the meeting
archive and transcript copies (3 160 files warm up in under two seconds),
BM25-lite over stems with IDF, length normalisation and a capped hub boost —
a 280 KB owner node no longer surfaces for every query — freshness, dossier
summaries first, one hop over `[[links]]` from a found node, and the same
honesty gate. Vectors are per chunk (headings, breadcrumbs), cached in
`data/graph_search/` by mtime; during a meeting only the query is embedded,
files are indexed after the meeting (45 s cap) and at night. A warm query
costs ~0.1 s of lexical work plus one embedding call when Ollama is free.

**The answer never claims the unread part was checked.** The index leaves out
the meeting archive and transcript copies on purpose — without that the cold
walk does not fit in seconds. On the working graph that is 11 506 files out of
14 789, and 1 945 archive files carry decision lines. The answer tables used to
say the opposite («probably nothing in the archive»), asserting a check of what
was never opened. Now every negative answer carries what stayed out: the fields
come from the walk itself, not from the configured list, so a graph without an
archive folder produces no caveat and a graph whose folders are named
differently is not described as unread. Files the walk reached and could not
open are counted the same way. The wording lives in one facade table, and the
caveat names *separate fragments* — topic summaries are built by a walk over the
whole graph, archive included, so claiming otherwise would be a lie in reverse.

**A link to a merged node points at the canon.** When two notes about one thing
are merged, the duplicate stays as a two-line redirect file. Search used to
treat it as an ordinary document: incoming links fed the stub instead of the
canon, a hop from a node dead-ended there, and in the answer it took a slot with
an arrow instead of content. On the working graph (3 199 files, 404 stubs) that
was 1 048 links pointing at dead files and 40 hops that never happened. Merge
chains are walked with cycle protection and a depth cap. In the answer a stub is
replaced by its canon before ranking, so the slot is never lost.

**Adjacency is keyed by path, not by file name.** A link that names a folder must
resolve unambiguously — the author already gave that certainty, and a name key
threw it away. Measured on the working graph on 17 Sep: 3 532 links out of 41 409
landed on a file other than the one named, systematically on a digest instead of
a core, because digests are rebuilt nightly and are always fresher. After the fix
not a single link lands elsewhere; 17 links point at a path that no longer exists.

Resolution lives in `LinkCatalog`, one link catalogue per index generation. Two
levels face outwards: `named` answers which document is NAMED (a path in the
catalogue wins; a path named with no file behind it is a miss, not a namesake
lookup; a bare target goes through the name catalogue, where a live file always
beats a stub), and `live` answers which LIVE document stands behind it — a stub
is unrolled through the canon map, and a stub behind a stub is an honest "nowhere".
The unrolling lives here rather than in each consumer (incoming-link votes, hops,
stub replacement in the answer), because each of them did it differently and
missed on bare targets.

The catalogue is built once per snapshot and published together with the documents
and the votes in a single assignment: while documents reached the world separately
from the catalogue, a search could see new files with old resolution and crash on a
target its own snapshot no longer had. The assignment itself is one operation with
a mandatory base: the walk takes the generation under the lock as its first line
and hands it to the publisher explicitly — the publisher cannot re-read the field.
"Decide on one snapshot, write another" is thereby inexpressible rather than
forbidden in prose; three review rounds in a row had caught exactly that in
different branches of the walk, and a test now guards the shape (exactly two
assignments).

Who owns a key — a name or a path — is decided by one function everywhere: newest
first, shortest path on a tie, because a node's date is its mtime and a checkout or
a graph copy moves it. The single priority on top of that: for the name catalogue a
live file beats a stub.

What the fix did to ranking (30 queries, top 5): lexical coverage of the query did
not move at all — 0.989 before and after, every reshuffle happens between files
with FULL coverage. What changed is who gets shown at equal relevance: digests take
half as many slots (34 → 17), primary nodes more (47 → 61), median age 10 → 9 days.
The hub boost needed no recalibration: 462 nodes sit at the cap against 467, the
same set.

**The verdict is a field, not a prefix.** `brain.vault_search` returns a
`Result`: `status` is one of *confident* / *weak* / *unverified* / *empty*,
`fragments` is what goes into a prompt (dossiers and snippets, no headers, no
markers), `text` is the human rendering. Without an embedding (Ollama busy
during a meeting) lexical matches are *unverified*, never confident and never
"nothing in the archive"; "weak" — the honest "the archive has almost nothing"
— is only pronounced when the vector cache covers at least 80 % of the index.
Dossiers count as evidence in the verdict (their key coverage of the query), so
a summary without snippets is never "empty". What to *say* is one table in the
facade (`LEAD` / `ABSENCE`, complete over every status; the reason behind
"unverified" — embedder busy or cache incomplete — is a field of the result) and
one builder, `memory_block`, that splits a prompt budget between graph nodes and
archive snippets by share instead of letting the nodes eat the archive; on a
confident verdict the snippets come first and nodes get only the remainder.
Déjà-vu goes to graph nodes unless the verdict is confident and calls the archive
"empty" only on a verified verdict; all three contours feed the block as built. The previous contract was a string with "⚠" parsed by
`startswith` in three places — a dossier printed before the marker silenced the
gate — and then an enum re-interpreted by three hand-written `if/elif` chains.

**Chunks, not files.** Each file is split by markdown headings; long sections
are split by paragraphs with overlap, and text without punctuation by length.
Every chunk carries a breadcrumb (`File → H1 → H2`) into the embedder, because
a block that reads "yes, let's do that" means nothing on its own. Measured on a
working graph: 941 unique files become ~4800 chunks, median 1686 characters.

This replaced one vector per file built from the first 12 000 characters. For a
node that assumption held; for a meeting transcript it inverted the result —
decisions are made at the end. On the same graph, 325 files were longer than
that cutoff, and **63% of all content never reached the index**. Ollama also
truncates bge-m3 input silently at roughly 12 300 characters despite the model's
declared 8192 tokens — verified by binary search, a unique marker appended at
the end leaves the vector unchanged.

**The hidden flag.** iCloud marks items inside its container `UF_HIDDEN`, and
`FileManager` with `.skipsHiddenFiles` skips them without a word. On a working
graph that hid `Люди`, `Системы`, `Встречи` and nearly all of `Документация` —
546 files visible out of 1172. Search no longer looks at the flag at all;
intentionally hidden folders (`.obsidian`, `.trash`, `.git`) are filtered by
name, which is a property the user controls and sync does not.

**Document role.** The pipeline emits several documents per meeting, and they
differ sharply as an answer to "what did we decide": raw material (transcript,
hints, drafts) is damped to 0.7, distilled material (minutes, summaries, cores)
is lifted to 1.15. Raw text is best for a quotation and worst for an answer.

**Context budget.** Output is capped: no single source takes more than 40% of
the budget, a source left with under 300 characters is dropped whole, and the
two strongest sources go to the beginning and the end — attention sags in the
middle of a long context. The 32K window is not a target to fill.

**Cost.** Files are read and normalized only when their mtime changes; needles
are matched over UTF-8 bytes rather than through `String.range(of:)` with its
unicode normalization; snippets are extracted only for candidates that can
still reach the answer. On the working graph a query takes 0.6-1.1 s, down
from 1.8-2.5 s.

### What the numbers say

Quality is measured end-to-end (search → synthesis) against a private set of
questions with expected facts; generation temperature is pinned to zero so the
bench compares changes rather than sampling noise. On the working graph:

| Change | Facts recalled |
|---|---|
| One vector per file, first 12 000 chars | 11 / 14 |
| Vectors per chunk | **13 / 14** |
| Damping only transcripts | 11 / 14 |
| Weighting by document role | **13 / 14** |
| Context budget on/off | 13 / 14 either way |

What the chunked index recovers is exactly the kind of detail a question is
usually about: rate limits, token names, system abbreviations — things stated
once, deep inside a long meeting.

The budget shows no gain here and is kept for a different reason: it bounds
how much of the context a single transcript may occupy, which this ten-question
set does not exercise.

On a fully built index (865 files, 4335 chunks) the same bench reaches
**14/14**: every expected fact survives into the answer. The two that used to
go missing were details stated once, deep inside long meetings — a rate limit
and a token name.

One negative result worth recording. Adding "carry abbreviations and error
codes VERBATIM" to the synthesis prompt looked like an improvement (13/14 on a
first run) and turned out to be a regression once temperature was pinned:
11/14 against 13/14 without it. The instruction pushes the model to quote
instead of admitting it does not know. The change was reverted.


## Measuring memory quality

`app/Probes/MemoryBench.swift` runs the real search path — the one the app
uses — against a set of questions with expected facts, and reports how many of
those facts survive into the answer.

    CHAROITE_BENCH=~/path/memory_bench.yaml \
    CHAROITE_GRAPH_DIR=~/path/to/graph \
    CHAROITE_BENCH_ANSWERS=1 \
      swift test --package-path app \
        --filter CharoiteAppLiveProbes.MemoryBench

Live probes are a separate Swift test target. CI and nightly builds compile
that target so its signatures cannot rot — behaviour runs only by hand — and execute only `CharoiteAppTests`; a missing
private graph or local model must not look like a successful product test.

Without `CHAROITE_BENCH_ANSWERS` it measures only what search delivers;
with it, the full path through synthesis. The report lands in
`/tmp/charoite_bench.txt` with the missing facts named.

The question set stays outside the repository — it is about real meetings.
Format is deliberately trivial:

    - q: "What did we decide about the payment provider?"
      must: ["YuPay", "2.8%"]

**Generation temperature is pinned to zero.** This is not a detail. With the
default temperature the same code scored 11, then 13, then 11 again out of 14 —
numbers that cannot tell you whether a change helped or you got lucky. The
pinned bench is what caught a prompt "improvement" of mine that was actually a
regression.

`scripts/memory_bench.py` measures the daemon's memory (`src/graph_search.py`)
— what the owner sees during a meeting; `--brain` and `--legacy` keep the old
contours for before/after comparisons. It still cannot answer questions about
the app's search, which is a separate Swift implementation.

## Where code and data live

The shipped code and the working files are deliberately separated.

- **Code** — `src/`, `scripts/`, the config example. In the app it lives inside
  the bundle (`Charoite.app/Contents/Resources/charoite`) next to the python
  runtime; in development, in the cloned repository.
- **Data** — recordings, transcripts, logs, models, `config/config.yaml`. These
  belong to the user and live in the working folder.

The split has a sharp edge at the config: `config/config.yaml` is the owner's
and comes from the DATA root, while `config/config.example.yaml` ships with the
release and comes from the CODE root. `config_loader.load_user_or_example`
reads them from those two different roots for exactly that reason; while the
two roots were one tree the difference was invisible, and asking the data
folder for the example crashed the module on import as soon as an installed
entry point named its own root.

The entry point names the working folder. The root is owned by the path canon
(`src/charoite_paths.py`): `use_data_root(path)` sets it for the process,
`resolve_root` answers with the named root before `CHAROITE_ROOT`, and the
variable stays the channel for children — nightly scripts, the indexer, the
cloud worker — which derive the root themselves. The app still passes
`CHAROITE_ROOT` to the daemon on launch. Without it the daemon refuses to
start — exit code `EXIT_ROOT_UNNAMED` (5) and an error status carrying the recipe — because an
entry point NAMES the root rather than asking for it; deriving it from the
file location and publishing that guess to children is what the refusal
exists to prevent. A module that merely asks (`resolve_root`) still gets a
guess when nothing is named — the code root — and that is the only place the
derivation survives. The code root is derived once, from where the canon
itself lies (`<root>/src/charoite_paths.py` next to `<root>/scripts/`,
checked on import);
`code_root(__file__)` only verifies that the caller belongs to that tree.
Deriving it from each caller's position held while every caller sat one level
below the root: a module moved into `packages/<dist>/src/<pkg>/` would have got
`packages/<dist>/` as the code root and `packages/<dist>/src/` as the data
root (№331).

The answer is asked for, never remembered. `ROOT = resolve_root(__file__)` at
module level — or the same line as a class field — looks like a call to the
canon, but it is a snapshot: the value is taken while the module is being
imported, which is earlier than the entry point gets to name the root. The
process then disagrees with itself in silence — half the modules live in the
named root, half in the one derived from the file location. That is how the
model lease broke: the writer stored it under the frozen root while the health
watchdog looked under the current one, so «our generation is in flight»
answered «no». The layout guard knows this shape as `snapshot` and counts
everything executed at import time — a class body, an `if`, a `try` — except
`if __name__ == "__main__"`, which does not run on import at all.

The guard reads text, so its reach ends where text stops being the evidence,
and the boundary is named rather than implied: a method called on import
(`X = A().root()`), a wrapper taken as a value (`functools.partial`), and a
helper imported from another module all pass it silently. Those are caught by
behaviour instead — the witness in `tests/test_backup_offload.py`, which asks
every registered module for the root after the entry point has renamed it and
then looks for the *previous* root as a value anywhere in the engine — and,
once the canon starts refusing an unnamed root, by the refusal itself. Chasing
each new way of writing the same freeze with one more heuristic is how the rule
spent five review rounds; the shapes list stays a cheap detector of the common
cases, not the guarantee.

The order matters because deriving from the file location answers a different
question: where the CODE lives. While code and data share one tree the answers
coincide by accident; from an installed package the same formula yields
`site-packages`, and the pipeline would silently read defaults instead of the
owner's settings. So the root is named, not guessed: an empty value is
refused, a second root different from the active one is refused, and a
deliberate change goes through `use_data_root(..., replace=True)`. Writing to
the variable is a publication: the canon remembers what was there before it
and `forget_data_root()` restores that value — but only while the variable is
still its own (#327).

The reason is simple: the bundle is signed and read-only. Meeting recordings
cannot be written into it, and keeping the code in a user folder would mean
cloning it by hand — the install would start with a terminal again.
