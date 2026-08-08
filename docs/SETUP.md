# Setup

***English** · [Русский](ru/SETUP.md) · [中文](zh/SETUP.md)*

## 1. Dependencies

**From the prebuilt app (recommended).** Charoite.app from the
[releases](https://github.com/charoiteai/Charoite_audio/releases) ships a
python runtime inside: no git clone, no venv, no pip. All you need is the
language model — Ollama:

```bash
brew install ollama
ollama pull qwen3.6:35b-a3b && ollama pull qwen3.5:4b && ollama pull gemma4:latest
```

Everything else the app asks for and does itself: your name and graph folder in
the first-run wizard, the voice-separation model with a button, permissions
through system dialogs.

**From source** (development, custom build, non-Apple-Silicon):

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install .
cp config/config.example.yaml config/config.yaml
```

The app uses the embedded runtime when present and the `.venv` next to the
repository otherwise. To build a bundle with the runtime yourself:
`scripts/build_embedded_python.sh && app/make_app.sh`.

Which models exactly — the app suggests itself: the first-run wizard reads
your machine's memory and shows three ready sets ("Full", "Balanced",
"Light") with the recommended one marked, writes the chosen one into the
config and downloads it with a single button. Model details are in
[MODELS.md](MODELS.md).

## 2. Config: two required fields

**The easy way is in the app.** The first-run wizard asks for your name and
graph folder and writes them into `config/config.yaml` itself; the folder is
picked from a panel. Editing the file by hand, below, is for installs without
the interface.

In `config/config.yaml`:

- `sufler.user_name` — your name: labels your microphone in the transcript
  and is never assigned to another voice.
- `sufler.graph_dir` — knowledge-graph folder (empty = graph off,
  transcription still works). Point it inside your Obsidian vault, e.g.
  `~/Documents/Obsidian/Work` — Charoite creates the structure itself.

Also worth filling: `sufler.user_context` (1-2 sentences about your work) —
context for instant answers.

## 3. System audio (calls) — nothing to do

The app captures meeting audio with macOS itself (ScreenCaptureKit). On the
first recording the system asks once for "Screen & System Audio Recording" —
press Allow. That's it: no drivers, no Audio MIDI Setup, no switching the
output device. Sound keeps going to your speakers as usual, and on macOS 15+
the microphone arrives in the same stream.

Separate channels give free "you / the other side" diarization and echo
filtering.

**Fallback — BlackHole** (macOS before 13, or permission denied):

1. Install [BlackHole 2ch](https://existential.audio/blackhole/).
2. Audio MIDI Setup → "+" → Multi-Output Device → tick speakers AND BlackHole.
3. System output → that Multi-Output (you hear sound, Charoite gets it too).

Charoite picks the source itself: ScreenCaptureKit first, then BlackHole. The
meeting status shows which channel is in use.

## 4. macOS permissions

- **Microphone** — requested on first run.
- **Screen & System Audio Recording** — requested on the first meeting
  recording; without it only the microphone is heard (or BlackHole, if you
  set it up).
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

The first successful recording should end in a meeting card, not merely in a
transcript file. Follow the end-to-end check in the
[practical user guide](USER_GUIDE.md). A complete map of temporary audio,
transcripts, graph documents and retention lives in
[Data and recovery](DATA_AND_RECOVERY.md).

## 7. Where things live

- `transcripts/` — transcripts and the meeting's working files
- `recordings/` — full recordings (auto-deleted after `record_keep_days`)
- `<graph_dir>/Встречи-архив/` — a "date — title" folder per meeting:
  summary, minutes, transcript, questions and answers, debrief

This is the short map. Wherever retention, the source of truth or the recovery
order after a failure matter, use the
[full data map](DATA_AND_RECOVERY.md).

## Troubleshooting

- **Empty transcript** — check inputs: `python -c "import sounddevice as sd; print(sd.query_devices())"`.
- **Slow answers** — `ollama ps`: the model must stay in RAM; keep
  `num_ctx: 8192` in the config.
- **No system audio** — check the permission: System Settings → Privacy →
  Screen & System Audio Recording, Charoite must be listed and enabled. After
  an app update the permission sometimes has to be re-granted: untick and tick
  it again. If you use BlackHole as the fallback, the macOS output must be the
  Multi-Output device rather than the speakers directly.

## Semantic search (recommended)

The app's archive search adds a semantic layer when the `bge-m3`
embedding model is available in Ollama:

```bash
ollama pull bge-m3   # ~1.2 GB; without it search is lexical-only
```

The index builds in the background on first search and updates
incrementally as the graph changes (stored in
`~/Library/Application Support/Charoite/semantic_index_v2.bin`).

## Diagnosis

`python3 scripts/doctor.py` checks Python, dependencies, config keys, the graph folder, Ollama and its models (incl. `bge-m3`), and diarization — with an exact fix for every problem.

The second half of the report is about running, not installing: whether the model answers a **generation** probe (a stalled Ollama returns its model list instantly while inference sits still — that difference is the only way to tell them apart), whether any meetings got stuck on the way to the graph, how many files wait in the import folder, and how much disk is left. Any "Charoite is silent" starts here.

The doctor is the one script that runs under any Python: it is written without
dependencies so that it can answer *before* they are installed. Everything else
runs via `.venv/bin/python` — and if you start it with the system Python, the
answer is a one-line recipe instead of a traceback (`src/deps.py`).

## Night cycle (optional)

`scripts/nightly.sh` keeps the graph tidy while you sleep: Tier-3 core
revision (duplicates, merges — with backups), the morning brief
`_Сегодня.md` (ready-made context for the day), and the memory bench
(quality regression signal). Schedule it with launchd:

```xml
<!-- ~/Library/LaunchAgents/ai.charoite.nightly.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.charoite.nightly</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>/PATH/TO/Charoite_audio/scripts/nightly.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>/tmp/charoite_nightly.log</string>
  <key>StandardErrorPath</key><string>/tmp/charoite_nightly.log</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/ai.charoite.nightly.plist
```
