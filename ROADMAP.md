# Charoite Roadmap

***English** · [Русский](docs/ru/ROADMAP.md) · [中文](docs/zh/ROADMAP.md)*

Local-first, in rough priority order. No dates — quality over deadlines.

A design principle worth stating: **no voice biometrics**. Speaker
recognition stays social — introductions and addressing in the
conversation itself — never stored voice prints. Same-voice cluster
merging happens in memory during a single recording and is discarded.

## Near

- **Full English and Chinese faces** — the engine, the macOS UI and the phone
  speak all three; the archive summary follows `sufler.language`, and reading
  is separate from writing, so switching the language no longer breaks the
  archive retroactively. What's left is per-language screenshots for the
  READMEs.
- **Direct Wi-Fi delivery to the Mac** — the phone hands recordings to
  the Mac daemon over the local network (Bonjour, pairing code); iCloud
  becomes the optional fallback. Measurement first: how long a recording
  actually takes through iCloud across ten meetings. Minutes — we build
  it; seconds — a second transport is not worth its attack surface.
- **Graph-aware archive answers** — one hop over explicit `[[links]]`
  from matched nodes (person → their meetings → decisions) into the
  answer context, deduplicated; sources and the honesty gate stay as they
  are. Today search returns the best block of each file and never follows
  a link.
- **A local reranker over the top candidates** — separately, and only
  after numbers: `AnswerQualityProbe` is an observation on three questions,
  not a measurement, and nobody has measured how often the RRF top-5
  misses. The SenseVoice lesson: benchmark first, model in the bundle
  second.
- **Diarization: merge thresholds by evidence, not by one meeting** — the
  0.60 threshold was calibrated on a single in-person meeting (65 minutes,
  three speakers) and is already the third value; it moves only on
  labelled live recordings with different microphones and speaker counts,
  measured separately for offline and live. An observation status, not a
  dated item.
- **iPhone companion in the App Store** — TestFlight is done. Before
  submission: the privacy manifest (`PrivacyInfo.xcprivacy` is missing
  while the delivery queue already uses file-timestamp APIs from the
  required-reason list), a privacy policy page and App Privacy answers, a
  background-audio justification for App Review, and lock/background/
  interruption runs on a real iPhone.

## Mid

- **Android companion: direct delivery** — the companion core shipped
  (app-android/): recording, meetings feed, tasks, delivery into a chosen
  folder. Local-network delivery to the Mac comes after the protocol
  exists on the iPhone side (see Near): today it exists on neither phone
  nor Mac — the daemon listens on no non-loopback port. Until then
  Syncthing keeps the folder in sync.
- **Graph nodes inside the app** — separate from the Memory screen (that
  one answers questions, it does not show links). The meetings library
  exists; what is missing is opening a node of any kind (person, system,
  core), seeing its digest and its outgoing `[[links]]` as a list. Stop
  there: a graph canvas is not planned.

## Not in this cycle

Better to say it than to keep it in "Mid" for years:

- **Windows port.** The daemon and audio intake are abstracted (manifest +
  raw PCM; a WASAPI-loopback producer on Windows could speak the same
  protocol), but diarization and transcript assembly still address the far
  side by the literal `blackhole` in several files, and above all the UI
  would have to be written again: SwiftUI does not port, and it is larger
  than both mobile companions together. Not without a second developer or
  explicit demand.
- **Companion live mode** (the phone streams meeting audio to the Mac and
  mirrors the transcript). The companion has no live audio capture (it
  records to a file), the Mac has no network listener at all; this is a
  separate low-latency system on top of a delivery path that does not
  exist yet.
## Done (recent)

- Update manifests signed with an owner key that never enters CI, and a
  release gate: an unsigned release never becomes `latest`, so the updater
  never sees it (August 2026)
- UI revision against Rams' ten principles: honesty about the network,
  memory and cloud surfaces, empty states, a meeting card with four depths;
  the vocabulary lives in `docs/DESIGN.md` (August 2026)
- Recording auto-stop: silence on both channels and a duration ceiling —
  with a notification, not silently (August 2026)
- The owner's name in the transcript comes from the capture channel, with
  no voice print stored (August 2026)
- iPhone companion on TestFlight; Android on compileSdk 37 and Compose BOM
  2026.08 (August 2026)
- SenseVoice as a `stt.backend` option for Chinese (sherpa-onnx,
  `scripts/get_models.py --stt sensevoice`). The benchmark is honest: on
  synthesized Chinese phrases Whisper is more accurate (CER 0.064 vs
  0.149) — SenseVoice stays an option, not a replacement (August 2026)
- Install without a terminal: the app carries a portable CPython **and the
  daemon's own code** inside the bundle, while `CHAROITE_ROOT` keeps
  recordings, transcripts and the config in the user's folder; the first-run
  wizard writes the config and installs the models (August 2026)
- System audio through ScreenCaptureKit: no driver, no Multi-Output Device,
  no aggregate devices — one system permission. The Core Audio tap stays a
  disabled reserve after it wedged `coreaudiod` four times on macOS 26.5
  (August 2026)
- Model sets sized to the machine's RAM, offered and installed from the
  first-run wizard instead of a config comment (August 2026)
- One button scale across the app (seven roles, three sizes) and a second
  window that renders exactly like the first (August 2026)
- macOS meeting lifecycle in the UI: recording timer and menu-bar state,
  honest post-processing stages, retry without duplicate runs, a 14-day
  recent-meetings list and an in-app result card with copy and coherent rename
  actions (August 2026)
- Streaming archive answers with persisted question history (August 2026)

- Topic dossiers: nightly summaries built on top of cores, incremental
  rebuild by source fingerprint, an index the search consults first, and an
  optional cloud review pass behind the `cloud_edit_graph` toggle (July 2026)

- iPhone companion v1 core: background recording (meeting / note /
  diary), delivery into a user-chosen iCloud Drive folder with an
  on-device outbox queue that re-sends on every launch; voice notes are
  routed to the notes pipeline on the Mac (July 2026)
- Nightly cloud review of graph cores — contradictions, stale facts,
  merge candidates, lost threads; top risks and lost threads land in the
  morning brief (July 2026)
- Live meeting context: the daemon distills the topic from the live
  transcript and rebuilds the «past meetings» block mid-call; cloud
  refinement appends to the same hint card (July 2026)
- Same-voice shard merging in diarization, in-memory only (July 2026)
- Import folder (watched) for recorded meetings, replacement dictionary
  for STT-mangled terms, post-meeting hook (July 2026)
- Voice diary mode + one-command import of recorded meetings
  (audio/text/subtitles) (July 2026)
- Meeting archive folders carry the meeting time; copy buttons on hint
  and cloud panes; speaker-name canonicalization against graph nodes
  (July 2026)
- Free guard rail: Dependabot, secret scanning with push protection,
  nightly CI, shellcheck/semgrep gates, SwiftLint (July 2026)
- English documents, phase 1+2: `sufler.language: en` switches minutes,
  summary, instant answers AND graph node content to English (July 2026)
- Calendar brief: one-click prep for the next event (opt-in, read-only,
  July 2026)
- Semantic layer in the built-in app search — bge-m3 + RRF, incremental
  background index, honesty gate (July 2026)
- Hybrid search v2: stemming, IDF, freshness, distillates over raw
  transcripts, honesty gate, clickable sources (July 2026)
- Native macOS app: live transcript, theses, archive Q&A, local chat,
  dictation, voice notes, menu bar (July 2026)
- Demo graph — try the product before your first meeting (July 2026)
