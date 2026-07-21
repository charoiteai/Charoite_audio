# Setup

## 1. Dependencies

```bash
brew install ollama
ollama pull qwen3.6:35b-a3b && ollama pull qwen3.5:4b && ollama pull gemma4:latest

git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
```

On 16 GB RAM pick lighter models — presets are in the config comments
and [MODELS.md](MODELS.md).

## 2. Config: two required fields

In `config/config.yaml`:

- `sufler.user_name` — your name: labels your microphone in the transcript
  and is never assigned to another voice.
- `sufler.graph_dir` — knowledge-graph folder (empty = graph off,
  transcription still works). Point it inside your Obsidian vault, e.g.
  `~/Documents/Obsidian/Work` — Charoite creates the structure itself.

Also worth filling: `sufler.user_context` (1-2 sentences about your work) —
context for instant answers.

## 3. System audio (calls) — BlackHole

Without it Charoite hears only the mic (in-person meetings work as-is).
For calls: install [BlackHole 2ch](https://existential.audio/blackhole/),
create a Multi-Output Device (speakers + BlackHole) in Audio MIDI Setup and
set it as the system output. Separate channels give you free you/them
diarization and echo filtering.

## 4. macOS permissions

- **Microphone** — requested on first run.
- **Universal Access** (optional) — only for dictation auto-paste; without
  it the text simply stays in the clipboard.

## 5. Voice diarization (optional)

Put an ERes2Net embedding model at `models/diar/embedding.onnx` — see
[DIARIZATION.md](DIARIZATION.md). Without it labels are per-channel
(you/them), with it — per voice ("Speaker 1/2/…").

## 6. Run

```bash
.venv/bin/python src/main.py     # CLI: live transcript + hints
.venv/bin/python src/daemon.py   # daemon for UI integration (NDJSON)
```

First run downloads the STT model (~1 min).

## Troubleshooting

- **Empty transcript** — check inputs: `python -c "import sounddevice as sd; print(sd.query_devices())"`.
- **Slow answers** — `ollama ps`: the model must stay in RAM; keep
  `num_ctx: 8192` in the config.
- **No system audio** — macOS output must be the Multi-Output device.
