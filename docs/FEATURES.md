# Features

***English** · [Русский](ru/FEATURES.md) · [中文](zh/FEATURES.md)*

## During the meeting

- **Recording nudge** — optional calendar integration reminds you to start recording when a scheduled call begins.
- **Native system audio (ScreenCaptureKit)** — captures call audio (and microphone on macOS 15+) directly without virtual drivers; BlackHole remains the fallback for headless or denied-permission runs, with an explicit notice when used.
- **Live transcript** — utterances merge into clean per-speaker paragraphs; consecutive lines from the same voice flow without repeating the speaker prefix, and dropped audio spans are noted explicitly.
- **Live diarization and names (`src/speaker_names.py`)** — labels voices as `Speaker 1/2/…` from the first seconds, then assigns names automatically once someone introduces themselves or is addressed (vetoed if voice pitch contradicts the name; silent model refusals are never disguised as success). Details: [DIARIZATION.md](DIARIZATION.md).
- **The meeting thread** — the single live canvas that grows incrementally as topics emerge: `●` topic, `-` point, `⚑` decision/deadline, `?` open question, `⏮` archive recall with date. Earlier topics collapse with counters; restated thoughts are deduplicated. With `sufler.live_feed: true`, the experimental grounded chronological feed runs locally (see [live-feed.md](live-feed.md)).
- **Instant answers (`⚡`) and live hints** — detects questions directed at you and weaves concise local answers into the thread alongside topic context, relevant archive history and open items.
- **Live archive context** — searches your knowledge graph as the meeting topic emerges and surfaces matching people, systems, decisions and past discussions.
- **Opening brief** — pre-populates the hint area at meeting start from calendar context and prior notes.
- **Auto-theses and live draft minutes** — extracts 📌 checkpoints (decisions, deadlines, action items) and 💭 key thoughts, and refreshes draft minutes roughly every 10 minutes.
- **Ask the assistant** — type any question mid-meeting to query the live transcript and graph memory (`src/brain.py`).
- **Decision gate in shadow (opt-in, research)** — `sufler.decision_gate_shadow: true` evaluates when an instant answer should fire without logging conversation text (`scripts/gate_bench.py`; see [research/decision-gate.md](research/decision-gate.md)).
- **Optional cloud assist (`☁️`, off by default)** — `cloud_live` and `cloud_hints` (`src/privacy.py`) provide a second opinion or refine thread lines in place with highlighted edits (`==...==`).

## After the meeting (automatic)

- **Refined debrief** — with `sufler.refined_debrief` enabled, paragraphs that pass the audit stay in the debrief. A marker identifies uncertain details such as the speaker’s identity. If a passage still fails verification after correction, the debrief shows the corresponding transcript excerpt.
- **Two-pass diarization rebuild** — re-runs speaker separation over the full channel recordings after Stop (sherpa-onnx by default, or optional Nemotron via `sufler.diarize_backend: nemotron`, `src/diarize_nemotron.py`). Unfinished rebuilds and silent recordings are surfaced visibly.
- **Layered documents** — produces plain-markdown BLUF outputs in your configured language (`sufler.language: ru|en|zh`, custom `sufler.minutes_template` supported): one-minute **Summary** → **Minutes** → **Debrief** → full **Transcript**, exported into timestamped archive folders (`YYYY-MM-DD HH-MM — Topic`) with a portable meeting card.
- **Normalized action items** — assignments in minutes (auto-drafts, manual `scripts/protocol.py`, and MCP tool output) are deterministically normalized into `- [ ]` checkboxes scoped to actual participants so they appear reliably in the Tasks window. Existing files can be migrated with `scripts/fix_action_items.py`.
- **Self-updating knowledge graph (`src/charoite_graph/graph_nodes.py`)** — creates and updates Obsidian nodes for Meetings, People, Systems, Cores and Blockers; ignores neutral speaker placeholders and one-off background mentions.
- **Preserved edits and safe recovery** — auto-generated minutes regenerate cleanly after diarization rebuilds (backing up prior copies to `transcripts/.prev/`), while hand-edited documents are preserved and only restamped with resolved names. Stalled models, crashes and forgotten silent recordings recover automatically.
- **Optional cloud debrief (`cloud_enrich`, `scripts/cloud_review.py`)** — runs a bounded, timeout-protected review after the meeting when explicitly enabled.
- **Post-meeting hook** — `sufler.post_meeting_hook` runs your custom command after processing finishes.

## Outside meetings

### Workspace, library and search

- **Unified macOS workspace (`app/`)** — single window with Today (recording capsule + prep screen), Meeting library (card feed with week calendar strip, status summary, search and meeting card actions: copy participant-safe summary, rename via UI or `scripts/rename_meeting.py`, retry, forget via `scripts/forget_meeting.py`), Memory (streaming archive Q&A with source provenance chips and answer history), Tasks (all graph checkboxes with search and due-date tracking) and Settings.
- **Archive Q&A and briefs (`src/brain.py`, `src/charoite_graph/graph_search.py`)** — hybrid lexical + semantic search (`bge-m3`, `scripts/graph_search_index.py`) with Russian morphology, CJK support, dossier-first lookup and 40%-per-source context budgeting.
- **Nightly cycle (`scripts/nightly.sh`)** — installed via `launchd` in one click from Settings: refreshes topic dossiers (`src/charoite_graph/dossier.py`, `scripts/nightly_dossier.py`, optional cloud review via `scripts/nightly_dossier_review.py`), runs Tier-3 semantic core revision (`src/tier3.py`, `src/nli.py`, `scripts/tier3_cores.py`), deduplicates graph/archive (`scripts/dedup_graph.py`, `scripts/dedup_archive.py`), checks graph health (`scripts/graph_doctor.py`), and prepares a morning brief (`scripts/morning_brief.py`).
- **Multiple chat engines (`src/llm.py`)** — `llm.engine: ollama` (default), MLX server, or opt-in `cloud` gateway (`sufler.cloud_engine: true`).
- **Claude Code MCP server** — exposes live status, transcript, hints, minutes and graph updates strictly within `CHAROITE_ROOT`.

### Dictation, notes and import

- **Global dictation (`⌥⌘D`)** — local STT pasted back into the originating app/window while restoring the clipboard (including images). Never pastes into password fields (detected via Accessibility); on macOS 26+ shows a non-focus-stealing live draft strip while GigaAM produces the final text.
- **Voice notes (`⌥⌘N`) and diary (`⌥⌘J`)** — quick spoken thoughts cleaned up by the local model and filed into the graph or personal diary.
- **Meeting import (`scripts/import_meeting.py`)** — imports audio (`m4a`/`wav`/`mp3`), transcripts (`txt`/`md`) or Zoom/Teams subtitles (`vtt`/`srt`) via CLI, the External recording tab, a watched folder, or the opt-in iPhone Voice Memos bridge (`audio.voice_memos_bridge`). Recording timestamps come from media metadata before file mtime.
- **Replacement dictionary (`sufler.vocabulary`)** — fixes recurring STT domain terms and proper names across all outputs.
- **Multilingual STT** — GigaAM (Russian), SenseVoice (Chinese and four other languages), plus Parakeet and Whisper from source.

### Mobile companions

- **iPhone companion (`app-ios/`)** — background-safe recording with Live Activity and Dynamic Island controls, call-interruption recovery, stalled-recording watchdog, iCloud Drive outbox queue with manual Share fallback, plus read-only Meetings feed and Tasks checkboxes.
- **Android companion (`app-android/`)** — foreground-service 16 kHz mono WAV recorder with an on-device queue, Meetings feed and Tasks checkboxes synced via any folder sync tool (zero network permissions).

### Setup, health and updates

- **Terminal-free setup** — bundled CPython runtime inside `Charoite.app`, RAM-sized model presets, one-click Ollama/model installation (`scripts/get_models.py`, `scripts/install_engine.py`), and pre-recording readiness checks (`scripts/doctor.py`).
- **Health rollup and macOS integration** — menu-bar icon summarizes recording, processing, Ollama and nightly health; holds idle sleep during recording; flags low disk, tight memory and Low Power Mode.
- **Fail-closed in-app updates** — checks GitHub releases (opt-out), verifies the maintainer's ed25519 manifest and Apple Developer ID signature, and refuses to restart while a recording is active.
- **Consistent UI system** — shared tokens and seven-role button scale across macOS and iOS (see [DESIGN.md](DESIGN.md)).

## Utilities (`scripts/`)

Maintenance, migration, diagnostics and benchmarking tools (full reference in [scripts/README.md](../scripts/README.md)):

| Script | What it does |
|---|---|
| `scripts/install_engine.py` | Installs an isolated diarization engine environment (e.g. `nemotron`) |
| `scripts/get_models.py` | Downloads and checksum-verifies STT and diarization ONNX models |
| `scripts/doctor.py` | Full readiness check (runtime, audio, Ollama, models, NLI, graph) |
| `scripts/graph_doctor.py` | Audits knowledge-graph integrity, links and placeholders |
| `scripts/dedup_graph.py` | Nightly de-duplication of graph files (run by `scripts/nightly.sh`) |
| `scripts/dedup_archive.py` | Cleans duplicate archive folders and conflict copies |
| `scripts/merge_graphs.py` | Merges two graph folders safely with redirect handling |
| `scripts/migrate_placeholders.py` | Migrates legacy neutral speaker placeholders |
| `scripts/fix_action_items.py` | Normalizes legacy minutes assignments into `- [ ]` checkboxes |
| `scripts/forget_meeting.py` | Removes a meeting from transcripts, archive, recordings and graph backups |
| `scripts/rename_meeting.py` | Renames a meeting across transcript, sidecar, archive and graph |
| `scripts/cloud_review.py` | Bounded cloud review of a meeting (called by the graph pipeline) |
| `scripts/decision_evidence_audit.py` | Read-only audit of exported decision quotes and replacement links |
| `scripts/memory_bench.py` | Validates end-to-end retrieval and answer quality (`--demo`, `--demo-en`, `--demo-zh`) |
| `scripts/memory_case_suite.py` | Validates frozen demo memory questions and exports lexical retrieval |
| `scripts/retrieval_compare.py` | Compares ranked retrieval runs offline on frozen judgments |
| `scripts/runtime_profile.py` | Profiles local LLM runtimes (Ollama, MLX) through Charoite's streaming transport |
| `scripts/bench_models.py` | Benchmarks models on Charoite's scenarios |
| `scripts/stt_bench.py` | Benchmarks local STT backends |
| `scripts/diar_bench.py` | Computes diarization error rate (DER) on synthetic or real audio |
| `scripts/gate_bench.py` | Evaluates the shadow decision gate |
| `scripts/nemotron_shadow_replay.py` | Replays a recording through the streaming Nemotron shadow |
| `scripts/nemotron_shadow_check.py` | Compares streaming Nemotron shadow logs with final meeting labels |

## Document format

All outputs are plain markdown readable without special rendering: bold-keyed lists instead of wide tables, short blocks, consistent sections and bottom-line-up-front (BLUF) ordering: Summary (1 min) → Minutes → Debrief → Transcript.
