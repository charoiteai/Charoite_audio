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
2. **Offline re-pass** (after Stop): the full recording is re-diarized per
   channel with sherpa-onnx (the same two models). The system channel is
   clustered with the number of voices the live session heard as a hint;
   microphone segments that overlap system-channel speech by more than half are
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


## Which voice is the owner

The owner's label comes from one rule shared by the capture, the daemon and
the rebuild (`src/channel_labels.py`, since 2026-08-30): the microphone
channel is labelled with `sufler.user_name`, or «Я» when the name is empty. A
name that looks like a neutral label («Собеседник 2», «Собеседник») cannot
sign anything — paragraphs are merged by label, and the rebuild picks the
audio track by it, so the other side's lines would be recognised from the
microphone; the owner's signature is then switched off and the app says so at
start. Before, three copies of this rule could disagree within one meeting.

The live transcript and the final one answer this differently, on purpose.

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

**The rebuild decides independently**, over the whole recording at once,
rather than repeating the live decision: it has all the audio, the live path
has a sliding window and inertia. On a call both agree; on a meeting that
changes format mid-way they may differ, and the one with more data is right.

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


## The merge threshold is measured, not guessed

Segmentation often splits one person's speech across several clusters —
especially in a room recorded by a single microphone: someone turns away,
leans back, drops their voice. The merge step compares average cluster
embeddings by cosine and joins the close ones. It runs when clustering picks
the number of voices itself: always on the microphone channel, and on the
system channel when the live session gave no usable hint.

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
