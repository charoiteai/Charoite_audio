# Charoite

**Fully local AI meeting assistant with speaker diarization and a self-updating knowledge graph. Nothing ever leaves your Mac.**

Charoite listens to your meetings (microphone + system audio, no bots joining calls), transcribes them locally, tells speakers apart, answers questions mid-meeting, and after each meeting builds an Obsidian knowledge graph that remembers people, systems, decisions and recurring topics — across all your meetings.

*Русская документация: [README.ru.md](README.ru.md). Charoite is Russian-first today (GigaAM STT is SOTA for Russian); English STT works via Whisper/Parakeet backends.*

## Why Charoite

- **100% local by default.** Audio, transcription, diarization, LLM summaries — all on your machine (Ollama + ONNX). No cloud, no telemetry, no accounts. The optional Claude layer is off unless you turn it on.
- **Speaker diarization that ships.** Live "Speaker 1/2/…" labels during the meeting, plus an offline re-pass over the full recording after the meeting for clean paragraphs per speaker. Names are assigned automatically when someone introduces themselves — never guessed.
- **A knowledge graph, not a pile of notes.** Meetings become episodes; people, systems and decisions become nodes; recurring topics become "Cores" with status and history. During a meeting Charoite whispers "⏮ this was discussed on Jul 15, status was …".
- **Layered output per meeting**: one-minute Summary (with links to what changed since past meetings) → Minutes → Debrief → full Transcript. Read as deep as you need.
- **Real-time help**: instant local answer when the other side asks you a question (⚡), auto-theses, live draft minutes, voice notes and dictation.

## Requirements

- Apple Silicon Mac (M1 or newer), 32 GB RAM recommended for the default models
- [Ollama](https://ollama.com) with `qwen3.6:35b-a3b` and `gemma4:latest` (or edit `config` to use smaller models)
- Python 3.11+
- Optional: [BlackHole](https://existential.audio/blackhole/) to capture system audio (calls), [Obsidian](https://obsidian.md) to browse the graph

## Quick start

```bash
git clone https://github.com/charoiteai/charoite && cd charoite
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml   # then set user_name & graph_dir
.venv/bin/python src/main.py                        # CLI mode: live transcript + hints
```

STT models download automatically on first run (GigaAM via `onnx_asr`). For live diarization put an ERes2Net speaker-embedding ONNX model at `models/diar/embedding.onnx` (see [docs/DIARIZATION.md](docs/DIARIZATION.md)).

## Privacy

See [PRIVACY.md](PRIVACY.md). Short version: no telemetry, no network calls except to your own localhost services (Ollama) — verify it yourself, it's all here. Recordings auto-delete after `record_keep_days`. Voice embeddings live only in RAM during a meeting; no voice prints are stored.

## Status

Public beta. Issues and feedback welcome. Roadmap: menu-bar macOS app, English-first prompts, speaker enrollment across meetings (voice → person node), packaged graph viewer.

## License

Apache-2.0.
