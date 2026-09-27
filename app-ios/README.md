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
  the lock screen — with a **Stop** button right there: the phone can stay
  face down on the table and the recording still ends when the meeting does.
- **Listens right away** — open the app and the recording is already
  running (gear → "Record as soon as the app opens", on by default; the
  kind is the last one you picked; kicks in once the delivery folder is
  chosen). Opened during a call? iOS keeps the microphone for the call,
  so the app arms itself, shows "waiting for the microphone" and starts on
  its own the moment the call ends — no second tap — while the app stays
  open: a backgrounded app cannot start on iOS, but come back within 30
  minutes and it starts on return. Cancel with the same big button.
- **Hands-free start** — an App Intent "Start recording in Charoite" for
  Siri, Shortcuts, the Action button and Back Tap. iOS never starts a
  recording from the background: the intent opens the app, which starts
  immediately. What no app can do on iPhone: record the call itself — the
  microphone belongs to the call; a call during a recording is a pause.
  When the call ends, the app waits for the microphone for up to a minute
  (iOS hands it back a few seconds after a long call, not instantly) and
  continues the same file; if the input never comes back, it closes the
  file and continues the meeting in a new one. A second call inside that
  minute cancels the wait, so the countdown itself never cuts the file
  while iOS has told the app a call is live. Opening the app during a pause
  does not start that countdown: for up to a minute it probes the input on
  the same ladder, without rotating, and continues the same file the moment
  the microphone is back. If the new file cannot start because the input is
  still busy, the start is armed; in the background that arm cannot fire
  on its own (iOS suspends the app), so the recording picks up when the app
  is opened again or when the system grants it time. Calls are recorded by
  the Mac.
- **Stalled-recording watchdog** — if the file's duration stops growing
  for more than three seconds (a call, an interruption, a stolen
  microphone), the screen says so in orange. An earlier build measured
  time by the wall clock: thirty minutes ran on screen while forty-one
  seconds landed in the file, and there was no way to know. Outside a
  call the app tries to resume by itself; after three failed attempts it
  closes the file and continues the meeting in a new one. A codec error or
  an iOS audio-service reset does the same — the file is kept and the
  meeting goes on in the next one; three codec errors in a row stop the
  recording honestly instead of producing empty pieces.
- **Why a recording stopped** — every closed file gets a `<file>.json`
  manifest beside it: who closed it, why (the Stop button, the microphone
  not back within a minute after a call, an audio-service reset, a codec
  error, a stall — or no stop recorded at all, when the app died), when,
  and how many seconds. It travels to the Mac as a pair with the audio and
  becomes an event in the meeting's recording trace, so a meeting that
  arrives in three pieces says why.
- **Delivery** — recordings land in an iCloud Drive folder you pick once
  (the same folder the Mac app watches as its import folder). No
  connection right now? An on-device outbox queue re-sends on every
  launch and after every stop. A file is published atomically (copied
  under `.part`, then renamed) and leaves the queue only once iCloud
  reports its copy uploaded, where iOS can tell at all — "copied into the
  folder" is not yet "on its way to the Mac". Voice notes
  (`note_`/`diary_` prefixes) are routed into the Mac's notes pipeline
  automatically.
- **The queue is visible in full** — the "Recordings queued: N" line opens a list:
  what was recorded, when, how large. Anything older than a day is
  highlighted: normal delivery takes seconds, so whatever hangs longer is
  no longer "about to leave". Re-send with one button from there.
- **Take the recording by hand** — a "Share recording" button hands the
  file off anywhere, and the recordings folder shows up in Files and over
  the cable (`UIFileSharingEnabled`). The five most recent recordings stay
  on the phone after delivery: "iCloud accepted it" is not "the Mac got it".
- **Meetings feed** — reads straight from a graph folder you pick (second
  bookmark): the portable meeting cards (`Встречи-архив/*/meeting.meta.json`
  — participants, gist, decisions, action items, open questions) and, for
  older meetings without a card, `Встречи/*.md`; newest first, the card or
  full text on tap. Files not yet downloaded from iCloud are requested and
  skipped honestly.
- **Tasks** — every `- [ ]` checkbox from the graph in one list; ticking
  writes back into the markdown file itself, so the Mac, Obsidian and
  the phone always agree.

## Build and install

Requires Xcode 16+ (XcodeGen writes a project format Xcode 15 cannot open)
and [XcodeGen](https://github.com/yonaskolb/XcodeGen):

```bash
cd app-ios
export DEVELOPMENT_TEAM=<team id>   # read by project.yml; no team ID is stored in the repo
xcodegen generate
open CharoiteiOS.xcodeproj   # build to your device
```

A build without signing, as CI does it, needs no team:
`xcodebuild … CODE_SIGNING_ALLOWED=NO build`.

The version shown in Record settings comes from `MARKETING_VERSION` in `project.yml`
(bumped by release-please); check the generated plist after `xcodegen generate`:
`plutil -p Info.plist | grep CFBundleShortVersionString` must print `$(MARKETING_VERSION)`,
and the built bundle resolves it to the release number. The build number
(`CURRENT_PROJECT_VERSION`, both targets) is not managed by release-please:
bump it by hand before every App Store / TestFlight upload.

Tests: a unit target (graph parsing, the recorder's policies for calls,
stalls, codec errors, auto-start and stop manifests, the delivery queue)
and UI tests. Run them on a simulator (CI does this nightly):

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

## TestFlight

Build and upload with cloud signing through an App Store Connect API key
(App Manager role; the key is NOT in the repository):

    export DEVELOPMENT_TEAM=<team id>
    xcodegen generate
    xcodebuild -project CharoiteiOS.xcodeproj -scheme CharoiteiOS \
      -destination 'generic/platform=iOS' \
      -archivePath build/CharoiteiOS.xcarchive archive \
      -allowProvisioningUpdates \
      -authenticationKeyPath ~/.config/charoite/AuthKey_<KEY_ID>.p8 \
      -authenticationKeyID <KEY_ID> -authenticationKeyIssuerID <ISSUER_ID>
    xcodebuild -exportArchive -archivePath build/CharoiteiOS.xcarchive \
      -exportOptionsPlist ExportOptions.plist -exportPath build/export \
      -allowProvisioningUpdates \
      -authenticationKeyPath ~/.config/charoite/AuthKey_<KEY_ID>.p8 \
      -authenticationKeyID <KEY_ID> -authenticationKeyIssuerID <ISSUER_ID>

`destination: upload` in `ExportOptions.plist` sends the build straight to
TestFlight; the same file carries the owner's `teamID` — put yours there.
One-time prerequisites: the bundle id is registered through the ASC API
(POST /v1/bundleIds — this needs an App Manager key; a Developer-role key
gets 403), while the APP RECORD can only be created by hand in ASC (App
Store Connect → Apps → "+" → New App); without it the export fails with
"Error Downloading App Information". `ITSAppUsesNonExemptEncryption: false`
(set in `project.yml`, which generates Info.plist) spares every build the
manual export-compliance question. Bump `CURRENT_PROJECT_VERSION` before
each upload (see above).
