# Charoite.app — the macOS companion app

*[**English**] · [Русский](../docs/ru/app/README.md) · [中文](../docs/zh/app/README.md)*

A native SwiftUI shell over the Charoite Python daemon: live transcript
with diarization, thesis cards, hints and the Claude pane, archive
questions and briefs, local chat with graph memory, dictation (⌥⌘D) and
voice notes (⌥⌘N), menu bar. Everything local — just like the daemon.

## Build

```bash
cd app
./make_app.sh          # swift build -c release + bundle + ad-hoc signing
open build/Charoite.app
```

Requirements: macOS 14+, Xcode Command Line Tools (`xcode-select --install`).

## First-time setup

1. Install Ollama and let the first-run wizard do the rest: it asks for your
   name and graph folder, offers the model set that fits this Mac's memory
   and installs it. The python runtime and the daemon's code travel inside
   the bundle — no `git clone`, no venv, no `pip`.
2. Settings (⌘,) if you need to change something:
   - **Charoite_audio folder** — only when you run from sources: point it at
     the cloned repository and the app runs that code instead of its own;
   - **Ollama** — server address (default `http://localhost:11434`);
   - the "Check" button verifies the daemon, Ollama and the graph.
3. The graph path lives in `config/config.yaml` (`sufler.graph_dir`) inside
   your working folder — the wizard writes it, the daemon reads it.

On the first "Listen to meeting" macOS asks for microphone access; for
dictation auto-insert into the active field grant the app Accessibility
rights.

The first recording is an end-to-end check: the timer must advance, both sides
must appear in the transcript, Stop must progress through actual processing
stages, and the run must finish in a result card. See the
[practical user guide](../docs/USER_GUIDE.md) for the complete workflow and
failure recovery.

## What lives where

- `Sources/CharoiteApp/Views/Sufler` — the main window: transcript,
  theses/hint/Claude panes, archive questions and briefs.
- `Sources/CharoiteApp/Views/LocalChat` — chat with a local model; the
  "Memory" toggle mixes in graph findings (file search, no servers).
- `Sources/CharoiteApp/Services` — the daemon bridge (NDJSON
  stdin/stdout, watchdog, auto-restart), dictation, local graph search.

## Windows

- **Copilot** — transcript, a growing meeting thread, hints, archive answers,
  briefs, a recording timer and honest post-meeting processing stages.
- **Recent meetings** — up to 20 runs from the last 14 days, with state,
  transcript access and retry for a failed run. It is a status history, not a
  replacement for the full graph archive.
- **Meeting card** — title, date, duration, participants, gist, decisions and
  action items without leaving the app; copy actions, transcript and Obsidian
  links, plus coherent renaming across meeting files.
- **Meeting tasks** — every graph checkbox in one list, ticking writes
  straight into the markdown; open-count badge in the toolbar.
- **Chat with memory** — a local model + graph facts; live model list
  from Ollama; markdown in bubbles; one-button answer copy.
- Archive answers stream token by token; past session questions collapse
  under the current answer; a good answer saves into the graph as a note
  with one button.

The menu bar remains useful with the main window closed: it shows the recording
timer, processing, ready and failed states, and exposes Start/Stop, the latest
result, Retry and Recent meetings. Storage locations and retention are covered
in [Data and recovery](../docs/DATA_AND_RECOVERY.md).

## Archive search (v2)

Questions and briefs are ranked properly: Russian stemming, IDF (a rare
query word weighs more), query coverage, file freshness (date in the
name), graph distillates prioritized over raw transcripts, result
diversity (one meeting can't take every slot). Weak matches are flagged
"⚠ the archive may not contain this" — the model won't invent an answer
from irrelevant chunks. Sources in the answer are clickable — they open
in Obsidian.

The semantic layer (bge-m3 via your Ollama) works in the built-in search
too: the index builds in the background and refreshes by mtime (see
docs/SETUP — `ollama pull bge-m3`). If the optional brain server is up
(port 8100), search goes there; with neither — pure lexical.

The app opens no network connections except localhost: your Ollama, the
optional brain companion (:8100) and the copilot daemon.
