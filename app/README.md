# Charoite.app — the macOS companion app

*[**English**] · [Русский](../docs/ru/app/README.md) · [中文](../docs/zh/app/README.md)*

A native SwiftUI shell over the Charoite Python daemon: live transcript
with diarization, the meeting thread, hints and the Claude pane, meeting
prep and briefs, a meeting library with result cards, import of outside
recordings, tasks, chat with graph memory, dictation (⌥⌘D), voice notes
(⌥⌘N) and a diary (⌥⌘J), menu bar. Local by default — just like the
daemon; the few things that can leave the Mac are listed at the end.

## Build

```bash
cd app
./make_app.sh          # swift build -c release (arm64) + bundle + signing
open build/Charoite.app
```

Requirements: macOS 14+ on Apple Silicon, Xcode Command Line Tools
(`xcode-select --install`).

Signing: Developer ID Application with the hardened runtime and a timestamp
when such a certificate is in the keychain (or named in
`CHAROITE_SIGN_IDENTITY`; `-` forces ad-hoc), otherwise ad-hoc. With an ad-hoc
signature macOS treats every rebuild as a new app: microphone and
screen-recording permissions have to be granted again, and the first launch
needs "Open Anyway". Under the hardened runtime the daemon does not inherit
the app's microphone access, so the embedded interpreter is signed with its
own entitlements (`Resources/entitlements/embedded-python.entitlements`).

The bundle carries the daemon code (`src/`, `scripts/`,
`config/config.example.yaml`) and, when `scripts/build_embedded_python.sh`
was run first, the portable python runtime; without it the app runs the
`.venv` in its data folder, as a clone install does. The version comes from
the latest git tag, the build number from the commit count. Release builds
(DMG, notarization, the signed update manifest) are described in
[Releasing](../docs/RELEASING.md).

Tests: `swift test --filter '^CharoiteAppTests\.'` in `app/`. `Probes/` holds
live probes against a real graph and Ollama; they skip unless their
environment flags are set and are run only by hand.

## First-time setup

1. Open the app and let the first-run wizard do the rest: it installs or
   starts Ollama (through Homebrew when it is there, otherwise it opens the
   download page), asks for your name and graph folder, offers the model set
   that fits this Mac's memory and pulls it, and offers the ~80 MB model that
   tells speakers apart. The python runtime and the daemon's code travel
   inside the bundle — no `git clone`, no venv, no `pip`.
2. Settings (⌘,) if you need to change something:
   - **Data folder** — where `config/config.yaml`, transcripts, recordings
     and models live; by default `~/Library/Application Support/Charoite`.
     When the chosen folder is a clone with `src/daemon.py`, a separate
     toggle — "Run the daemon code from this folder (development)" — runs that
     code instead of the signed bundle's;
   - **Ollama** — server address (default `http://localhost:11434`); an
     address that is not this Mac is refused, and the refusal is shown, unless
     `llm.allow_remote: true` is set in the config;
   - the "Check" button verifies the daemon, Ollama, bge-m3 and the graph;
   - the nightly cycle (a launchd job at 04:15), the import folder, the
     calendar and "What leaves this Mac" live here too.
3. The graph path lives in `config/config.yaml` (`sufler.graph_dir`) inside
   the data folder — the wizard writes it, the daemon reads it. A relative
   path is resolved from the data folder.

On the first "Listen to meeting" macOS asks for microphone access. The other
side of a call is captured through ScreenCaptureKit: grant "Screen & System
Audio Recording" and restart the app — without it only your microphone is
recorded. For dictation auto-insert into the active field grant the app
Accessibility rights. Calendar access is optional and reads only event titles
and times.

The first recording is an end-to-end check: the timer must advance, both sides
must appear in the transcript, Stop must progress through actual processing
stages, and the run must finish in a result card. See the
[practical user guide](../docs/USER_GUIDE.md) for the complete workflow and
failure recovery.

## What lives where

- `Sources/CharoiteApp/App` — scenes (the main window, the "Chat with memory"
  window, the menu bar, Settings), the `charoite://` URL handler and
  navigation between sections.
- `Sources/CharoiteApp/Views/Workspace` — the main window: sidebar, Today,
  the meeting library, External recording.
- `Sources/CharoiteApp/Views/Sufler` — the Meeting section (transcript,
  thread, hints, Claude pane) and the first-run wizard.
- `Sources/CharoiteApp/Views/Meetings`, `Prep`, `Tasks`, `LocalChat`,
  `MenuBar`, `Settings`, `Dictation` — the meeting card, meeting prep, tasks,
  Memory, the menu bar, Settings and the dictation preview.
- `Sources/CharoiteApp/Services` — the daemon bridge (NDJSON stdin/stdout,
  watchdog, auto-restart), system-audio capture, the recording lifecycle and
  processing status, dictation, local graph search and the semantic index,
  import, calendar, updates.
- `Sources/CharoiteApp/L10n.swift` — UI strings in Russian, English and
  Chinese.

## Main window and menu bar

One window with a sidebar; the menu bar, the meeting card and prep switch
sections instead of opening windows of their own.

- **Today** — a record button with readiness, the state of the current
  meeting (recording, processing, result ready), prep for the next calendar
  event (earlier meetings on the topic, open action items, the last meeting
  on it), the latest results, whether last night's processing ran and which
  version is running, the update offer.
- **Meeting** — transcript, a growing meeting thread, hints and instant
  answers to the other side's questions, Digest (⌘⏎) and Minutes on demand,
  questions about this meeting and the graph, the opt-in Claude layer (marked
  "leaves this Mac"), a recording timer and honest post-meeting processing
  stages; chat with memory opens as a side panel.
- **Meetings** — the library: cards by day with state, duration, participants
  and action items, a week strip, search over summaries, minutes, analyses
  and graph notes. The meeting card beside it shows title, date, duration,
  participants, gist, decisions and action items at four depths (Summary ·
  Minutes · Analysis · Transcript), with copy actions, transcript and Obsidian
  links, coherent renaming across meeting files and retry for a failed run.
  The feed is a status history of up to 20 runs from the last 14 days, not a
  replacement for the full graph archive.
- **External recording** — drop a phone recording, someone else's call
  recording or a Zoom export: the file is copied into the import folder (the
  original stays yours), the queue shows what waits, what failed (retry,
  transcript, Finder) and when the processed copy is deleted.
- **Tasks** — every action item from minutes and graph notes in one list;
  ticking writes straight into the markdown. "Mine" comes first, stale items
  fold away, a by-meeting or by-due-date view, an open-count badge.
- **Memory** — chat with a local model (live model list from Ollama); the
  "Graph memory" chip mixes in graph findings, each answer shows its source
  chips and a provenance line, the "What memory knows" column lists the
  cores. The same chat opens as its own "Chat with memory" window; history is
  shared.

The menu bar remains useful with the main window closed: its icon turns into a
red triangle when recorded data is at risk and a yellow circle when something
is degraded without loss; the menu shows the recording timer, processing,
ready and failed states, and exposes Start/Stop, the latest result, Retry,
Recent meetings, Today, a quick question to the local model, dictation, voice
note and diary. Meetings are indexed in Spotlight, and `charoite://record/start`,
`stop`, `toggle`, `charoite://meeting/<id>`, `charoite://tasks` and
`charoite://today` drive the app from Shortcuts or a terminal. Storage
locations and retention are covered in
[Data and recovery](../docs/DATA_AND_RECOVERY.md).

## Archive search (v2)

Questions and briefs are ranked properly: Russian stemming (plus light English
stemming and bigrams for Chinese), IDF (a rare query word weighs more), query
coverage, file freshness (date in the name), graph distillates prioritized over
raw transcripts, result diversity (one meeting can't take every slot). Weak
matches are flagged "⚠ weak graph matches" — the model won't invent an answer
from irrelevant chunks. The source chips under the answer are clickable: a
meeting opens its card, a graph note or dossier opens as the file itself.

The semantic layer (bge-m3 via your Ollama) works in the built-in search
too: the index keeps a vector per block of each file, builds in the background
and refreshes by mtime (see docs/SETUP — `ollama pull bge-m3`). If the optional
brain server is up (port 8100), search goes there; with neither — pure lexical.

## What leaves the Mac

By default the app talks only to localhost: your Ollama, the optional brain
companion (:8100) and the copilot daemon. Two exceptions are yours to control:
at most once every four hours it asks the public GitHub API for the latest release number
(`sufler.check_updates`, a toggle in Settings), and an update you accept is
downloaded from GitHub releases and replaces the app only after its checksum,
the owner's manifest signature and the Developer ID signature check out. Both
are off under `CHAROITE_NO_CLOUD`. The cloud layers — Claude during a meeting,
the cloud chat engine — are opt-in and run in the daemon; see
[Privacy](../PRIVACY.md).
