# Architecture

***English** · [Русский](ru/ARCHITECTURE.md) · [中文](zh/ARCHITECTURE.md)*

## Overview

```
mic ──────────┐                        ┌─ live transcript (per-voice paragraphs)
              ├─ AudioHub ─ STT ─ daemon ┼─ loops: ⚡ answers · theses · minutes
system audio ─┘  (3s chunks)           │   · names · dialogue markup · live context
(ScreenCaptureKit; BlackHole fallback) └─ NDJSON stdout ←→ stdin commands (UI)

Stop → recording rebuild → graph update → archive + Summary → [optional cloud review]
```

Everything runs on your machine by default; local LLM and embedding calls go to loopback only (Ollama / MLX). Optional cloud features are off by default (see [PRIVACY.md](../PRIVACY.md)). The app's only non-meeting network request of its own is the optional GitHub release check.

## The daemon (`src/daemon.py`)

One process runs thread loops around a shared locked `Transcript` (`src/transcript.py`; runtime modules never import `src/main.py`): STT loop, fast question trigger, instant answers, auto-hints, co-thinking (theses), meeting thread, live minutes, name resolution, dialogue markup, live archive context, autostop, optional cloud loop and heartbeat.

- **Protocol:** NDJSON events stream to stdout (`transcript`, `thesis`, `hint`, `status`, …); commands arrive on stdin (`hint`, `ask <question>`, `expand [topic]`, `cloud`, `summary`, `set hints|theses|cloud on|off [quiet]`, `stop`). A second instance is blocked via `flock` (`logs/daemon.lock`).
- **Errors vs. content:** Daemon statuses carry `"error": true` on failure; a model error never overwrites the last good hint on screen.
- **Grounded live feed (opt-in):** With `sufler.live_feed: true`, `grounded_feed.semantic.SemanticActor` and `grounded_feed.local_runner.LocalFeedRunner` run strictly under `LocalLoopbackAuthority` (zero cloud egress). Accepted speech, narrative claims, open questions, cursor and displayed entries live in one immutable state, with verified Stop export (`<transcript>.grounded.json`). See [live-feed.md](live-feed.md).

### Audio capture and time axis

The macOS app captures system audio (and microphone on macOS 15+) via ScreenCaptureKit into a growing stream file described by `data/sck_stream.json`. `AudioHub` tails that file. Without a fresh manifest, `AudioHub` falls back to BlackHole plus microphone via PortAudio.

`ChannelAxis.swift` places each buffer by its stream `synchronizationClock` converted to host time (`CMSyncConvertTime`). Short drops (0.1–2 s) are padded with silence (up to 30 s for a late channel start, half of `AudioHub.BUF_CAP_S`); longer gaps or backward timestamps re-anchor the axis without cutting audio. Both channels remain separate, giving free me/them separation; overlapping speech drops the microphone chunk as speaker echo.

### Channel labels and owner (`src/channel_labels.py`)

`ChannelLabels` is the single source of truth built from config for:
- `mic_raw` — raw channel label (`sufler.user_name` or the default local label unless it collides with a neutral speaker label),
- `mic_signed` — owner signature for transcripts and graph attribution,
- word-match owner name.

### Priority and scheduling (`src/live_gate.py`, `src/machine_power.py`)

A live meeting always outranks background jobs (rebuilds, graph extraction, nightly dossiers, core revision):
- **Meeting lock:** `logs/daemon.lock` is held for the lifetime of the recording (console `src/main.py` also takes `logs/console.lock`). Background workers poll it non-blockingly and pause between chunks while it is held.
- **Rebuild serialization:** `logs/rebuild.lock` ensures only one transcript rebuild runs at a time per machine.
- **Process detection:** `live_gate.daemon_process` detects running daemons across data roots by exact python + script argv pattern.
- **Transport resilience (`src/llm.py`):** `llm.stream` and `llm.complete` retry transient `503`/`429`/`502` responses with backoff, treat in-stream `{"error": ...}` frames as errors, and track in-flight generation leases so health watchdogs distinguish active inference from stalls.
- **Inside the daemon:** Instant hints outrank the background meeting thread; the thread runs strictly on the light model and yields the hint slot immediately.
- **Power awareness:** `src/machine_power.py` detects macOS Low Power Mode so slow hint latency is reported honestly to the user.

### Decision gate in shadow (`src/decision_gate.py`)

Opt-in (`sufler.decision_gate_shadow: true`) research gate running alongside instant answers (`⚡`). It logs only numeric scores and verdict metadata (never transcript text) to evaluate when a live answer is warranted; measured with `scripts/gate_bench.py`. See [research/decision-gate.md](research/decision-gate.md).

### Optional cloud layer (`src/cloud.py`, `src/privacy.py`)

All five cloud permissions (`cloud_enrich`, `cloud_live`, `cloud_hints`, `cloud_edit_graph`, `cloud_engine`) live in `src/privacy.py` and default to `false`:
- **CLI loops (`cloud_enrich`, `cloud_live`, `cloud_hints`, `cloud_edit_graph`):** Invoke `claude -p` via subscription (no API keys in env), validate exit codes and non-empty stdout, and never silently swallow missing binaries.
- **Graph sandbox:** Cloud dossier/graph review never edits the live Obsidian folder directly. It runs against a snapshot copy (`src/file_locks.py`), validates diffs against symlink escapes and destructive edits, and applies accepted changes atomically under the shared graph lock.
- **Cloud chat (`llm.engine: cloud` + `sufler.cloud_engine: true`):** Routes chat calls to an OpenAI-compatible gateway whose key lives in a separate `0600` file (`llm.cloud_key_file`, never in `config.yaml`). Secrets are redacted from error output; STT, diarization, embeddings and NLI remain local regardless of chat engine.

## Diarization: two passes

1. **Live pass (`src/diarize_live.py`):** Per-channel speaker tracking (ERes2Net embeddings + pyannote segmentation ONNX) plus optional streaming Nemotron (`src/live_nemotron.py`). Yields to STT under backlog.
2. **Post-meeting pass (`src/rebuild_transcript.py`, `src/diarize.py`, `src/diarize_nemotron.py`):** Re-labels full channel recordings after Stop, filters echo, resolves owner voices, merges shards, and matches spoken names with pitch-register veto.

Full specification, contracts and thresholds: [DIARIZATION.md](DIARIZATION.md).

## Post-meeting pipeline (`src/graph_updater.py`)

After Stop, the pipeline runs: recording rebuild → minutes/debrief/theses generation → archive export (`src/meeting_archive.py`) → knowledge graph update → optional cloud debrief.

- **Derivative passports (`src/transcript_origin.py`):** Minutes, debrief, theses and normalized action items record the hash and origin of the transcript they were generated from. When a transcript is rebuilt after diarization, unmodified auto-generated derivatives are regenerated cleanly (archiving the previous copy to `transcripts/.prev/`), while hand-edited files are preserved and only restamped with resolved speaker names.
- **Idempotency (`src/once.py`):** Post-meeting steps guard against double execution on retries or crash recovery.
- **Deterministic action items:** Assignments are normalized after LLM generation into standard `- [ ]` checkboxes so they reliably surface in the Tasks view.

## The knowledge graph (`src/charoite_graph/`)

The knowledge graph is a plain Obsidian markdown folder — files on disk are the single source of truth (no external graph or vector database).

- **Structure (`src/charoite_schema.py`, `src/graph_links.py`):** Meetings (`Встречи/` / `Meetings/`), People (`Люди/` / `People/`), Systems (`Системы/` / `Systems/`), Cores (`Ядра/` / `Cores/`), Blockers (`Блокеры/` / `Blockers/`) and Dossiers (`Досье/` / `Dossiers/`). Multiple spheres/projects can live side by side as separate graph folders.
- **Frontmatter and redirects:** `src/charoite_graph/frontmatter.py` manages YAML frontmatter; `src/charoite_graph/redirects.py` resolves merged nodes to their canonical target so links never point at stale duplicates.
- **Atomic writes (`src/charoite_graph/safe_write.py`):** Every node update writes to a temp file and swaps atomically under the shared lock (`src/file_locks.py`) so no node is ever truncated mid-write.
- **Graph hygiene:** Neutral diarization labels (`Speaker 3`) and one-off background mentions never become person nodes; `scripts/graph_doctor.py`, `scripts/dedup_graph.py` and `scripts/migrate_placeholders.py` audit and clean the graph.

### Dossiers: a summary layer between search and the graph

At night (`scripts/nightly.sh`, gated by `scripts/night_gate.py`), a local model builds and incrementally refreshes a dossier per cross-cutting Core (1-hop neighbourhood): current status, chronology, decisions, open questions and participants with source links.

- **Incremental fingerprints:** Each dossier records a hash of its source inputs; unchanged topics skip model calls.
- **Author edits preserved:** A dedicated `Author edits` section survives automated rebuilds.
- **Search priority:** Search consults the lexical dossier index (`Досье/_index.json`) first while keeping primary meeting notes distinct from derived summaries.

## Memory model and search (`src/brain.py`, `src/charoite_graph/graph_search.py`)

- **One LLM gateway (`src/llm.py`):** All chat completions use pinned `temperature: 0` for reproducible answers and extraction.
- **One embedder (`bge-m3` via Ollama):** Powers semantic search and Tier-3 core revision.
- **Local NLI judge (`src/nli.py`, ONNX):** Handles semantic thesis deduplication, core-revision equivalence checks and decision-gate zero-shot fallback. See [MODELS.md](MODELS.md).
- **Search profiles (`src/brain.py`):** `brain.search(profile, query)` tailors retrieval for archive Q&A, live meeting context (`src/threads.py`) and pre-meeting briefs. Documents are split by markdown headings into chunks; adjacency is keyed by relative path (not bare filename); hidden iCloud files (`UF_HIDDEN`) are handled explicitly; context budgets cap any single source at 40% to prevent one long meeting from crowding out the rest.
- **Honest scope:** Answers never claim unread/unindexed files were checked; folder MOC indexes serve navigation without overriding primary sources.
- **MCP server (`src/mcp_server.py`):** Exposes live status, transcript, hints, minutes and graph updates to MCP clients strictly scoped to `CHAROITE_ROOT`.
- **Quality measurement:** `scripts/memory_bench.py` validates retrieval and answer survival against demo or custom question sets; `scripts/retrieval_compare.py` compares ranking runs offline on frozen judgments.

## Surviving a crash and data integrity

- **One timestamp per recording:** Raw PCM and transcript filenames share a single session stamp. On normal Stop, PCM is finalized to WAV; on crash recovery at next start, orphaned `.pcm` pairs are caught up automatically.
- **Audio invariants:** `AudioHub` enforces strict sample accounting and honest per-channel degradation (monotonically rising dead-channel age, 30 s visibility). See [INVARIANTS.md](INVARIANTS.md).
- **Stopping a recording:** Stop drains pending audio, flushes final transcript/thread state without wiping the canvas, and hands off to the background rebuild.
- **Kinds of duplicates handled:** Conceptual core duplicates (Tier-3 revision + `scripts/dedup_graph.py`), byte-identical archive mirrors (deliberate copies outside the graph), and iCloud conflict copies (`Minutes 2.md`).
- **Nightly guarantees (`scripts/nightly.sh`):** Four scheduled `launchd` slots survive a closed laptop; `CHAROITE_NIGHTLY_UNTIL` prevents night jobs from spilling into the workday; live meetings preempt heavy nightly steps; user edits made concurrently to a core are never overwritten.
- **App UI efficiency:** Ticking values (recording clock) live in isolated views, and publish gates compare actual consumer-visible state to avoid redundant SwiftUI repaints.

## Layer boundaries (`docs/design/layout.json`)

Code layout is enforced mechanically by `scripts/layout_map.py` and `tests/test_import_boundaries.py` (run in `scripts/preflight.sh` and CI):
- `docs/design/layout.json` is the single machine-readable source of truth: module-to-layer assignment, allowed dependency direction, explicit debt allowlist, manual entry points, `run_contracts`, and `mutation_critical` doors.
- `docs/design/layout.md` is generated from `layout.json` and checked for freshness.
- Every executable path referenced in code or prose (`src/*.py`, `scripts/*`, `app/*.sh`) is verified to exist on disk.

## Readiness probe and app updates

- **Readiness probe (`scripts/doctor.py`):** Checks Python runtime, daemon config, dependencies, audio devices, Ollama/models, NLI readiness and graph folder without trusting stale state inside the data folder.
- **Release updates:** The macOS app periodically checks GitHub's public releases API (disabled with `sufler.check_updates: false` or `CHAROITE_NO_CLOUD=1`). Before swapping the bundle, it verifies the maintainer's ed25519-signed release manifest (version + sha256), the Apple Developer ID signature, and that no recording is active. See [RELEASING.md](RELEASING.md).

## Where code and data live

Shipped code and user data are strictly separated so signed `.app` bundles stay read-only:

- **Code root:** `src/`, `scripts/`, `config/config.example*.yaml`, `pyproject.toml` and `requirements-nemotron.lock`. Staged into `Charoite.app/Contents/Resources/charoite` via `app/stage_code.sh` (built by `app/make_app.sh`).
- **Data root:** `config/config.yaml`, `recordings/`, `transcripts/`, `logs/`, `models/`, `engines/`, `backups/`. Owned by `src/charoite_paths.py`: entry points declare it via `CHAROITE_ROOT` / `use_data_root()` and refuse to start without it (exit code `5`, `EXIT_ROOT_UNNAMED`; exit code `11`, `EXIT_PRIVACY_REFUSED` on a blocked remote model URL). Data roots inside an `.app` bundle are refused.
- **No import-time root snapshots:** Modules ask `resolve_root()` at call time rather than caching `ROOT` at import time (enforced by `scripts/layout_map.py`).
- **Clean refusal drain:** The macOS app (`DaemonLaunch.swift`) drains daemon stdout to EOF (with a bounded ceiling for lingering grandchildren) before declaring exit, so startup refusal statuses are never lost.

### Graph identity and compatible backup locks

Graph writes identify an existing directory via an open file descriptor (native physical path on macOS, descriptor path on Linux) without case or Unicode folding. `GraphAddresses` captures the physical graph view plus all backup, sandbox, quarantine and cleanup roots once per mutation (`mutation_snapshot` / `mutation_lock`), refusing writes if identity fails. Shared-lock CLI writers acquire the ordered group of primary and proven legacy lock addresses under a single deadline, releasing the whole group on partial failure.
