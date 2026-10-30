# Speaker diarization

***English** · [Русский](ru/DIARIZATION.md) · [中文](zh/DIARIZATION.md)*

How diarization is built today: which engines there are, where each one is
switched on, how audio flows between them, what they promise each other, where
the thresholds live and what is known not to work. Everything here is read off
the code on `main`; file and function names are the source of truth. Why a
threshold has its value and which bug it paid for is in the fix log,
[research/diarization-log.md](research/diarization-log.md). The Russian and
Chinese pages are still in the older, chronicle form.

Diarization answers one question per piece of speech: *which voice is this*.
Who the owner is, and what a voice is called, are decided on top of it by
separate rules (sections 5 and 6). Without any diarization model Charoite still
works: the transcript is signed by channel — the owner on the microphone, «the
other side» on the system channel.

## 1. Three stacks

| Stack | When | Where in the code | Models | Role |
|---|---|---|---|---|
| **Live tracker** (ERes2Net) | during the meeting | `src/diarize_live.py` — `live_tracker` → `SegmentTracker`; `SpeakerTracker` without segmentation | `models/diar/embedding.onnx`, `models/diar/segmentation.onnx` | voice numbers in the live feed on both channels; echo check between microphone and call channel; fallback layout for the stream |
| **sherpa-onnx pass** | after Stop | `src/diarize.py` — `diarize`, `merge_shards`; settings `src/sherpa_config.py` | the same two files | voice labels of the final transcript, per channel; the fallback engine when Nemotron is off or refuses |
| **Nemotron 3 Diarization** (MLX) | after Stop, and live in `shadow`/`on` | `src/diarize_nemotron.py` (engine process, `diarize_in_env`, `engine_state`), `src/live_nemotron.py` (stream) | `models/diar/nemotron/` + its own Python environment `engines/nemotron` | call-channel and microphone labels after the meeting; call-channel labels live in `on` |

The tracker cannot be removed even when Nemotron labels the call channel live:
`live_nemotron.start` refuses `on` without the segment tracker, because echo
and microphone ownership are decided by tracker voices (section 5).

Nemotron never runs inside the daemon or the app: the engine is a separate
process started with the interpreter of its own environment through the
`foreign_python` door (`diarize_nemotron.diarize_in_env` after the meeting,
`foreign_python.spawn_stream` live). MLX and `transformers` never enter the
product's process.

## 2. Where each stack is switched on

All keys live in `config/config.yaml`, section `sufler`.

| Key | Values (default) | Read by | Normalizer |
|---|---|---|---|
| `live_diarize` | `true` / `false` (`true`) | `daemon.py` (tracker start) | — |
| `live_diarize_threshold` | cosine (0.45) | `daemon.py` → `SpeakerTracker` (simplified mode only) | — |
| `diarize_backend` | `sherpa` / `nemotron` (`sherpa`) | `rebuild_transcript.call_channel_engine`, `rebuild()`, `diarize_nemotron.engine_state` | `diarize_nemotron.normalize_engine_key` |
| `live_nemotron` | `off` / `shadow` / `on` (`off`) | `live_nemotron.start`, `engine_state` | `diarize_nemotron.normalize_live_mode` |
| `nemotron_python` | path or empty (`""` = installed environment) | `diarize_nemotron.engine_interpreter` | — |

An unknown `diarize_backend` is not folded into `sherpa`: the rebuild labels
with sherpa and the transcript header names the unknown value. An unknown
`live_nemotron` switches the stream off with a status line.

Which tracker runs is decided by the files on disk, not by config:
`diarize_live.tracker_kind(seg_model, emb_model)` returns `segments` (both
models), `chunks` (embeddings only) or `None` (nothing — channel labels only);
`diarize_live.availability_note` tells the person why diarization is missing or
degraded, and names the install command.

### Installing the models

```bash
.venv/bin/python scripts/get_models.py --diar          # embeddings, then segmentation
.venv/bin/python scripts/get_models.py --segmentation  # segmentation model only
.venv/bin/python scripts/get_models.py --diar --check  # verify both files, no network
```

The app's first-run wizard («Tell speakers apart») runs the same `--diar`.
Default embedding model: `eres2net-base`; `--model eres2net-en` and
`--model eres2netv2` are the alternatives (`--list`). Files are checked against
a pinned sha256 and must be valid ONNX; an interrupted download resumes.

Nemotron is installed by the product into its own environment, on your
command:

```bash
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron          # install
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron --check  # what is there, no network
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron --plan   # plan as one JSON line, no network
```

The installer also fetches the missing sherpa model set (the fallback engine
needs it). Exit codes: `0` installed and probed, `1` refusal, `12`
(`EXIT_INSTALL_BUSY`) the machine is busy (live recording, a meeting being
processed, the night cycle, models in memory), `13` (`EXIT_INSTALL_CANCELLED`)
cancelled. The doctor (`scripts/doctor.py`) prints the exact command for your
machine. The engine's state — installed, broken, enabled, which interpreter,
outdated — is one function, `diarize_nemotron.engine_state(root, cfg)`; the
doctor, the installer's `--plan` and the app read its fields and do not
re-derive them.

## Post-meeting pass on Nemotron

```yaml
diarize_backend: nemotron   # sherpa by default
nemotron_python: ""         # empty: the environment scripts/install_engine.py installed
```

With this the rebuild sends the call channel to Nemotron, then the microphone:
on a call `mic_channel_engine`, in a room `room_mic_engine`. Any refusal or
failure falls back to sherpa for that channel, and the header says so
(`ENGINE_REFUSED_REASON`; the raw refusal with machine paths goes to the log
only). Details of the channel decision are in section 4.

- **Version.** The mlx-audio version the engine seam was checked on is
  `MLX_AUDIO_VERSION` in `src/diarize_nemotron.py`; `version_verdict` refuses
  another `major.minor` and warns on another patch. Bench recipe for the
  development venv (a test keeps the pin here equal to the constant):

  ```bash
  .venv/bin/pip install "mlx-audio==0.5.9"
  .venv/bin/hf download mlx-community/Nemotron-3-Diarization --local-dir models/diar/nemotron
  ```

  Raising it: change `MLX_AUDIO_VERSION`, run
  `.venv/bin/python scripts/lock_runtime_deps.py nemotron` (needs `uv`), update
  the recipes in both `DIARIZATION.md` (English and Russian). The `.in` file and
  the lock are generated: a PR that edits only `.in` stays red.
- **Network.** `load_model` accepts only a local folder with `config.json` and
  weights; a repository id is refused with the download recipe.
- **Voices.** Voice caches live in RAM and die with the process; nothing
  derived from a voice is written to disk (`tests/test_no_voice_biometrics.py`).
- **License.** The weights are NVIDIA OpenMDW 1.1 and are never committed.

## 3. Data flow

```
capture hub (audio.py)
 ├─ chunks per channel, start on the hub's sample axis
 │    ├─ STT  ──────────────────────────────────────────────┐
 │    └─ live tracker (diarize_live)  → voice number        │
 ├─ raw blocks (add_frame_listener, same axis)              │
 │    └─ Nemotron stream (live_nemotron, shadow/on) → slot  │
 │                                                          ▼
 │                      layout of pieces: plan_pieces / settle / stream_layout
 │                      owner and echo: owner_voice     → live transcript
 └─ recordings of both channels: recordings/<stamp>_*
          │  after Stop
          ▼
 rebuild_transcript.rebuild()
   call channel: call_channel_engine (Nemotron) → else diarize_channel (sherpa, live voice count as hint)
   microphone:   mic_channel_engine / room_mic_engine (Nemotron) → else mic_sherpa (hint or veto plan)
   resolve_channel_segments: echo filter, owner_voices, dwarf clusters
   names (speaker_names), register (voice_pitch)          → final transcript
```

- **One sample axis per channel.** The hub counts every sample appended to a
  channel, including audio the buffer cap dropped. A chunk and a raw block
  carry their start on that axis, from one counter under one lock, so the
  stream's segments line up with STT chunks without counting positions twice.
- **Yielding to recognition.** When STT falls behind
  (`stt_runtime.should_shed_diarization`: enter at a backlog of two chunks and
  at least 6 s, recover below half a chunk), live diarization is skipped and
  chunks go out under the channel label; the rebuild labels those minutes again.
- **Yielding to a live meeting.** The rebuild calls `_yield_to_live` before
  every heavy channel pass, not only on entry: a meeting that starts mid-rebuild
  gets the machine back.

## 4. Contracts between the stacks

**Channel segments.** Both engines answer one channel with
`list[(start_s, end_s, voice)]`, or `None`. Pieces shorter than
`rebuild_transcript.MIN_SEGMENT_S` (1 s) are dropped for every engine.

| Answer | Meaning | What the rebuild does |
|---|---|---|
| `None`, channel ≤ `MIN_DIARIZE_S` (20 s) | not labelled: too short to judge | channel is not silence; the live voice count still holds |
| `None`, channel > 20 s | engine failure | `_channel_lost`: the live transcript stays, the meeting is marked as not rebuilt |
| `[]` | labelled, no speech | the channel is silent (sherpa-onnx gives `[]` on silence and noise, measured 21 of 21) |
| segments | labelled | to `resolve_channel_segments` |

Exception: on a call, Nemotron heard next to no speech on the microphone
(`mic_thin`) and sherpa then failed — the microphone counts as empty.

**Nemotron after the meeting.** `diarize_in_env` returns a
`foreign_python.Outcome` (`ok`, `failed`, `empty`, `skipped`, `rejected` with a
reason). The rebuild collapses it to the table above inside `_nemotron`; a
single segment covering at least `DEGENERATE_SHARE` (90 %) of the channel is a
refusal, not one speaker.

**Microphone engine follows the call channel.** The microphone goes to Nemotron
only when the call channel was labelled by Nemotron, or when there is no call
recording and `diarize_backend` is `nemotron`. After a refusal on the call
channel the engine is not called again.

**Room or call.** `call_silent` — no call recording, or it was labelled empty,
or its speech never passed the call threshold — means a room: the microphone is
labelled as several people (`mic_plan` → hint from the live count, or auto
with a merge veto). Otherwise every non-echo microphone voice is the owner.

**Live stream (`on`).** A call-channel chunk waits for its stream label at most
`WAIT_CAP_S` (2 s), on its own event, not under the shadow's lock; not labelled
in time — the tracker's layout, and the journal line names the fallback
reason. Stream segments go through the same `plan_pieces` / `settle` as tracker
segments. Stream labels are numbered above any tracker number
(`STREAM_VOICE_BASE` = 1000, kept by `StreamVoices`), so a voice name never
crosses between them; a tracker piece takes the stream label linked to its
voice, or the channel label — never a second number for the same person.

**Journal.** `logs/nemotron_live_<stamp>.jsonl`, version `JOURNAL_V` (3):
numbers and reasons only, no audio and no text. Read by
`scripts/nemotron_shadow_check.py` and `scripts/nemotron_shadow_replay.py`.

## 5. Which voice is the owner

One rule for the live feed and the rebuild, in `src/owner_voice.py`:

- The microphone label is `sufler.user_name`, or «Я» when empty
  (`channel_labels.ChannelLabels`). A name that looks like a neutral label
  cannot sign anything, and the app says so at start.
- **On a call the microphone belongs to the owner.** Every microphone voice
  that is not echo is the owner, together with at least `MIN_MIC_SECONDS` (15 s)
  of speech (`owner_voices`).
- **Call flag** (`call_from_gate`): at least `CALL_MIN_SPEECH_S` (30 s) of
  system-channel chunks above the capture gate within a `CALL_WINDOW_S`
  (120 s) window. Live counts chunks as they come; the rebuild cuts the
  recording with the same `speech_gate`. Once raised, it stays.
- **Echo.** The rebuild drops a microphone segment covered by call-channel
  speech by more than half. Where the call channel stops being a witness — a
  run of zeros of `UNSUPPORTED_MIN_S` (60 s) or more, or a channel ending that
  much earlier (`unsupported_spans`) — a voice proved to be echo outside those
  spans (`ECHO_VOICE_SHARE` 0.8 of its speech, at least `ECHO_VOICE_MIN_S` 20 s)
  gets a neutral label inside them. Only when the microphone was labelled by
  Nemotron.
- **Echo by text** (`text_echo_match`: 80 % shared words, at least five, within
  8 s) feeds only the `owner-pulse` log line; it does not change signatures.

## 6. Names and register

- `src/speaker_names.py` — a name heard in the conversation is assigned to a
  label by the local LLM behind trust guards (`judge_name`, `trustworthy_name`);
  refusals are remembered for the meeting (`settle`, `forget_refusals`).
- `src/voice_pitch.py` — F0 register (`LOW_MAX` 145 Hz, `HIGH_MIN` 190 Hz) only
  vetoes a name that contradicts the voice (`contradicts`). Kept in memory of
  the meeting, never written to the transcript, graph or documents.

## 7. Thresholds and where they live

| Threshold | Value | Where | Measured |
|---|---|---|---|
| tracker attach (segments mode) | 0.62 | `diarize_live.SegmentTracker` | 0.55 merged people, 0.7 split them |
| tracker attach (chunks mode) | 0.45 | `sufler.live_diarize_threshold` → `SpeakerTracker` | — |
| live voices per channel | 8 | `diarize_live.LIVE_MAX_SPEAKERS` | one shared limit left the owner without a number |
| sherpa clustering | 0.8 | `diarize.diarize(threshold=)` | — |
| shard merge | 0.60 | `diarize._merge_shards` | same voice 0.68–0.89, different people 0.11–0.46 |
| merge veto in a room | 0.46 | `diarize.VETO_BELOW` | top of the «different people» range; on calls it split the owner (4 of 8) |
| weak shard attach | 0.50 | `diarize.WEAK_THRESHOLD` | — |
| participant floor | 30 s | `diarize.MIN_SPEAKER_S` | — |
| dwarf cluster, call channel | 25 s (sherpa), 5 s (Nemotron) | `rebuild_transcript.BH_DWARF_S`, `NEMOTRON_BH_DWARF_S` | — |
| dwarf cluster, microphone | 10 s | `rebuild_transcript.MIC_DWARF_S` | — |
| room = call recorded for | ≥ 80 % of the microphone | `rebuild_transcript.ROOM_MIN_CALL_SHARE` | — |
| live count as microphone hint | 3+ voices | `rebuild_transcript.MIC_HINT_MIN` | — |
| Nemotron timeout | 60 s + 0.1 × length | `rebuild_transcript.NEMOTRON_TIMEOUT_S` | — |
| stream label shown after | 5 s of slot speech | `live_nemotron.SLOT_SHOW_S` | ghost slots with 0–9 s each |
| stream queue / footprint stop | 10 s / 2048 MB twice | `live_nemotron.QUEUE_CAP_S`, `FOOTPRINT_STOP_MB` | — |
| MLX cache of the child | 512 MB | `live_nemotron.CACHE_LIMIT_MB` | 8.5 GB without the cap |
| memory pressure stop | level 4 | `live_nemotron.PRESSURE_STOP` | level 2 held without the stream |

A dash in «Measured» means no number is recorded for the value; that is the
list of thresholds to measure first.

## The merge threshold is measured, not guessed

Segmentation splits one person across several clusters, especially in a room
on one microphone. The merge step (`diarize._merge_shards`) joins clusters whose
mean embeddings are closer than 0.60 by cosine. It runs when clustering chose
the number of voices itself, and after the live hint on a room microphone
(`merge_shards`, the hint is then an upper bound).

Measured on one 65-minute room recording with three speakers: pieces of the
same voice 0.68–0.89, different people 0.11–0.46. The line sits in the empty
band between them. In a room one bridge is not enough — single linkage chained
a meeting of six into one label — so the room microphone merges pair by pair
under a veto: two groups never join while any pair between them is below
`VETO_BELOW` (0.46). On calls the veto is off.

A threshold changes only through the bench (section 9): a new value comes with
a DER on the fixtures and on a real recording, and the fix log keeps the old
number next to the new one.

## 8. Known limits

- **A colleague at the same microphone** on a call gets the owner's name: the
  microphone rule is by channel, not by voice print.
- **A call where the other side is silent the whole meeting** looks like a
  room; the owner's speech may spread over several neutral labels.
- **Sound that is not a call** (a video through the speakers for over 30 s in
  a 2-minute window) raises the call flag in a room, and the whole microphone
  is signed as the owner.
- **More than 8 people** on a call: the live tracker labels a new voice with
  the channel label only; Nemotron has 8 slots, so two people share one.
- **Start of a call.** The call flag rises 30–83 s in (eleven recorded calls);
  until then microphone labels in the live feed stay neutral.
- **Synthetic voices mislead Nemotron**: macOS `say` voices taking turns land
  in one slot. Judge it on real recordings only.
- **Russian** is not among Nemotron's training languages; checked as a
  voice count on two calls, not as DER.
- **Translations** (`ru/`, `zh/`) still describe the older form; the Russian
  page also holds the test contract of the app's install offer.

## 9. How to measure

`scripts/diar_bench.py` computes DER: the share of speech time labelled
wrongly (missed, false alarm, wrong voice), hypothesis labels matched to the
reference first.

```bash
.venv/bin/python scripts/diar_bench.py --make                    # synthetic dialogue + reference
.venv/bin/python scripts/diar_bench.py                           # live, live-split, sherpa
.venv/bin/python scripts/diar_bench.py --wav recordings/<stamp>_blackhole.wav --engine compare
.venv/bin/python scripts/diar_bench.py --wav fragment.wav --truth fragment.txt --engine compare
```

`--truth` takes Audacity labels, RTTM or the bench's JSON; without it the bench
prints voices, speech, overlap and switches per engine. The live stream is
replayed over a recording by `scripts/nemotron_shadow_replay.py` and judged by
`scripts/nemotron_shadow_check.py` (wait times, layout source, fallback reasons).

## Crosstalk, and the Nemotron experiment

`--make --crosstalk` builds a fixture where replies overlap by 0.8–1.5 s; DER
is then scored the NIST way (a frame may hold several voices).

Numbers on an M1 Max, 28 September 2026, mlx-audio 0.5.6, model loading
included. AMI test meetings (Mix-Headset, four speakers, English), strict
word-level reference, no collar:

| Engine | ES2004a (17.5 min) | IS1009a (14.0 min) | RTF |
|---|---|---|---|
| post-meeting pass, sherpa (production) | 0.606 · 3 voices | 0.453 · 4 voices | 0.35–0.36 |
| live-split (daemon slicing) | 0.676 · 8 voices | 0.541 · 9 voices | 0.06–0.07 |
| Nemotron, whole file | **0.269** · 4 voices | **0.277** · 4 voices | 0.003 |
| Nemotron stream (`low`, 1.04 s) | 0.273 · 5 voices | 0.273 · 4 voices | 0.04 |

Synthetic fixture (live tracker, DER): one label per chunk 0.270, positional
layout (`live-split`) 0.167. Live stream (`on`): the label is ready 0.8 s of
audio after the chunk end (p50, max 1.3 s). The full chronicle of these and
earlier numbers — [research/diarization-log.md](research/diarization-log.md).
