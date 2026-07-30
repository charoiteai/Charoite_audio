# Speaker diarization setup

***English** · [Русский](DIARIZATION.ru.md) · [中文](DIARIZATION.zh.md)*

Charoite uses two diarization passes:

1. **Live** (during the meeting): speaker-embedding model labels chunks as
   «Собеседник 1/2/…» in real time. Requires an ERes2Net embedding model in
   ONNX format at `models/diar/embedding.onnx` (512-dim output, 16 kHz input).
   One command installs it:

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
| live tracker (cosine to centroids) | 0.725 | 1 of 4 |
| sherpa-onnx: pyannote segmentation + the same embedder | 0.296 | 3 of 4 |
| same, told there are 4 speakers | **0.248** | 4 of 4 |

The live tracker's threshold barely matters: from 0.25 to 0.55 it collapses
everyone into a single voice. The cause is not the threshold but the fact that
speech is cut by a timer (three-second chunks) rather than at utterance
boundaries: one chunk holds the end of one phrase and the start of another, and
the embedding comes out mixed. That is exactly what a segmentation model fixes.
