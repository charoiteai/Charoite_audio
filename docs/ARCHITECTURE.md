# Architecture

***English** · [Русский](ARCHITECTURE.ru.md) · [中文](ARCHITECTURE.zh.md)*

## Overview

```
mic ───────┐                        ┌─ live transcript (per-voice paragraphs)
           ├─ AudioHub ─ STT ─ daemon ┼─ loops: ⚡ answers · theses · minutes
BlackHole ─┘   (3s chunks)          │   · déjà vu · names · dialogue markup
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
- **One embedder — bge-m3** (Ollama): semantic search and the core-revision
  prefilter. There is deliberately no second embedding model.
- **Precision — local NLI** (src/nli.py, ONNX): thesis dedup and the
  core-revision judge. Only in latency-tolerant loops; live hints and
  déjà vu run on cheap stemming.
- **Cloud — opt-in post-meeting enrichment only**, via subscription (no API
  key in the environment). Meeting data never leaves the machine by
  default; no cloud memory SaaS, none planned.

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

`app/Tests/MemoryBench.swift` runs the real search path — the one the app
uses — against a set of questions with expected facts, and reports how many of
those facts survive into the answer.

    CHAROITE_BENCH=~/path/memory_bench.yaml \
    CHAROITE_GRAPH_DIR=~/path/to/graph \
    CHAROITE_BENCH_ANSWERS=1 \
      swift test --package-path app --filter MemoryBench

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
