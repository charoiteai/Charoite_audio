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

A chunk still gets one label — it carries one piece of recognised text, so the
voice that spoke longest in it wins. If recognition also goes per utterance,
errors halve again: the measured DER of per-utterance labelling is 0.090.


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
