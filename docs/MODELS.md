# Why these models

Everything runs locally. Below is the reasoning for each default: our own
benchmarks on an M1 Max (32 GB) plus independent sources. Every choice is
replaceable in the config.

## STT: GigaAM v3 (default)

`gigaam-v3-e2e-rnnt` via [onnx_asr](https://github.com/istupakov/onnx-asr) —
a Russian ASR model by [Sber](https://github.com/salute-developers/GigaAM), MIT.

- **Speed**: a 3-second chunk transcribes in ~0.1–0.6 s on M1 Max — live
  transcript latency is dominated by STT, and this leaves headroom.
- **Russian quality**: on real meetings it is clearly more accurate than
  whisper-large-v3-turbo — fewer hallucinations on short chunks, more robust
  to domain terms and acronyms.
- **Built-in punctuation & capitalization** (e2e model) — critical because
  the trailing «?» is the main trigger for instant answers, and it makes
  transcripts readable as-is.
- The model downloads automatically on first run.

Config alternatives: `whisper` (mlx, 100+ languages) and `parakeet`
(English, extremely fast) for non-Russian meetings.

## Main LLM: qwen3.6:35b-a3b

MoE: ~35B total, ~3B active parameters — 30B-class quality at small-model
speed.

- **Our benchmark on a real meeting transcript**: first token in 0.27 s,
  full answer in 2.2 s — vs 1.08 s / 4.5 s for dense gemma4:26b, while
  holding the assistant role more reliably (gemma confused who said what).
- **The 30B class is the floor for structured/graph extraction** — not our
  preference but an industry observation:
  [LightRAG](https://github.com/hkuds/lightrag) names Qwen3-30B-A3B a
  reasonable minimum for entity extraction;
  [Graphiti](https://github.com/getzep/graphiti) warns that very small
  models break the JSON schema; on the schema-guided KG benchmark
  [OSKGC](https://ceur-ws.org/Vol-4041/paper1.pdf) 7–8B models lose
  ~0.1 Micro F1 vs frontier and struggle most with ontology compliance.
  Charoite builds a knowledge graph, so going below the 30B class is not
  an option.
- `think: false` everywhere: reasoning mode moves output into the thinking
  field (empty content) and adds ~10 s of latency.

## Light model: gemma4 (e4b)

Live theses, classification, dialogue markup inside paragraphs, draft
minutes — everything that must run every few seconds in parallel with the
main model.

- ~0.36 s per classification, ~92% accuracy on our tasks.
- Fits in RAM alongside qwen (~30 GB total) so background loops never
  compete with the main model for loading.

## Diarization: ERes2Net (3D-Speaker)

Speaker embeddings — [ERes2Net](https://github.com/modelscope/3D-Speaker)
(ONNX, 512-dim).

- **Our benchmark on real meeting recordings** against CAM++ and TitaNet:
  ERes2Net separates same/other voices best — same-speaker cosine 0.29–0.8
  with cross-speaker ≤0.16 on the call channel, which yields workable
  thresholds (0.45 + a relative speaker-switch rule).
- Market context: even the best open pipeline, pyannote 3.1, reports
  DER ~19% on meetings (AMI) and is known for mid-recording label swaps —
  which is why Charoite complements live diarization with an offline
  re-pass over the full recording (echo filter, micro-fragment merging,
  name assignment).

## Mandatory num_ctx: 8192

Some Ollama Modelfiles ship with a 262144 context default — without an
explicit `num_ctx` the KV cache balloons by gigabytes and generation slows
down several-fold. Every Charoite call passes `num_ctx: 8192` explicitly.

## Swapping models

Everything lives in `config/config.yaml`: `stt.backend`, `llm.model`,
`llm.small_model`; the embedding model is just the file
`models/diar/embedding.onnx`. On 16 GB machines start with
`llm.model: gemma4:latest` and a lighter STT backend.
