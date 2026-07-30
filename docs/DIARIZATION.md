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
