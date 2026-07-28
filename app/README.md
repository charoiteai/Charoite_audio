# Charoite.app — the macOS companion app

*[**English**] · [Русский](README.ru.md) · [中文](README.zh.md)*

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

1. Install Charoite_audio itself (the repository root): venv, models,
   `config/config.yaml` — see the root README.
2. Launch the app → Settings (⌘,):
   - **Charoite_audio folder** — where you cloned the repository
     (default `~/Charoite_audio`);
   - **Ollama** — server address (default `http://localhost:11434`);
   - the "Check" button verifies the daemon, Ollama and the graph.
3. The graph path is read from `config/config.yaml` (`sufler.graph_dir`)
   — configured once for the daemon, the app picks it up.

On the first "Listen to meeting" macOS asks for microphone access; for
dictation auto-insert into the active field grant the app Accessibility
rights.

## What lives where

- `Sources/CharoiteApp/Views/Sufler` — the main window: transcript,
  theses/hint/Claude panes, archive questions and briefs.
- `Sources/CharoiteApp/Views/LocalChat` — chat with a local model; the
  "Memory" toggle mixes in graph findings (file search, no servers).
- `Sources/CharoiteApp/Services` — the daemon bridge (NDJSON
  stdin/stdout, watchdog, auto-restart), dictation, local graph search.

## Windows

- **Copilot** — transcript, theses, hints, archive answers, briefs.
- **Meeting tasks** — every graph checkbox in one list, ticking writes
  straight into the markdown; open-count badge in the toolbar.
- **Chat with memory** — a local model + graph facts; live model list
  from Ollama; markdown in bubbles; one-button answer copy.
- Archive answers stream token by token; past session questions collapse
  under the current answer; a good answer saves into the graph as a note
  with one button.

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
