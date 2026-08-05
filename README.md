# Charoite

[![CI](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml) [![swift-tests](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml) [![CodeQL](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml) ![License](https://img.shields.io/badge/license-Apache--2.0-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey) ![Local](https://img.shields.io/badge/cloud-none%20by%20default-success) [![Release](https://img.shields.io/github/v/release/charoiteai/Charoite_audio)](https://github.com/charoiteai/Charoite_audio/releases)

**Fully local AI meeting assistant with speaker diarization and a self-updating knowledge graph. Nothing ever leaves your Mac.**

Charoite listens to your meetings (microphone + system audio, no bots joining calls), transcribes them locally, tells speakers apart, answers questions mid-meeting, and after each meeting builds an Obsidian knowledge graph that remembers people, systems, decisions and recurring topics — across all your meetings.

*[Русский](docs/ru/README.md) · [中文](docs/zh/README.md). Charoite is Russian-first today (GigaAM STT is SOTA for Russian); English works via Parakeet/Whisper, Chinese via Whisper — and Qwen, the default LLM, is native in Chinese.*

> **[Manifesto: local-first, not local-only](MANIFESTO.md)** — what months on real
> meetings taught us about where local models shine and why a strong model
> runs the knowledge graph. Numbers, boundaries, and the test that would
> prove us wrong.

![Charoite app — archive answers with sources, English UI](docs/img/app-main-en.png)

![Meeting tasks — every graph checkbox in one window](docs/img/app-tasks-en.png)

## Why Charoite

- **100% local by default.** Audio, transcription, diarization, LLM summaries — all on your machine (Ollama + ONNX). No cloud, no telemetry, no accounts. The optional Claude layer is off unless you turn it on.
- **Speaker diarization that ships.** Live "Speaker 1/2/…" labels during the meeting, plus an offline re-pass over the full recording after the meeting for clean paragraphs per speaker. Names are assigned automatically when someone introduces themselves — never guessed.
- **A knowledge graph, not a pile of notes.** Meetings become episodes; people, systems and decisions become nodes; recurring topics become "Cores" with status and history. During a meeting Charoite whispers "⏮ this was discussed on Jul 15, status was …".
- **Topic dossiers.** At night a local model builds a summary per cross-cutting topic: current state, chronology, decisions, open questions, who is involved — each point linked to its source node. Search consults the dossier index **first**, so "where does this topic stand" is answered by a written summary instead of a dozen scattered fragments. Rebuilds are incremental: a dossier carries a fingerprint of its sources, and an unchanged topic never wakes the model. Hand-written additions live in the "Author edits" section and survive rebuilds.
- **Cloud models — optional, at every step.** All of them are off by default and enabled one by one: post-meeting review (`cloud_enrich`), a second opinion at conversation speed (`cloud_live`), dossier review with the right to edit (`cloud_edit_graph`). They run on a subscription, with no API key in the environment. What leaves for the cloud and what never does — see [PRIVACY.md](PRIVACY.md).
- **Layered output per meeting**: one-minute Summary (with links to what changed since past meetings) → Minutes → Debrief → full Transcript. Read as deep as you need.
- **Real-time help**: instant local answer when the other side asks you a question (⚡), auto-theses, live draft minutes, voice notes and dictation.

## How it compares

|  | Cloud meeting assistants | Local transcribers | **Charoite** |
|---|:---:|:---:|:---:|
| Audio stays on your machine | ✗ | ✓ | ✓ |
| No bot joins the call | sometimes | ✓ | ✓ |
| Speaker diarization | ✓ | rarely | ✓ |
| Live hints during the meeting | ✓ | ✗ | ✓ |
| Memory across meetings (graph) | ✗ | ✗ | ✓ |
| Works fully offline | ✗ | ✓ | ✓ |
| Open source | rarely | sometimes | ✓ |

Cloud assistants are smart but your conversations live on someone else's
servers. Local transcribers keep audio private but forget each meeting the
moment it ends. Charoite keeps both promises: everything stays on the Mac,
and every meeting makes the next one smarter.

## Requirements

- Apple Silicon Mac (M1 or newer), 32 GB RAM recommended for the default models
- [Ollama](https://ollama.com) — see the RAM table below for which models fit
- Python 3.11+
- Optional: [BlackHole](https://existential.audio/blackhole/) to capture system audio (calls), [Obsidian](https://obsidian.md) to browse the graph

## Which models for your RAM

Everything runs locally. STT (~1 GB) and diarization (~0.5 GB) are constant;
the LLMs are what scale with memory. `num_ctx: 8192` throughout.
Semantic search adds `bge-m3` (~1.2 GB) — recommended at 16 GB+.

| RAM | Main LLM | Light LLM | Graph | Notes |
|----|----|----|----|----|
| **8 GB** | `qwen3.5:4b` | same | no | Transcript, theses, minutes, basic suggestions |
| **16 GB** | `gemma4:latest` | `qwen3.5:2b` | slow | Full live loop — recommended entry point |
| **32 GB** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | yes | The default config, benchmarked here |
| **64 GB+** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | yes | Headroom for the cloud Claude layer + long meetings |

Below 16 GB the knowledge graph is off (sub-30B models break the JSON schema);
on 4 GB run STT only and point `llm.base_url` at another machine.

**iOS/iPadOS**: the phone does STT + light generation, anything heavier goes to
a Mac over the REST API. On iOS 26+ the built-in ~3B Foundation Models handle
theses for free. Full macOS/iOS tables and the reasoning: [docs/MODELS.md](docs/MODELS.md).

## Quick start

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install .
cp config/config.example.yaml config/config.yaml   # then set user_name & graph_dir
```

Working in English or Chinese? Use a language preset instead — English gets
Parakeet STT (English SOTA on Apple Silicon), Chinese gets Whisper STT with
Qwen (native Chinese) as the LLM, both get meeting documents and a copilot
role in your language:

```bash
cp config/config.example.en.yaml config/config.yaml   # English
cp config/config.example.zh.yaml config/config.yaml   # 中文
```

Something not working? One command shows what's missing and how to fix it:

```bash
python3 scripts/doctor.py
```

**Option A — the macOS app (recommended):**

Download `Charoite.app.zip` from the [latest release](https://github.com/charoiteai/Charoite_audio/releases/latest)
(the bundle is ad-hoc signed, not notarized — on first launch macOS blocks it:
run `xattr -d com.apple.quarantine /Applications/Charoite.app`, or open
System Settings → Privacy & Security and press *Open Anyway*. Right-click → Open
no longer works on macOS 15+), or build it yourself:

```bash
./app/make_app.sh && open app/build/Charoite.app
```

Live transcript, theses and hints, archive questions and briefs, local
chat with graph memory, dictation (⌥⌘D) and voice notes (⌥⌘N).
On the first launch the app checks the actual meeting path before offering to
record: `.venv`, daemon and config, Python dependencies, microphone and audio
inputs, Ollama models and the graph folder. Missing BlackHole, `bge-m3` or an
optional graph is shown as a limitation, not confused with a fatal failure.

**Option B — CLI:**

```bash
.venv/bin/python src/main.py     # live transcript + hints in the terminal
```

**No meetings yet?** Point `graph_dir` at the bundled [demo graph](demo)
(Russian) or [demo/graph_en](demo) (English) and ask
«что решили по платёжному провайдеру?» / "what did we decide about the
payment provider?" — see the product working before recording anything.
One command validates the whole retrieval loop: `.venv/bin/python scripts/memory_bench.py --demo`.
Got old recordings? One command imports a meeting file (audio/text/Zoom-subtitles) into the archive and the graph: `.venv/bin/python scripts/import_meeting.py file --date 2026-07-15`. Or point the app at an import folder (Settings → Import) — recordings dropped there become meetings on their own. A replacement dictionary (`sufler.vocabulary`) fixes terms the STT keeps mangling, everywhere at once.

STT models download automatically on first run (GigaAM via `onnx_asr`). Live diarization ("Speaker 1/2/…" per voice) takes one command:

```bash
.venv/bin/python scripts/get_models.py --diar    # model choices: --list
```

Without it Charoite still works, but labels follow channels (you vs. the other side) and the daemon says so when the meeting starts. Details in [docs/DIARIZATION.md](docs/DIARIZATION.md).

## iPhone companion (app-ios/)

The phone is the microphone on the table, the Mac stays the brain. The
SwiftUI companion ([app-ios/](app-ios)) records meetings, voice notes
and diary entries (background-safe, with a Live Activity timer in the
Dynamic Island), drops files into a user-chosen iCloud Drive folder with
an on-device outbox queue — and reads the graph back: a meetings feed
and task checkboxes straight from the same markdown files Obsidian and
the Mac app see. Build with XcodeGen: `cd app-ios && xcodegen generate`,
then open `CharoiteiOS.xcodeproj`.

## Android companion (app-android/)

The same role for a tablet or an Android phone
([app-android/](app-android)): background recording through a foreground
service, an on-device queue, the meetings feed and task checkboxes from
the same markdown files. Recordings are written as 16 kHz mono WAV —
exactly what recognition needs, and a format that survives a crash —
and land in a folder you pick once; the Mac sees that folder through
Syncthing or any sync tool. The app holds no network permissions at all.
Build: `cd app-android && ./gradlew assembleDebug`.

## Documentation

- [Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md)

- [Manifesto](MANIFESTO.md) — why local models run the conveyor and a strong model runs the graph
- [Setup](docs/SETUP.md) — install, BlackHole for calls, permissions, first run
- [Practical user guide](docs/USER_GUIDE.md) — the complete path from readiness to a result card and retry
- [Data and recovery](docs/DATA_AND_RECOVERY.md) — storage, retention, backups and safe recovery
- [Features](docs/FEATURES.md) — everything Charoite does, live and post-meeting
- [Architecture](docs/ARCHITECTURE.md) — the daemon, two-pass diarization, graph pipeline
- [Models](docs/MODELS.md) — why these defaults, with benchmarks; **RAM presets for macOS (4/8/16/32 GB) and iOS**
- [Diarization](docs/DIARIZATION.md) — embedding model setup and tuning
- [Design](docs/DESIGN.md) — shared tokens and UI conventions for macOS and iOS

## Privacy

See [PRIVACY.md](PRIVACY.md). Short version: no telemetry, no network calls except to your own localhost services (Ollama) — verify it yourself, it's all here. Recordings auto-delete after `record_keep_days`. Voice embeddings live only in RAM during a meeting; no voice prints are stored.

## Status

Public beta — the current version is on the [Releases page](https://github.com/charoiteai/Charoite_audio/releases/latest). Issues and feedback welcome; questions go to [Discussions](https://github.com/charoiteai/Charoite_audio/discussions). The native macOS app lives in [app/](app) — build with `app/make_app.sh`; the iPhone companion is in [app-ios/](app-ios) and the Android one in [app-android/](app-android). Roadmap in [ROADMAP.md](ROADMAP.md): non-Russian result-card parsing, iOS localization, direct Wi-Fi delivery from phones and a packaged graph viewer.

If Charoite is useful to you, a ⭐ helps other people find it.

## License

Apache-2.0.
