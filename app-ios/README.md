# Charoite for iPhone — the companion app

*[**English**] · [Русский](../docs/ru/app-ios/README.md) · [中文](../docs/zh/app-ios/README.md)*

The phone is the microphone on the table; the Mac stays the brain. A
SwiftUI companion (iOS 17+, works from iPhone 12 up) that records
meetings, voice notes and diary entries and reads the knowledge graph
back — while every heavy step (STT, diarization, LLM, graph building)
runs on your Mac.

## What it does

- **Record** — three kinds: Meeting / Note / Diary. Background-safe
  recording (start from the screen, then lock the phone or switch apps),
  live level meter, a Live Activity timer in the Dynamic Island and on
  the lock screen.
- **Stalled-recording watchdog** — if the file's duration stops growing
  for more than three seconds (a call, an interruption, a stolen
  microphone), the screen says so in orange. An earlier build measured
  time by the wall clock: thirty minutes ran on screen while forty-one
  seconds landed in the file, and there was no way to know.
- **Delivery** — recordings land in an iCloud Drive folder you pick once
  (the same folder the Mac app watches as its import folder). No
  connection right now? An on-device outbox queue re-sends on every
  launch and after every stop. Voice notes (`note_`/`diary_` prefixes)
  are routed into the Mac's notes pipeline automatically.
- **The queue is visible in full** — the "queued: N" line opens a list:
  what was recorded, when, how large. Anything older than a day is
  highlighted: normal delivery takes seconds, so whatever hangs longer is
  no longer "about to leave". Re-send with one button from there.
- **Take the recording by hand** — a "Share recording" button hands the
  file off anywhere, and the recordings folder shows up in Files and over
  the cable (`UIFileSharingEnabled`). The five most recent recordings stay
  on the phone after delivery: "iCloud accepted it" is not "the Mac got it".
- **Meetings feed** — reads `Встречи/*.md` straight from a graph folder
  you pick (second bookmark), newest first, full text on tap. Files not
  yet downloaded from iCloud are requested and skipped honestly.
- **Tasks** — every `- [ ]` checkbox from the graph in one list; ticking
  writes back into the markdown file itself, so the Mac, Obsidian and
  the phone always agree.

## Build and install

Requires Xcode 15+ and [XcodeGen](https://github.com/yonaskolb/XcodeGen):

```bash
cd app-ios
xcodegen generate
open CharoiteiOS.xcodeproj   # select your team, build to your device
```

Tests: unit target (graph parsing) + UI tests. Run them on a simulator:

```bash
xcrun simctl privacy booted grant microphone ai.charoite.CharoiteiOS
xcodebuild -project CharoiteiOS.xcodeproj -scheme CharoiteiOS \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

## First-time setup on the phone

1. **Record tab** → the tray icon (↑) → pick your delivery folder in
   iCloud Drive (e.g. `Charoite Inbox` — the Mac's import folder).
2. **Meetings tab** → the books icon → pick your graph folder inside
   the Obsidian location in Files.

The two folders are different on purpose — delivery is where recordings
go, the graph is what the phone reads — so their icons differ too: an
orange icon means that folder is not chosen yet. Both choices are
one-time; security-scoped bookmarks survive restarts.

## Privacy

The app talks to nothing but your own iCloud Drive folders. No
accounts, no telemetry, no third-party services. Recordings you delete
from the folders are gone — there is no hidden copy.
