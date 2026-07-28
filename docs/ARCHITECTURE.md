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
