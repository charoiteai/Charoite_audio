# Charoite Roadmap

***English** · [Русский](ROADMAP.ru.md) · [中文](ROADMAP.zh.md)*

Local-first, in rough priority order. No dates — quality over deadlines.

A design principle worth stating: **no voice biometrics**. Speaker
recognition stays social — introductions and addressing in the
conversation itself — never stored voice prints. Same-voice cluster
merging happens in memory during a single recording and is discarded.

## Near

- **Full English and Chinese faces** — the engine speaks all three
  (Parakeet/Whisper STT, `language: en|zh` documents, graph content, hints
  and minutes; Qwen is native in Chinese; `config.example.{en,zh}.yaml`;
  README ×3 and translated docs as front doors). What's left is the last
  mile: app UI localization (string catalogs for macOS and iOS) and
  per-language screenshots for the READMEs.
- **Chinese SOTA STT backend** — SenseVoice/Paraformer (FunASR) via
  sherpa-onnx as a `stt.backend` option: noticeably lower CER on Chinese
  than Whisper, same local-only story as GigaAM for Russian.

- **iPhone companion, v1 completion** — the recorder is shipped
  (app-ios/): meetings feed and task checkboxes from the graph, a Live
  Activity in the Dynamic Island (timer + stop from anywhere), TestFlight.
- **Direct Wi-Fi delivery to the Mac** — the phone hands recordings to
  the Mac daemon over the local network (Bonjour, pairing code); iCloud
  becomes the optional fallback, not the default path.
- **Graph-aware archive answers** — pull 1-hop neighbours of matched
  nodes (person → their meetings → decisions) into the answer context,
  plus a local reranker over the top candidates.
- **Diarization shard-merge tuning** — same-voice merging within a
  recording shipped (30 clusters → 12 on a real in-person meeting);
  tighten thresholds on more live data.

## Mid

- **Windows port** — the daemon is Python + ONNX and the delivery protocol
  is platform-neutral (mDNS + TLS); the work is a native shell and an
  audio-capture story to replace BlackHole.
- **Android companion** — same open delivery protocol as the iPhone app
  (NsdManager + TLS upload); background recording is simpler on Android.
  Until then, Syncthing into the import folder works today.

- **Companion live mode** — the phone streams meeting audio to the Mac
  and mirrors the live transcript and hints on its screen.
- **App Store release** — after the meetings feed makes v1 feel complete
  (TestFlight first).
- **Packaged graph viewer** — browse the meeting graph without Obsidian.
- **Streaming archive answers** — tokens as they generate, not one blob.

## Done (recent)

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
