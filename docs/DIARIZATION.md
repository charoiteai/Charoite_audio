# Speaker diarization setup

Charoite uses two diarization passes:

1. **Live** (during the meeting): speaker-embedding model labels chunks as
   «Собеседник 1/2/…» in real time. Requires an ERes2Net embedding model in
   ONNX format at `models/diar/embedding.onnx` (512-dim output, 16 kHz input).
   Get one from the [3D-Speaker project](https://github.com/modelscope/3D-Speaker)
   (ERes2Net trained on CN-Celeb/VoxCeleb works well for Russian and English) and
   export/download it as ONNX.
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
