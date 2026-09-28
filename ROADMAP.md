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
  archive retroactively. What's left: the graph's own folder and section
  names are Russian in every mode (the writer creates `Люди/`, `Встречи/`,
  `Ядра/` whatever `sufler.language` says — the config templates point
  here), and the Chinese README still shows the Russian screenshots.
- **Direct Wi-Fi delivery to the Mac** — the phone hands recordings to
  the Mac daemon over the local network (Bonjour, pairing code); iCloud
  becomes the optional fallback. Measurement first: how long a recording
  actually takes through iCloud across ten meetings. Minutes — we build
  it; seconds — a second transport is not worth its attack surface.
- **Graph-aware archive answers in the app** — the daemon's own memory
  (live hints, instant answers) already takes one hop over explicit
  `[[links]]` from matched nodes (person → their meetings → decisions),
  deduplicated (September 2026). The app's archive answers on the Memory
  screen do not: their search returns the best block of each file and never
  follows a link. The same hop goes there; sources and the honesty gate stay
  as they are.
- **A local reranker over the top candidates** — separately, and only
  after numbers: `AnswerQualityProbe` is an observation on three questions,
  not a measurement, and nobody has measured how often the RRF top-5
  misses. The SenseVoice lesson: benchmark first, model in the bundle
  second.
- **Decision gate for instant answers: from shadow to deciding** — the
  shadow shipped (`sufler.decision_gate_shadow`, off by default): an encoder
  logs a verdict next to every ⚡ answer and decides nothing. It gets to skip
  a model call only after measurement — a labelled set from real
  transcripts, one to two weeks of shadow on live meetings, a distilled head
  of our own — and only if it meets the criterion in
  [the research note](docs/research/decision-gate.md): at most 1% of real
  questions lost, at least half of the "clarify" refusals removed, ECE no
  higher than 0.05. Otherwise it stays a shadow.
- **Diarization: merge thresholds by evidence, not by one meeting** — the
  0.60 threshold was calibrated on a single in-person meeting (65 minutes,
  three speakers) and is already the third value; it moves only on
  labelled live recordings with different microphones and speaker counts,
  measured separately for offline and live. An observation status, not a
  dated item.
- **iPhone companion in the App Store** — TestFlight is done, and so is the
  privacy manifest (`PrivacyInfo.xcprivacy` for the app and its widget,
  September 2026). Before submission: a privacy policy page and App Privacy
  answers, a background-audio justification for App Review, and lock/
  background/interruption runs on a real iPhone.

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
  protocol), but the audio hub, the daemon and transcript assembly still
  address the far side by the literal channel label `blackhole` (recording
  file names carry it), and above all the UI would have to be written
  again: SwiftUI does not port, and it is larger than both mobile
  companions together. Not without a second developer or explicit demand.
- **Companion live mode** (the phone streams meeting audio to the Mac and
  mirrors the transcript). The companion has no live audio capture (it
  records to a file), the Mac has no network listener at all; this is a
  separate low-latency system on top of a delivery path that does not
  exist yet.

## Done (recent)

- Decision gate for instant answers in shadow mode: a verdict logged beside
  every ⚡ answer and `scripts/gate_bench.py` to measure what it would have
  dropped; it decides nothing yet (September 2026)
- Hint memory without a server: the daemon searches the graph in its own
  process — lexical plus bge-m3 blocks fused by RRF, dossiers first, one hop
  over `[[links]]`, the honesty gate — in tens of milliseconds. The separate
  memory server on :8100 is off; writing meeting facts to it is behind
  `sufler.brain`, off by default (September 2026)
- The cloud review of a meeting corrects the minutes: it drops false action
  items and decisions, restores the ones the local pass missed as
  checkboxes, and fixes misheard speaker names in the transcript and the
  participant list; one retry after a failed launch, the review stage in the
  meeting status, an effort level per kind of call (`cloud_effort`,
  `cloud_live_effort`, `cloud_night_effort`) (September 2026)
- Graph hygiene: a person's or system's description is a dated fact, and the
  old one goes to the node's chronicle (ten lines, older ones to the node's
  archive); no entity node for noise, a typo of an existing name or an
  ambiguous name; a vocative form or a voice from the room's background does
  not become a new person; action items go only to meeting participants
  (September 2026)
- Honest audio: a recording without the other side, or a channel that dies
  mid-meeting, raises a warning that stays until the end and leaves a trace
  in the meeting documents; the menu-bar icon and status line roll up
  recording, processing, Ollama and the night — red only when data is being
  lost (September 2026)
- Import: an "External recording" tab with the queue and failure labels,
  the meeting date taken from the recording itself rather than the file's
  mtime, a bridge from Voice Memos on the Mac, and a source key in the
  transcript sidecar so the same file is not imported twice (September 2026)
- iPhone: recording starts when the app opens; on a microphone held by a
  call it arms itself and starts when the call ends; a "Start recording" App
  Intent for Siri, Shortcuts and the Action button; after a call the meeting
  continues in a new file; delivery waits for iCloud to finish the upload;
  the reason a recording stopped travels to the Mac with the audio
  (September 2026)
- Rebuild: untouched minutes are regenerated from the final transcript, a
  hand-edited transcript is never re-recognized, and the minutes and the
  summary know which source they were built from — that source, not "the
  file exists", decides whether they are stale (September 2026)
- Dictation: a live draft from the system engine while you speak; the text
  lands only in the app where the hotkey was pressed (September 2026)
- Tasks: "Mine" always first, stale open items and anything overdue by more
  than a week fold away, one action item is one row (September 2026)
- Layer boundaries as a machine-checked layout (`scripts/layout_map.py
  --check` in CI); every entry point names the data root explicitly and
  exits with code 5 and a recipe when it has none; the graph modules live in
  the `src/charoite_graph` package (September 2026)
- The cloud edits a copy of the graph, not the graph: only permitted
  changes are carried back, so authorship is known by construction (August
  2026)
- Cloud chat engine for machines that cannot hold a large model: two keys
  (`llm.engine: cloud` and `sufler.cloud_engine`) send hints, theses and
  minutes to an OpenAI-compatible gateway; its key lives in a file of its
  own, https only; embeddings, NLI and speech recognition stay local
  (August 2026)
- Live pipeline health on screen: STT lag, stalled recognition and a failed
  disk recording stay visible in the meeting window and the menu bar until the
  daemon confirms recovery (August 2026)
- Update manifests signed with an owner key that never enters CI, and a
  release gate: an unsigned release never becomes `latest`, so the updater
  never sees it (August 2026)
- UI revision against Rams' ten principles: honesty about the network,
  memory and cloud surfaces, empty states, a meeting card with four depths;
  the vocabulary lives in `docs/DESIGN.md` (August 2026)
- Recording auto-stop: silence on both channels and a duration ceiling —
  with a notification, not silently (August 2026); farewells end the meeting
  too — one cuts the silence wait to a minute, two stop at once (September
  2026)
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
  no aggregate devices — one system permission. The Core Audio tap was
  disabled after it wedged `coreaudiod` four times on macOS 26.5 (August
  2026) and removed from the package on 2026-09-02; the orphan cleanup that
  outlived it went on 2026-09-06
- Model sets sized to the machine's RAM, offered and installed from the
  first-run wizard instead of a config comment (August 2026)
- One button scale across the app (seven roles, three sizes) and a second
  window that renders exactly like the first (August 2026)
- macOS meeting lifecycle in the UI: recording timer and menu-bar state,
  honest post-processing stages, retry without duplicate runs, a recent-
  meetings list (today the meeting library's card feed) and an in-app result
  card with copy and coherent rename actions (August 2026)
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
