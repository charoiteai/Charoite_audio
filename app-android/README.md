# Charoite for Android — the companion app

*[**English**] · [Русский](README.ru.md) · [中文](README.zh.md)*

The tablet (or phone) is the microphone on the table; the Mac stays the
brain. A Compose companion (Android 8+) that records meetings, voice
notes and diary entries and reads the knowledge graph back — while every
heavy step (STT, diarization, LLM, graph building) runs on your Mac.

## What it does

- **Record** — three kinds: Meeting / Note / Diary. Background-safe
  recording through a foreground service (start from the screen, then
  switch apps), live level meter, a timer in the shade and on the lock
  screen, with a Stop button right in the notification.
- **Delivery** — recordings land in a folder you pick once through the
  system picker (SAF). The Mac must see the same folder: through
  Syncthing or any sync tool; on the Mac it is the import folder. No
  sync right now? An on-device queue re-sends on every launch and after
  every stop. Voice notes (`note_`/`diary_` prefixes) are routed into
  the Mac's notes pipeline automatically.
- **Meetings feed** — reads `.md` files from the graph's “Встречи”
  folder (second folder you pick), newest first, full text on tap.
- **Tasks** — every `- [ ]` checkbox from the graph in one list; ticking
  writes back into the markdown file itself, so the Mac, Obsidian and
  the tablet always agree.

Graph scans and file reads run in the background. Large vaults therefore
stay responsive, and opening the same tab again cancels its obsolete scan
instead of publishing stale results.

## Recording format

16 kHz, mono, 16-bit WAV — exactly what recognition on the Mac needs,
with no transcoding on the way (about 115 MB per hour).

WAV is chosen not for its size but for what happens on a crash: an
MPEG-4 container without a proper `stop()` has no `moov` atom and reads
nowhere, and an hour of someone else's meeting cannot be re-recorded.
The WAV header is refreshed while recording, so even a file killed by
the system stays valid; on the next launch the app writes the actual
length into it.

## Build and install

Requires JDK 17 and the Android SDK (compileSdk 35). The SDK path lives
in `app-android/local.properties` (`sdk.dir=...`), which is not in the
repository.

```bash
cd app-android
./gradlew testDebugUnitTest        # graph parsing and the WAV header
./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

On-device check (microphone, service, file integrity):

```bash
./gradlew connectedDebugAndroidTest
```

## First-time setup

1. **Settings** → “Delivery folder” → pick the folder you sync with the
   Mac (on the Mac it is the Charoite import folder).
2. **Settings** → “Graph root” → point at the Obsidian graph folder —
   the one that holds the “Встречи” section.

Both are one-time choices: folder grants survive restarts and reboots.

## Privacy

The app holds no network permissions at all — check
`AndroidManifest.xml`. Audio leaves the device only through the folder
you picked. No accounts, no telemetry, no third-party services.

## Not there yet

Direct Wi-Fi delivery to the Mac: it waits for a shared protocol (mDNS +
pairing code + TLS), one for both iPhone and Android — see the ROADMAP.
Until then the folder is synced by Syncthing or any other tool, and the
app does not care which one.

Recordings made on Android carry the `android_` prefix, so the import
folder shows at a glance what recorded a given meeting.
