# Speaker diarization setup

***English** · [Русский](ru/DIARIZATION.md) · [中文](zh/DIARIZATION.md)*

Charoite uses two diarization passes:

1. **Live** (during the meeting): speaker-embedding model labels chunks as
   «Собеседник 1/2/…» in real time. Requires an ERes2Net embedding model in
   ONNX format at `models/diar/embedding.onnx` (512-dim output, 16 kHz input).
   One command installs it:

   **Easiest: the "Tell speakers apart" button in the app's first-run
   wizard.** The same thing from the terminal is below.

   ```bash
   .venv/bin/python scripts/get_models.py --diar          # default model
   .venv/bin/python scripts/get_models.py --diar --list   # what else is available
   .venv/bin/python scripts/get_models.py --diar --check  # verify what is installed
   ```

   The script prints the URL before connecting, verifies that what arrived is
   really ONNX and not truncated, and puts the file where the daemon looks for
   it. Models come from the [3D-Speaker project](https://github.com/modelscope/3D-Speaker)
   (Apache-2.0; ERes2Net works well for Russian and English), with ONNX mirrors
   assembled for sherpa-onnx. Your own link: `--url`.

   This is the only place in the product besides the optional cloud layer that
   reaches the network: only when you run it, once, offline afterwards.
   `--check` opens no connections at all.
2. **Offline re-pass** (after Stop): the full recording is re-diarized per
   channel, echo between mic and system audio is filtered, micro-fragments are
   merged into neighbours, and names heard in the conversation are assigned by
   the local LLM. The result replaces the live draft transcript.

Without `models/diar/embedding.onnx` Charoite still works: channel labels
(you vs. the other side) are used instead of per-voice labels.

Tuning (`config/config.yaml`):

- `live_diarize_threshold` (default 0.45) — cosine similarity to attach a chunk
  to a known voice; raise it if different people get merged, lower it if one
  person keeps splitting into two.


## Which voice is the owner

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
.venv/bin/python scripts/diar_bench.py           # measure both engines
```

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
| live mode today (segmentation + per-utterance embeddings) | **0.246** | 4 of 4 |
| previous chunk tracker (`--engine live-legacy`) | 0.725 | 1 of 4 |
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
embeddings by cosine and joins the close ones.

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
