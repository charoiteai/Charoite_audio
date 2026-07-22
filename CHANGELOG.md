# Changelog

All notable changes to Charoite are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-07-22

### Fixed

- **Explicit context window (`num_ctx`) for every LLM call.** Graph extraction,
  the post-meeting debrief and the MCP minutes tool were calling Ollama without
  it, so the model loaded with the (very large) context from its Modelfile,
  bloating the KV cache and swapping on 16–32 GB machines.
- **Minutes no longer pull a second heavy model.** The MCP minutes tool had a
  hardcoded 17 GB model that could not run alongside the resident one on 16 GB;
  it now uses the model from the config.
- **Speaker naming: "the name is a vocative, not the speaker".** The guard that
  prevents *"Sam, what do you think?"* from labelling the **current** speaker as
  Sam compared against a line format that never matches the transcript tail, so
  it never fired.
- **Name parsing no longer drops every name.** A greedy `{...}` match glued two
  JSON objects together and failed to parse; the last flat object is used now.
- **Summary history takes the newest events.** The per-topic chronicle is written
  newest-first, so taking the last three entries fed the summary the *oldest*
  context instead of what happened most recently.

## [0.1.0] - 2026-07-21

Initial public release. A fully local AI meeting assistant for macOS on Apple
Silicon — audio, transcription, diarization and LLM summaries all run on your
machine. Nothing leaves the Mac by default.

### Added

- **Fully local pipeline.** Speech-to-text (GigaAM via ONNX), diarization
  (ERes2Net embeddings) and summaries/graph (Qwen via Ollama) run on-device.
  No cloud, no telemetry, no accounts.
- **Speaker diarization that ships.** Live `Speaker 1/2/…` labels during the
  meeting, plus an offline re-pass over the full recording afterwards for clean
  per-speaker paragraphs. Names are filled in only when someone introduces
  themselves — never guessed.
- **Self-updating knowledge graph.** Meetings become episodes; people, systems
  and decisions become nodes; recurring topics become "Cores" with status and
  history. During a meeting Charoite surfaces past context: *"⏮ this was
  discussed on <date>, status was …"*.
- **Layered output per meeting.** One-minute Summary (with links to what changed
  since previous meetings) → Minutes → Debrief → full Transcript. Read as deep
  as you need.
- **Real-time assistance.** Instant local answer when the other side asks you a
  question (⚡), auto-theses, live draft minutes, voice notes and dictation.
- **Optional cloud layer.** A deeper post-meeting analysis via an external
  provider exists in the code but is **off by default** and clearly documented.
  Leave it off and the product stays 100% offline.
- **Privacy by architecture.** All network calls go to `localhost` only; voice
  embeddings used to tell speakers apart live in RAM for the duration of the
  meeting and are never written to disk. Verifiable with Wireshark or LuLu.
- **Explicit model context sizing** (`num_ctx`) across LLM calls to keep the
  local KV-cache small and inference fast on 16–32 GB machines.

### Requirements

- macOS 14+ on Apple Silicon (M1 or newer), 16 GB+ unified memory (32 GB ideal),
  Ollama with the documented models pulled.

### Known limitations

- Terminal / command-line workflow for now; a one-click macOS app is planned.
- Prompts are Russian-first; English prompts for a wider audience are on the
  roadmap.
- Cross-meeting voice recognition (binding a voice to a person node
  automatically) is not implemented yet.

[0.1.0]: https://github.com/charoiteai/Charoite_audio/releases/tag/v0.1.0
