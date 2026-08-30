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
- **An error inside a stream is an error.** An `{"error": …}` line inside a
  200 response, or a stream that ends without its terminator, raises: a
  truncated set of minutes is never passed off as complete.

### The hint outranks the thread (inside the daemon)

Since 30.08 the dialogue markup walks under the arbiter too: it used to be
the only loop taking the model past the lock — 900 tokens every six seconds
while the hint and ⚡ queued behind it; now it takes the lock quietly for a
second and lets the paragraph wait otherwise. Dropped frames of the fast
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
  the revision has its own exit code 2, missing graphs are marked in the status.
- **A person's edit does not vanish.** A core changed during the long pair
  judging is not overwritten from the in-memory snapshot — the pair waits for
  the next night. A duplicate's handwritten "Суть" moves into the canonical
  core instead of living only in a rotating backup. Dedup re-checks the digest
  before substituting: a file rewritten by the pipeline between the scan and
  the link is skipped.

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
   and enriches the graph with links visible only from history.

Each phase is published atomically under `logs/meeting-status/`: the macOS
app shows real progress, keeps failures linked to the source transcript, and
announces readiness only after the exact meeting note exists.

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
Ollama required; semantics is layered on top.

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

**Byte-identical copies of files** — the pipeline deliberately writes meeting
documents twice: the original into `Документация/Стенограммы встреч`, a copy
into `Встречи-архив/<date — title>` so the folder opens from Finder. On a
working graph that is 173 groups and 6.4 MB — duplicated iCloud sync and
duplicated weight on the phone. `scripts/dedup_graph.py` replaces the copy with
a hard link under `sufler.dedup_files`: both paths keep working, the bytes are
stored once. Search does not wait for the nightly job — it hashes content while
scanning and keeps the first copy, so the model never receives the same text
twice in one context.

## How search actually works

Two independent signals fused by RRF: lexical (stemming, IDF, query coverage,
freshness) and semantic (bge-m3 through the local Ollama). Neither is enough
alone — lexical catches internal identifiers a vector never will, semantic
closes the vocabulary gap when the question uses different words than the note.

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

The older `scripts/memory_bench.py` measures a different implementation (the
brain server, or its own Python fallback), so it cannot answer questions about
the app's search.

## Where code and data live

The shipped code and the working files are deliberately separated.

- **Code** — `src/`, `scripts/`, the config example. In the app it lives inside
  the bundle (`Charoite.app/Contents/Resources/charoite`) next to the python
  runtime; in development, in the cloned repository.
- **Data** — recordings, transcripts, logs, models, `config/config.yaml`. These
  belong to the user and live in the working folder.

`CHAROITE_ROOT` names the working folder: the app passes it to the daemon on
launch and every python module reads the root from there
(`src/charoite_paths.py`). Without the variable the root is derived from the
file location, so running from a repository behaves exactly as before.

The reason is simple: the bundle is signed and read-only. Meeting recordings
cannot be written into it, and keeping the code in a user folder would mean
cloning it by hand — the install would start with a terminal again.
