# Speaker diarization setup

***English** · [Русский](ru/DIARIZATION.md) · [中文](zh/DIARIZATION.md)*

Charoite uses two diarization passes:

1. **Live** (during the meeting): speakers in both channels — the system
   audio of a call and the microphone of a room — get labels
   «Собеседник 1/2/…» in real time. Two models, neither bundled:

   - the ERes2Net speaker-embedding model in ONNX format at
     `models/diar/embedding.onnx` (512-dim output, 16 kHz input) — without it
     there are no per-voice labels at all;
   - the pyannote 3.0 segmentation model at `models/diar/segmentation.onnx` —
     it finds utterance boundaries before voices are compared. Without it the
     live tracker falls back to a simplified mode that compares whole
     three-second chunks and confuses voices at utterance boundaries (the
     status line says so and names the command; DER below).

   **Easiest: the "Tell speakers apart" button in the app's first-run
   wizard** — it installs the embedding model. The segmentation model is one
   more command:

   ```bash
   .venv/bin/python scripts/get_models.py --diar          # embedding model (default)
   .venv/bin/python scripts/get_models.py --segmentation  # segmentation model
   .venv/bin/python scripts/get_models.py --list          # what else is available
   .venv/bin/python scripts/get_models.py --diar --check  # verify what is installed
   ```

   The default embedding model is `eres2net-base` (40 MB, trained on 200k
   speakers — the steadiest on mixed meetings); `--model eres2net-en`
   (27 MB, lighter, trained on English) and `--model eres2netv2` (71 MB, more
   accurate on similar voices, slower) are the alternatives. The script prints
   the URL before connecting, checks the file against a sha256 pinned in the
   script, verifies that it is really ONNX and not truncated, resumes an
   interrupted download on the next run, and puts the file where the daemon
   looks for it. Embedding models come from the
   [3D-Speaker project](https://github.com/modelscope/3D-Speaker) (Apache-2.0;
   ERes2Net works well for Russian and English), segmentation from
   [pyannote](https://github.com/pyannote/pyannote-audio), with ONNX builds
   assembled for sherpa-onnx. Your own link: `--url` (no checksum then).

   The script reaches the network only when you run it, once; the models work
   offline afterwards, and `--check` opens no connections at all. Besides the
   optional cloud layer, the product's only other network traffic is the
   version check and the first-run STT download — see [PRIVACY.md](../PRIVACY.md).

   When STT falls behind the audio (a backlog of two chunks, at least six
   seconds), live diarization yields: chunks go to recognition under the
   channel label until the queue drops below half a chunk. The recording on
   disk is not affected, and the after-meeting pass labels those minutes again.

   The live tracker holds up to eight voices on the remote channel and up to
   eight more on the microphone: with one shared limit, a call with seven or
   more remote speakers took every slot and left the owner's voice without a
   number. Once a channel's slots are taken, speech of a new voice on the
   remote channel is still transcribed — under the channel label, without a
   voice number; the after-meeting pass assigns the voices. On the microphone
   such speech is not transcribed live: the microphone label signs the owner,
   and a stranger in the room must not be signed with it. The simplified
   tracker mode (no segmentation model) keeps one shared limit for both
   channels.
2. **Offline re-pass** (after Stop): the full recording is re-diarized per
   channel with sherpa-onnx (the same two models). The system channel is
   clustered with the number of voices the live session heard as a hint,
   capped at eight (one channel's slots in the live tracker).
   When the system channel was recorded for most of the meeting and no speech
   was found in it (an in-room meeting) and the live session heard 3–12
   voices, the microphone is clustered without a hint and the shard merge
   runs under a veto: two groups never merge while any pair between them is
   below 0.46 (see "The merge threshold" below). With no system-channel
   recording at all (a machine without system audio, a stream that never
   opened) a room cannot be told from a call in headphones, so the live count
   from three voices goes to the microphone as an upper bound, with the shard
   merge after it. Limitation: a call where the other side stays silent the
   whole meeting is labelled as a room, and the owner's speech may spread over
   several neutral labels. A single
   microphone label with no call and 3–12 live voices gets no name (its speech
   cannot go to one person), and the transcript header says why. A failed
   system-channel diarization or a recording under 20 s is not silence.
   Microphone segments that overlap system-channel speech by more than half are
   dropped as echo; clusters with too little speech (under 25 s on the system
   channel, under 10 s on the microphone) go to the large cluster nearest in
   time, so no text is lost; names heard in the conversation are assigned by
   the local LLM behind trust guards (see "Names" below). The result replaces
   the live draft transcript.
   Without the recordings, or when neither channel yields segments, the live
   transcript stays as it is.

Without `models/diar/embedding.onnx` Charoite still works: channel labels
(you vs. the other side) are used instead of per-voice labels.

Tuning (`config/config.yaml`):

- `live_diarize` (default `true`) — the live pass on or off.
- `live_diarize_threshold` (default 0.45) — applies to the simplified mode
  only: cosine similarity to attach a chunk to a known voice; raise it if
  different people get merged, lower it if one person keeps splitting into two.
  The full mode uses its own measured threshold (0.62: 0.55 merged different
  people, 0.7 spawned extra voices).


### One sample axis per channel

The capture hub counts every sample it appends to a channel's recognition
buffer, including audio the buffer cap had to drop. A chunk handed to the live
labeller carries its start on that axis, and so does every raw block handed to
a frame listener (`add_frame_listener(fn(label, start, samples))`) — the same
number, from one counter, under one lock. A second labeller fed from the
blocks (the streaming Nemotron being prepared for the live contour) can then
be matched to chunks without counting positions itself. The recorded `.pcm`
holds each chunk at the same offset while writing to disk works; after a disk
failure the file stops short of the axis, and a block that failed to reach the
buffer is on the disk but not on the axis.

## Which voice is the owner

The owner's label comes from one rule shared by the capture, the daemon and
the rebuild (`src/channel_labels.py`, since 2026-08-30): the microphone
channel is labelled with `sufler.user_name`, or «Я» when the name is empty. A
name that looks like a neutral label («Собеседник 2», «Собеседник») cannot
sign anything — paragraphs are merged by label, and the rebuild picks the
audio track by it, so the other side's lines would be recognised from the
microphone; the owner's signature is then switched off and the app says so at
start. Before, three copies of this rule could disagree within one meeting.

The live transcript and the final one answer this with the same rule
(since 2026-10-02; before, the rebuild picked one dominant voice).

**In a call the microphone belongs to the owner entirely.** The other side
arrives on the system channel, so every non-echo microphone voice is the
owner — no matter how many fragments the lightweight tracker split them
into. The live path used to require a voice to be *dominant* in the
microphone; the tracker splits one person across several labels (measured
21.07: 8 voices live, 14 unnamed in the final), no fragment reached the
threshold, and the name went to nobody. People saw themselves as "Speaker 1"
for the whole conversation and got their name only in the final file.

Three caveats, each paid for with a bug:

- **Echo is sticky.** A voice once recognised as speaker bleed stays that way
  until the end. Counters decay, and without this the echo would "bleach" into
  the owner after a few minutes.
- **The decision is sticky.** The threshold is taken once; otherwise the label
  flickers between the name and "Speaker" on every pause.
- **The call flag does not depend on the tracker.** The diarization model is
  not bundled, and previously, without it, the flag was never raised — so on a
  remote meeting the rule never applied at all.

Echo is also compared by text: a microphone phrase that shares 80% of its
words (five distinct words at least) with a system-channel phrase within
eight seconds counts as a match. For now these matches only feed the
once-a-minute `owner-pulse` line in the err log (call flag, counters, echoed
voices, the current verdict) and do not change the signature: every guard
tried against false marking had a hole on one side, so switching it on waits
for field data.

The ⚡ gate leans on the same bookkeeping: a question from the microphone
triggers an instant answer only for a voice that is positively not the owner
(owners already known, this voice not among them); until the owner is
established the microphone triggers nothing, so the owner's own first
questions do not get answered back.

**An in-person meeting looks exactly like "no call"**: everyone sits in one
room and lands in the microphone, the system channel stays silent. That is why
the rule only engages when there is speech on the system channel.

**The rebuild applies the same rule** (`owner_voice.owner_voices`) to the
whole recording: in a call every microphone label left after the echo filter
is signed as the owner once they hold at least 15 s of speech together.
Before 2026-10-02 the rebuild picked one voice holding 60% of the microphone
speech and leading the next by 15 points; when the offline engine split the
microphone into several labels nobody won, and the final transcript replaced
the live name with «Собеседник N».

The call flag is the same idea but not the same fact. The live path raises it
on any chunk of the system channel; the rebuild raises it when the
system-channel diarization returned speech. If that diarization fails, or the
recording is shorter than 20 s, the final transcript treats the meeting as
in-person and leaves the labels neutral.

One rule has two costs:

- **A hybrid meeting.** A colleague at the same microphone gets the owner's
  name, live and in the final transcript. Telling voices apart inside the
  microphone needs a voice print of the owner, not a share of speech.
- **Echo residue.** The rebuild drops a microphone segment as speaker bleed
  when system-channel speech covers more than half of it. Bleed covered half
  or less stays: it used to be a neutral «Собеседник N», now it carries the
  owner's name.

## How to measure it

"It confuses speakers" stays an opinion until there is a number.
`scripts/diar_bench.py` computes DER (diarization error rate): the share of
speech time labelled wrongly — speech that was missed, speech heard in silence,
and time given to the wrong voice. Hypothesis labels are matched against the
reference first, so "spk0 instead of Milena" is not an error: diarization must
tell people apart, not guess names.

```bash
.venv/bin/python scripts/diar_bench.py --make    # synthetic dialogue + ground truth
.venv/bin/python scripts/diar_bench.py           # measure both engines (live, sherpa)
```

`--engine live-split|live-legacy|all` picks another mode, `--overlap` slices
the audio the way production does. Measuring needs the two models above.

There are no meeting recordings in this repository and there cannot be — those
are other people's conversations. The fixture is built locally with the macOS
speech synthesiser: four different voices read lines, and the ground truth is
exact because we wrote it. This is a floor, not a benchmark: synthesised voices
are cleaner than live ones, with no crosstalk and no room noise, so an engine
that confuses speakers HERE will do worse in a real meeting. The converse does
not hold — these numbers must not be presented as real-meeting quality.

Measured on 2026-07-30 (32 s, 4 voices):

| Engine | DER | Voices found |
|---|---|---|
| segment tracker (`--engine live`: segmentation + per-utterance embeddings) | **0.246** | 4 of 4 |
| previous chunk tracker (`--engine live-legacy`; today's simplified mode without the segmentation model) | 0.725 | 1 of 4 |
| after-meeting pass (`--engine sherpa`) | 0.296 | 3 of 4 |
| same, told there are 4 speakers | 0.248 | 4 of 4 |

The previous tracker collapsed everyone into a single voice, and the threshold
barely mattered: from 0.25 to 0.55 the result was identical. The cause was not
the threshold but the fact that speech was cut by a timer (three-second chunks)
rather than at utterance boundaries: one chunk holds the end of one phrase and
the start of another, and the embedding comes out mixed. Segmentation gives the
boundaries, and the embedding is computed per utterance.

Since 2026-08-15 recognition goes per utterance too — positional layout
(`SegmentTracker.split`). When several people spoke inside one chunk in pieces
of at least a second, the daemon transcribes the pieces separately and each
gets its own author; text at utterance boundaries stops leaking to the wrong
voice. Three rules guard against "micro-labels": a foreign piece shorter than
a second is attributed to no one (losing a half-second "yes" is more honest
than faking its author — the short-shard rule of the post-meeting pass),
neighbouring pieces of the same voice merge into one window, and window
padding never crosses the midpoint of the gap to another voice's speech.
A segment clipped by the right chunk edge that lives entirely inside the
overlap zone is deferred — the next chunk brings it whole.

Measured on the same fixture but with production slicing (3.0 s chunks,
2.5 s step, `--overlap` — the old bench cut end-to-end and flattered itself):

| Live mode | DER | Confusion | Voices |
|---|---|---|---|
| one label per chunk (`--engine live`) | 0.270 | 0.162 | 4 of 4 |
| positional layout (`--engine live-split`) | **0.167** | **0.054** | 4 of 4 |

Speaker switches are equal in both modes — the transcript did not flicker.

The bench also prints the run time and RTF (time divided by recording
length, model loading included) for every engine.


## Crosstalk, and the Nemotron experiment

Both passes above give every piece of speech one voice. When two people talk
at once — an interruption, an argument — the second voice is either lost or
blended into a mixed embedding that resembles nobody. The first fixture could
not show this: its replies never overlap.

`--make --crosstalk` builds a second fixture (`data/diar_bench_crosstalk`)
where four replies start 0.8–1.5 s before the previous one ends. With
overlapping ground truth, DER is scored the NIST way: a frame may hold several
voices, too few voices is a miss, too many is a false alarm. Without overlaps
this is exactly the old metric when the hypothesis has no overlaps either —
the tests hold that equality, and the live numbers on the plain fixture
reproduce to the digit. A hypothesis with overlaps of its own (sherpa can emit
them) is scored the NIST way too and may read higher than the archived number,
which laid it out "last one wins". A reference without a single frame of
speech (an empty file, point labels only) is refused before any engine runs:
DER is undefined there, not perfect. The live
tracker names one voice per chunk by design, so its overlapping chunk windows
are still laid out "last one wins" rather than counted as a second voice.

[Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization)
(NVIDIA, September 2026) is an end-to-end streaming Sortformer, ~100M
parameters, up to eight speakers: for every 10 ms frame it gives each voice
its own activity probability, so two voices in one frame is a normal answer.
`src/diarize_nemotron.py` wraps its MLX port. The post-meeting pass uses it
behind a switch (see [Post-meeting pass on Nemotron](#post-meeting-pass-on-nemotron));
the live contour and the daemon do not call it, the bench does.

Apple Silicon only. Neither the package nor the weights are app dependencies.
For the product, `scripts/install_engine.py nemotron` installs both (see
[Post-meeting pass on Nemotron](#post-meeting-pass-on-nemotron)). The bench runs
the engine inside its own process, so for it they are one-time manual steps in
the development venv (paths are relative to the repo root; with `CHAROITE_ROOT`
set, the bench prints the exact folder):

```bash
.venv/bin/pip install "mlx-audio==0.5.6"
.venv/bin/hf download mlx-community/Nemotron-3-Diarization --local-dir models/diar/nemotron
.venv/bin/python scripts/diar_bench.py --make --crosstalk
.venv/bin/python scripts/diar_bench.py --crosstalk --engine compare
```

- `compare` runs `live-split` (always with the daemon's slicing — `--overlap`
  is implied, so the live number is the one the daemon gets) and `sherpa`
  against `nemotron` (the whole file,
  30.4 s buffer — the model's ceiling) and `nemotron-live` (a stream in 0.5 s
  blocks, as the daemon would feed it). `--nemotron-preset` picks the stream
  latency: `low` 1.04 s (default), `very_low` 0.64 s, `ultra_low` 0.32 s.
- **Version.** The version the seam was checked on lives in
  `MLX_AUDIO_VERSION`, and a test keeps the recipe above equal to it. Another
  `major.minor` branch or a package without distribution metadata is refused
  before the run with the recipe; the same branch with another patch, a dev or
  a local build gets a warning and the run goes on.
- **Network.** The loader accepts only a local folder with `config.json` and
  weights and hands mlx-audio a `pathlib.Path`, for which the library does not
  go to the hub. A repository id is refused with the download recipe, not
  downloaded.
- **Voices.** The voice cache lives in the stream state in RAM and dies with
  it; our code writes nothing derived from a voice to disk — the static guard
  `tests/test_no_voice_biometrics.py` and a run of the wrapper with a stub
  model hold that. What mlx-audio itself writes is measured below: nothing but
  a filesystem probe of `filelock` on import.
- **License.** The weights are under NVIDIA OpenMDW 1.1, not Apache-2.0, so
  they are never committed; mlx-audio also pulls `transformers`, which is why
  it is not in `pyproject.toml`.
- **Cost of streaming.** Every stream step re-encodes the voice cache and the
  FIFO (up to ~530 frames) for a short new chunk, so a second of audio costs
  much more than in the whole-file pass: on a random-weight run on a Linux CPU
  (plumbing only, not quality) the stream took ~16× the whole-file time. On an
  M1 Max the stream costs RTF ≈ 0.04 and the whole-file pass ≈ 0.003 (below).
- **Russian** is not in the model card's training languages. Diarization
  depends on language less than recognition does — but that is exactly what
  to measure, not assume.

### First numbers (M1 Max, 28 September 2026)

mlx-audio 0.5.6, `mlx-community/Nemotron-3-Diarization` (bf16), time includes
model loading. The post-meeting pass below is the production `diarize.diarize()`
(clustering threshold 0.8, shard merging at 0.60), not the bench's `sherpa`
engine, which runs sherpa-onnx with other settings (card №472).

**Real meetings with a reference.** Two AMI test meetings (Mix-Headset, four
speakers, English), reference from pyannote's AMI-diarization-setup
(`only_words`), no collar. The reference is strict, so absolute numbers sit
above published ones; the comparison between engines is what counts.

| Engine | ES2004a (17.5 min) | IS1009a (14.0 min) | RTF |
|---|---|---|---|
| post-meeting pass (production) | 0.606 · confusion 0.393 · 3 voices | 0.453 · confusion 0.266 · 4 voices | 0.35–0.36 |
| live-split (daemon slicing) | 0.676 · 8 voices | 0.541 · 9 voices | 0.06–0.07 |
| nemotron (whole file) | **0.269** · confusion 0.004 · 4 voices | **0.277** · confusion 0.071 · 4 voices | 0.003 |
| nemotron-live (`low`, 1.04 s) | 0.273 · 5 voices | 0.273 · 4 voices | 0.04 |

Nemotron barely confuses voices; most of its error is missed speech — short
words between pauses that the word-level reference counts. The production pass
merged 19 clusters into 2 on ES2004a: four people came out as three voices,
with four speaker switches in 17 minutes.

**Russian, no reference.** The other side's channel of two ~20-minute work
calls: the production pass, Nemotron and its stream found 4 / 4 / 4 voices in
one and 9 / 8 / 8 in the other; the meeting notes name 3–4 and 7–8
participants. A sanity check of the language, not a DER.

**Synthetic fixtures mislead this model.** On the crosstalk fixture Nemotron is
nearly perfect: DER 0.039, 4 of 4 voices, all 4.5 s of overlap found
(live-split 0.405, the bench's sherpa 0.391, the stream 0.32–0.34 across
presets). On the plain fixture without overlaps it puts all four macOS voices
into one slot: DER 0.628, slot 0 ≈ 0.9 on every reply. Length is not the
cause — a 27 s cut and 6 s of added silence give the same. The model opens a
new speaker when voices overlap, and synthetic voices taking turns look like
one person to it. Judge it on real recordings, not on `say`.

**Chunk seams.** In the raw stream output 64–74 % of the gaps between pieces
of one voice are exactly zero — the chunk cuts; under 1.2 % are shorter than
0.08 s. `MERGE_GAP_S = 0.0` glues the cuts, and the glued stream matches the
whole-file pass: 306 vs 305 and 236 vs 220 speaker switches, DER 0.273 vs
0.269 and 0.273 vs 0.277. A 0.5 s gap lowers the AMI DER to 0.219 / 0.233 by
filling pauses inside a turn — a transcript policy for the integration, not a
seam artifact.

**Nothing on disk.** Under a macOS sandbox that denies writes to `/Users`,
`/private/var`, `/private/tmp` and `/private/etc`, loading, the whole-file pass
and the stream over 300 s of a real recording complete. A Python audit hook
sees one kind of write in the whole path: `filelock`, pulled in by the loader
through huggingface_hub, probes symlink behaviour on import — an empty
temporary folder with two empty files, removed at once. No network events.

**Verdict.** On real meetings Nemotron beats the current post-meeting pass by
a wide margin at a hundredth of its time — so the pass can run on it.

### Post-meeting pass on Nemotron

The transcript rebuild labels the call channel with Nemotron when the config
says so (`config.yaml`, section `sufler`):

```yaml
diarize_backend: nemotron   # sherpa by default
nemotron_python: ""         # empty: the environment scripts/install_engine.py installed
```

The environment is installed by the product, once, on your command — with the
app's own Python and your data folder named (the doctor prints the whole command — with
the app's interpreter when it can find it, and with your data root):

```bash
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron          # install
CHAROITE_ROOT=<data folder> <app python> scripts/install_engine.py nemotron --check  # what is there, no network
```

In the app the interpreter is `Charoite.app/Contents/Resources/python/bin/python3`,
and the data folder is the one under Settings → Data folder
(`~/Library/Application Support/Charoite` by default). Without `CHAROITE_ROOT` the
installer refuses: guessing the root from its own location would put the environment
inside the signed `.app`, and a data root inside an `.app` is refused as well.
The installer prints where it goes on the network before connecting, checks the
machine against the header of `requirements-nemotron.lock` (Apple Silicon, macOS
14 or newer — mlx wheels start there — and the Python the lock was built for),
copies the running interpreter without the app's packages into
`engines/nemotron` under the data root, installs the lock with
`pip --require-hashes --no-deps`, fetches the weights from a pinned revision with
sha256 checks (`scripts/get_models.py`), runs the engine on a second of silence
through the same door the rebuild uses, and only then swaps the folder in by
rename; a failure anywhere leaves the previous environment as it was. A copy,
not a venv: a venv keeps the path of its base interpreter, and an app started
from Downloads with quarantine runs from a random path (App Translocation), so a
venv over the bundle would break on the next launch; python-build-standalone is
relocatable, and the copy keeps the bundle's signature and entitlements, so the
mlx wheels load. `nemotron_python` still takes an explicit interpreter, `~/…`
included; the path is used as written, symlinks unresolved — a venv's
`bin/python` is a symlink and has to stay one. Empty means the installed
environment. The lock is rebuilt with
`.venv/bin/python scripts/lock_runtime_deps.py nemotron` from `MLX_AUDIO_VERSION`
in `src/diarize_nemotron.py`.

The app's own Python has no mlx, and its bundle is signed, so nothing is
installed into it: the engine runs as a separate process of that interpreter.
`diarize_in_env` starts `src/diarize_nemotron.py` through one door,
`src/foreign_python.py`: a clean child environment (the readiness-probe recipe
— `PYTHONSAFEPATH`, no user site, no inherited `PYTHONPATH`/`PYTHONHOME`, no
bytecode, plus the variables of someone else's venv) and a typed outcome. The
weights are read from `models/diar/nemotron` under the data root, the
environment lives in `engines/nemotron`; both paths come from the engine module
(`model_dir`, `engine_dir`).

- **Fallback, visible.** No interpreter, no package, no weights (exit code 10,
  `EXIT_ENGINE_UNAVAILABLE`), a crash, a timeout (60 s plus a tenth of the
  recording) or a reply off the protocol — the meeting is labelled by sherpa as
  before, and the transcript header says so: «Голоса собеседников размечены
  запасным движком (sherpa): Nemotron не разметил голоса — причина в журнале
  разбора (logs/), проверка — доктор (scripts/doctor.py)». The raw refusal
  (interpreter path, the engine's last stderr line with weight paths and the
  download recipe) goes to the rebuild log only: transcripts are forwarded to
  people, and a local path carries the account name. A misspelt
  `diarize_backend`, a reply with no segments or one segment over the whole
  recording fall back the same way, each with its own short reason.
- **What happens to the segments.** Shorter than 1 s — dropped, as sherpa's
  are. The call channel then loses overlaps, whatever the engine: a partial
  overlap is split in the middle; a reply longer than 1 s inside someone else's
  cuts it in three; a shorter one goes to the enclosing voice — its sound is
  still transcribed inside that segment, only the authorship is lost, as with a
  merged fragment. Every moment is transcribed once.
- **Fragments.** A call voice with less than 5 s of speech over the meeting is
  merged into its nearest neighbour (sherpa: 25 s — it shatters voices into
  shards, Nemotron does not). Measured on five recordings of 28.09: voices with
  10–22 s are real people with a reply or two, 2–4 s is noise; recalibration
  after a week of real meetings is card №476. The mic channel keeps its 10 s.
- **Speaker echo in the mic.** A mic segment more than half covered by call
  speech is dropped as echo, and the share is taken over the *union* of the
  call segments, not one at a time: Nemotron cuts a turn into pieces, and echo
  over three pieces of 30 % each used to pass as live speech. On an 8-minute
  call of 28.09 one-at-a-time kept 69 % of the mic speech with Nemotron against
  17.5 % with sherpa; the union keeps 17.8 %.
- **Time.** The call channel of a 41-minute meeting: 5 s including the process
  start and the model load; 20 minutes: 3 s; 8 minutes: 2 s. The production
  sherpa pass on one thread runs at RTF 0.35 — about 7 minutes for 20 minutes
  of call; on two threads see below.

### Threads, and yielding to a live meeting

sherpa-onnx runs one thread unless told otherwise, and after a 69-minute meeting
on 29 September the microphone channel took about 22 minutes on one core of
eight. Every sherpa config is now built by one factory, `src/sherpa_config.py`:
the live trackers always get one thread; the pass after a meeting gets one
thread while a recording is running, and otherwise the largest admitted thread
count — measured to give the same labels and cheap enough next to a live
meeting — capped at half the performance cores.
A test forbids building a sherpa segmentation or embedding config anywhere else.

The measurement (29 September, sherpa-onnx 1.13.7, M1 Max) went through the
production path `rebuild_transcript.diarize_channel` → `diarize.diarize()`,
shard merging included on the microphone: 20 minutes of a meeting's microphone
and 20 minutes of a call channel with an eight-voice hint.
On 2 and 4 threads the segments are byte-identical to one thread — 0 of 101,001
and 0 of 96,356 speech frames differ — and the pass runs 1.53 times faster on 2
threads and 2.28 times on 4 (RTF 0.41 → 0.27 → 0.18 on the microphone). The list
holds 1 and 2. A pass that started before a meeting keeps its threads to the end,
and next to it the live speech recognition (8-second chunks) loses 55 % at p95
with 4 threads under `nice 10` and 9 % with 2; 4 threads wait until a pass can
yield to a meeting mid-way. Another sherpa-onnx or onnxruntime version is a
reason to measure again.

The rebuild yields to a live meeting before each channel's labelling and
before speech recognition (up to ten minutes each, like names and minutes), not
only when it enters the rebuild queue: a rebuild that waited behind another one
does not know about a meeting that started meanwhile.

### Live stream in shadow

The live contour still labels call voices with the ERes2Net tracker. Before
it waits for Nemotron's labels instead, three numbers are needed from real
calls: how long a chunk would wait for its label, what a second model process
costs next to the meeting, and whether the live slots agree with the pass
after the meeting. `sufler.live_nemotron: shadow` collects them and changes
nothing in the transcript (default `off`).

- **The process.** `diarize_nemotron.py --stream --preset low` runs under the
  engine's interpreter (the same environment the post-meeting pass uses), via
  `foreign_python.spawn_stream`: a long-lived child in the clean environment,
  audio in on stdin as s16le at 16 kHz, one JSON line per protocol message on a
  duplicate of stdout (anything a library prints goes to its log,
  `logs/nemotron_live_<stamp>.err`, owner-only). The handshake `ready` names
  the protocol version, rate, preset, frame length and block; the child then
  sends `seg` (start, end, slot, open) and `front` (samples fed, frames
  labelled, its CPU seconds and peak memory) after every 0.5 s block. The frame
  is the model's native 10 ms spectrum frame (hop / rate, the unit of its
  streaming state), and every `front` is checked against the audio fed: frames
  never ahead of it, the final one covering it to within a frame (measured on
  mlx-audio 0.5.6, all four presets). A broken check means the library changed
  its unit — the child exits and the shadow ends, rather than writing a wrong
  front; the journal's `end` line carries the check's numbers (the last line of
  the child's log names the reason of any exit). A model whose rate is not the
  stream's is refused before the handshake.
- **The axis.** The call channel reaches the child through a frame listener
  (see "One sample axis per channel"). Blocks before the handshake are not
  kept: the stream starts at the first block after it, `start0` on the hub's
  axis, and the child's seconds are offsets from there. A block that does not
  start where the previous one ended stops the shadow — labels are never
  shifted silently. More than 10 s of audio queued for the child stops it too:
  that is the answer "it does not keep up", not a reason to buffer.
- **Memory.** The stream yields for its own footprint, not for the machine's
  pressure (#579). Every 5 s, on a child's `front`, it writes the pressure level,
  the swap in use (`sysctlbyname`, no process) and the child's footprint
  (`phys_mb` from that `front`) into a `mem` line. The child's footprint above
  2048 MB on two checks in a row stops the stream (a leak, or the MLX cache cap
  gone); the critical pressure level 4 stops it at the first check and keeps it
  from starting — the recording wins. So does the child's own log growing past
  20 MB. A child that sends no `phys_mb` gets one `budget` line per meeting: the
  footprint budget is not watched. Level 2 (warning) no longer stops anything:
  on 01.10, on a 64 GB M1 Max with the 35b and 4b chat models loaded, it held
  without the stream (an A/B phase without the child: 70 % of the samples) and
  without swap (a live call), stopping the stream freed under 1 GB, and in the
  `on` mode it handed the labels to the tracker for the rest of the call — the
  stream lived 2–5 minutes on three calls in a row. At 1× the stream cost the
  hint model about 0.2–0.3 s on a 4.4 s answer (median). History: a lab A/B on
  29.09, before the MLX cache cap, sent 2.1–8.3 GB to swap per 4-minute phase
  with the stream; the swap storm of 31.08 made a hint take 19.7 s.
- **Chunks.** Every chunk the recognizer accepts gets exactly one journal line,
  written without waiting: when the child's front passes the chunk's end (the
  line holds the seconds per slot inside the chunk, how long it waited, how far
  the front was behind), at once if the stream has not started or has died,
  after 120 s without a front, or at the stop. The line also records what the
  live tracker did with the chunk: `shed`, `pieces`, `none`, `split_failed` or
  `off`.
- **The journal.** `logs/nemotron_live_<stamp>.jsonl`, owner-only: numbers and
  stop reasons — no audio, no utterance text. Lines: `header`, `ready`, `start` (`start0`),
  `seg`, `front`, `mem`, `budget` (no footprint from the child), `chunk`, `end` (reason
  and counters). A reconciliation cuts the
  recording at `start0` into the same blocks.
- **Stop.** At the end of the meeting, before the post-meeting pass starts,
  the child gets EOF, flushes its tail and exits; a child still alive after 1 s
  is killed, well inside the app's 5 s grace before it kills the daemon. The
  daemon waits for the `end` line last, after the recordings are finalized, and
  any child still alive when the daemon exits by any path except SIGKILL is
  killed on the way out. A shadow that died on the call says so once in the
  status line.
- **Capture never waits for the shadow.** The shadow's frame listener only
  updates state under its lock; journal lines, the child-log size check and the
  memory probe run outside it, so a slow disk under `logs/` delays the journal,
  not the recording.

### Live labels (`on`)

`sufler.live_nemotron: on` puts the stream's labels into the live transcript for
the call channel. The rollout is `off` → `shadow` on a live call → `on`.

- **The tracker stays.** `on` requires the segment tracker (`models/diar`): its
  voice numbers feed the echo check between the microphone and the call
  channel, and its layout is the fallback. Without it `on` is refused with the
  reason and no child starts.
- **The wait.** A call-channel chunk waits for its stream label at most 2 s
  (`WAIT_CAP_S`) — not under the shadow's lock, on its own journal entry. The
  label is usually ready 0.8 s of audio after the chunk end (p50, max 1.3 s).
  A stream that has not started, is dead, is further behind than the cap, or
  hears no speech in the chunk leaves the chunk to the tracker; the journal line
  says why (`fallback`).
- **The layout.** Stream segments go through the same windows and rules as the
  tracker's (`plan_pieces`, `settle`). Speech the tracker hears and the stream
  did not label is transcribed as its own tracker piece; stream windows do not
  pad into it.
- **Labels.** One label per stream slot for the whole meeting, shown in the feed
  only after 5 s of that slot's speech (`SLOT_SHOW_S`); before that the piece goes
  under the channel label. The engine opens ghost slots with 0–9 s of speech per
  meeting, and each became a "Speaker N". New labels after 60 s of silence were
  dropped: on two recordings the labels were exactly as pure without them, and
  69 minutes with six people gave 33 labels instead of 7. The model has 8 slots: on
  a call with more than 8 people, two people share a label.
- **Tracker pieces** (the stream is not up yet, timed out or heard nothing) take
  the stream label linked to their voice. A voice with no link goes under the
  channel label while the stream has not died. A tracker number would start one
  "Speaker N", and the stream label of the same person a second one. The stream
  label does not inherit the tracker's name: the tracker merges people, and the
  name would go to the wrong one. So the start of a meeting, before the
  handshake (usually 1–3 s, at most `HANDSHAKE_S` = 120 s), and every chunk the
  stream did not label run under the channel label. Once the stream has died, the tracker's
  own numbers come back, as in `off`.
- **Memory.** Every child — shadow or `on` — runs with the MLX cache capped at
  512 MB: replaying two recordings, the child's footprint stayed at 0.8–0.9 GB
  with the same labels, 0.8–1.0 GB at 1× next to the 35b and 4b models (without
  the cap a live shadow grew to 8.5 GB and was stopped by memory pressure).
- **The journal** is version 2: every chunk line names its layout source and the
  fallback reason; `scripts/nemotron_shadow_check.py` reads versions 1 and 2.

### Your own recording

The synthetic fixture is a floor. A real verdict needs a real meeting — which
cannot live in the repository, so it is measured where it lies:

```bash
# the other side's channel of a recent meeting: mono 16 kHz, as Charoite writes it
.venv/bin/python scripts/diar_bench.py --wav recordings/<stamp>_blackhole.wav \
    --engine compare --labels /tmp/diar_labels
# with a hand-made reference for a fragment
.venv/bin/python scripts/diar_bench.py --wav fragment.wav --truth fragment.txt --engine compare
```

The quickest reference: take 3–5 minutes of a heated stretch, label it in
Audacity (select a reply, Ctrl+B, type the name), File → Export → Labels.
RTTM and the bench's JSON are accepted too. Without `--truth` DER is not
computed; the bench prints what each engine found (voices, speech, overlap,
switches), and `--labels` writes each engine's hypothesis as an Audacity label
track (File → Import → Labels) to listen where they disagree.


## The merge threshold is measured, not guessed

Segmentation often splits one person's speech across several clusters —
especially in a room recorded by a single microphone: someone turns away,
leans back, drops their voice. The merge step compares average cluster
embeddings by cosine and joins the close ones. It runs when clustering picks
the number of voices itself: on the microphone channel (with no system-channel
recording also after the hint, which is then only an upper bound), and on the
system channel when the live session gave no usable hint.

In a room one bridge is not enough. Single linkage joins two groups through
one pair above the threshold, and on Sep 29 a meeting of six chained through
such bridges into one label with pairs of 0.13 inside it. On the microphone of
an in-room meeting the first merge pass goes pair by pair from the most
similar and refuses to join two groups while any pair between them is below
0.46 — the top of the "different people" range; groups under 30 s are still
attached to the nearest voice by the second pass, as before. On that recording: one label became four. On
calls the veto is off: there it split the owner into several labels (measured
on eight calls: four of them).

On Aug 14 the threshold was measured on a real recording: 65 minutes, one
microphone, three speakers. Pairwise similarity split cleanly:

| Compared | Similarity |
|---|---|
| pieces of the same voice | 0.68 – 0.89 |
| different people | 0.11 – 0.46 |

Between 0.46 and 0.68 there is an empty band, and that is where the line
belongs. The previous **0.72 sat inside the "same voice" range**: a pair at
0.68 was never merged, and one person reached the transcript as two
"speakers". The threshold is now **0.60**, clear of both edges.

**Quiet clusters are handled separately.** Fillers like "yeah" and "mhm" give
a short signal, the embedding from it is noisy, and it never reaches the main
threshold. Such clusters are obvious by volume: on that same meeting three
participants held 92% of the text while thirteen shards had a second or two
each — two orders of magnitude apart. So a cluster with less than **30
seconds** of speech is not treated as a separate participant and goes to the
nearest voice at a softer **0.50** — above the maximum for strangers, so a
stranger's line is never handed to a participant.

**Clusters the merge cannot see at all.** An embedding needs at least a second
of continuous speech. A cluster without such a piece is never compared to
anything — no threshold can reach it, there is simply nothing to compare. On
that same recording **8** clusters out of 74 turned out this way: 6.9 seconds
of speech between them out of 1432 (half a percent of the time), usually a
single 0.6–1.0 s remark. A live participant does not fit into that, yet they
spawn labels on par with people.

Such clusters go into **one shared voice** rather than being spread across
participants. We do not know who exactly said "yeah", and handing it to a
specific person would swap the author; a shared label honestly says "short
remarks, voice unidentified".

What the merge never does: it never joins two speakers who both cleared the
speech minimum, however similar they sound, and never attaches a shard that
resembles nobody present. An extra label is honester than a wrong author.

## Names: when a label gets one

A name replaces «Собеседник N» retroactively across the whole meeting, and the
minutes and the graph inherit it, so a wrong name costs more than a missing
one. The live naming and the after-meeting pass go through the same guards
(`src/speaker_names.py`; the rebuild uses them since 2026-09-13):

- **The owner's name never goes to someone else.** It is compared by the words
  of `user_name`, so a first name alone is recognised; in the final transcript
  the owner's label never inherits a name either.
- **A name nobody said is invented** and is dropped. It counts as heard when
  it occurs as a whole word, or in the vocative or instrumental form
  («Тань» → «Таня», «Колей» → «Коля»).
- **A name heard only in the label's own lines**, without an introduction
  («это…», «меня зовут…»), is the speaker addressing someone else.
- **Case forms are brought to known people of the graph.** A vocative is
  reversed exactly («Коль» → «Коля») and only when it matches one known
  person; otherwise a four-letter prefix does it («Полин» → «Полина»). A stop
  list keeps «Влад» from turning into «Влада».
- **The voice vetoes a clear contradiction.** The median pitch of the label's
  speech (`src/voice_pitch.py`) rejects a confidently female name for a
  confidently low voice and the reverse; between roughly 145 and 190 Hz it
  decides nothing. The pitch lives in the meeting's memory and is never
  written to the transcript, the graph or any document.

The graph adds two rules of its own (see [FEATURES.md](FEATURES.md)): a vocative
does not create a second person node, and a person who never spoke and is
mentioned at most once — a name called out in the background of a recording —
does not become a meeting participant: the note keeps the name as a line
«фон записи, не участник (узел не создан)» — background of the recording, not
a participant, no node — so a wrong call is visible and fixable by hand.
