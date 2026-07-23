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

## Light model: qwen3.5:4b

Live theses, classification, draft minutes — everything that must run every
few seconds in parallel with the main model.

- **Our benchmark vs gemma4:e4b** (July 2026, real assistant tasks):
  more accurate question classification (e4b failed a direct question),
  theses in 2.9 s vs 3.3 s without filler preambles, and 3.4 GB RAM vs
  9.6 GB — almost 3x lighter next to the main model.
- The exception is **dialogue markup** (`markup_model: gemma4:latest`):
  words must stay verbatim there, and qwen3.5:4b tends to slightly polish
  them; gemma keeps the text exact.
- Very low RAM — `qwen3.5:2b` (edge-class model of the same family).

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

## English meetings

The default STT targets Russian. For English audiences:

- **Parakeet TDT 0.6B v3** (`stt.backend: parakeet`) — 6.32% WER on the Open
  ASR Leaderboard vs 7.44% for Whisper, up to thousands of times real-time;
  already supported in the config.
- **Moonshine** — streaming by design (words appear as you speak, ~107 ms
  latency, models from 27 MB) — a candidate for early question detection
  instead of a server-side streaming STT.
- `whisper-large-v3-turbo` — the multilingual fallback (100+ languages).

## Phones (roadmap)

Memory budget: a 6 GB phone realistically gives a model ~3–3.5 GB. The
working mobile stack: **Moonshine Tiny/Base** (27–245 MB, CPU) or ANE-based
ASR + **qwen3.5:0.8b/2b** (~25–40 tok/s on phones) for theses and summaries.
On iOS additionally: the built-in ~3B Foundation Models (iOS 26+, zero
download) and Core AI for native Swift inference; diarization via ANE
pipelines. Model choice stays in the config.

## Presets by RAM — macOS

Live suggestions and the graph LLM are the memory-hungry parts; STT (~1 GB)
and diarization (~0.5 GB) are constant. Numbers are the working set on Apple
Silicon, `num_ctx: 8192` throughout (mandatory — larger contexts reload the
model and blow up RAM).

| RAM | Main LLM | Light LLM | STT | What you get |
|----|----|----|----|----|
| **4 GB** | — | — | GigaAM | Not enough for a local LLM. Run STT only (live transcript + saved minutes), and point `llm.base_url` at another machine or a cloud endpoint for suggestions. |
| **8 GB** | `qwen3.5:4b` (3.4 GB) | same model | GigaAM | Transcript, theses, draft minutes, basic suggestions. One model serves both roles; no parallel Claude layer. Skip the graph (30B floor). |
| **16 GB** | `gemma4:latest` (9.6 GB) | `qwen3.5:2b` | GigaAM | Full live loop: suggestions + theses + minutes in parallel. Graph extraction works but is slower. Recommended entry point. |
| **32 GB** | `qwen3.6:35b-a3b` (23 GB) | `qwen3.5:4b` (3.4 GB) | GigaAM | The default config. Big-model suggestions, light model for theses in parallel, reliable graph extraction. Benchmarked here. |
| **64 GB+** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | GigaAM | Same models, but headroom for the optional cloud Claude layer, longer meetings, and offline transcript rebuild without eviction. |

Rules of thumb: below 16 GB, drop the knowledge graph — sub-30B models break
the JSON schema. Below 8 GB, keep only STT locally. The `small_model` always
runs next to the main one, so budget for both at once.

## Presets by RAM — iOS / iPadOS

Phones and tablets can't hold a 30B model, so the split is different: the
device does STT and light generation, anything heavier goes to a Mac over the
REST API (`llm.base_url`). iOS RAM is also capped per app (roughly half the
physical RAM), so the usable budget is smaller than the sticker number.

| Device RAM | Local | Over REST API | Notes |
|----|----|----|----|
| **4 GB** (older iPhone/iPad) | STT only (Moonshine Tiny, ANE) | suggestions, theses, graph | Thin client. Live transcript on device, everything smart from the Mac. |
| **6 GB** (iPhone 15/16 base) | STT + `qwen3.5:0.8b` for theses | suggestions, minutes, graph | On-device theses and quick answers; the Mac handles depth. |
| **8 GB** (iPhone Pro, iPad) | STT + `qwen3.5:2b` | graph, cloud layer | Most of the live loop runs locally; only the graph needs the Mac. |
| **iOS 26+** (any) | + built-in ~3B Foundation Models | graph | Apple's on-device model ships free (zero download) via Core AI — use it for theses/classification, keep the graph on the Mac. |

The mobile STT choice is **Moonshine** (streaming by design, 27–245 MB, ~107 ms
latency) rather than GigaAM, which is tuned for the Mac. Diarization on iOS
runs through ANE pipelines. All of this stays configurable — the phone is a
client that can borrow the Mac's models whenever they're reachable.

## Swapping models

Everything lives in `config/config.yaml`: `stt.backend`, `llm.model`,
`llm.small_model`; the embedding model is just the file
`models/diar/embedding.onnx`. On 16 GB machines start with
`llm.model: gemma4:latest` and a lighter STT backend.
