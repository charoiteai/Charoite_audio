# Charoite for iPhone — the companion app

*[**English**] · [Русский](README.ru.md) · [中文](README.zh.md)*

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
- **Delivery** — recordings land in an iCloud Drive folder you pick once
  (the same folder the Mac app watches as its import folder). No
  connection right now? An on-device outbox queue re-sends on every
  launch and after every stop. Voice notes (`note_`/`diary_` prefixes)
  are routed into the Mac's notes pipeline automatically.
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

1. **Record tab** → folder icon → pick your delivery folder in iCloud
   Drive (e.g. `Charoite Inbox` — the Mac's import folder).
2. **Meetings tab** → "Choose folder" → pick your graph folder inside
   the Obsidian location in Files.

Both choices are one-time; security-scoped bookmarks survive restarts.

## Privacy

The app talks to nothing but your own iCloud Drive folders. No
accounts, no telemetry, no third-party services. Recordings you delete
from the folders are gone — there is no hidden copy.
