# Why these models

***English** · [Русский](ru/MODELS.md) · [中文](zh/MODELS.md)*

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

## Main LLM: qwen3.6:35b-mlx (same MoE, MLX engine)

MoE: ~35B total, ~3B active parameters — 30B-class quality at small-model
speed.

- **Our benchmark on a real meeting transcript**: first token in 0.27 s,
  full answer in 2.2 s — vs 1.08 s / 4.5 s for dense gemma4:26b, while
  holding the assistant role more reliably (gemma confused who said what).
- **Generation speed** (M1 Max, 32 GB, `num_ctx: 8192`, two identical runs
  on 2026-08-08): ~27 tok/s for `qwen3.6:35b-a3b` and ~32 tok/s for the
  light `qwen3.5:4b`. Speech is 4–5 tokens per second, so both stay ahead
  of the conversation — the throughput that matters is per hint, not per
  hour.
- **The 30B class is the floor for structured/graph extraction** — not our
  preference but an industry observation:
  [LightRAG](https://github.com/hkuds/lightrag) names Qwen3-30B-A3B a
  reasonable minimum for entity extraction;
  [Graphiti](https://github.com/getzep/graphiti) warns that very small
  models break the JSON schema; on the schema-guided KG benchmark
  [OSKGC](https://ceur-ws.org/Vol-4041/paper1.pdf) 7–8B models lose
  ~0.1 Micro F1 vs frontier and struggle most with ontology compliance.
  That is how the main model for the full profile was chosen, and on 64 GB
  it is still 35B. As a hard floor, though, the rule did not hold: our own
  benchmark on 19.08 across three real meetings showed `qwen3.5:4b`
  extracting the graph better than `gemma4:12b` (31 decisions against 28,
  96% against 100% verifiable quotes), and what breaks the JSON is a
  particular model (`GigaChat3.1-10B` — five parts out of six), not the size
  class. See "The 30B floor is retired" below.
- `think: false` everywhere: reasoning mode moves output into the thinking
  field (empty content) and adds ~10 s of latency.

## Engine: MLX build instead of GGUF (measured 2026-08-12)

Ollama ships the same MoE under two tags: `qwen3.6:35b-a3b` (GGUF,
llama.cpp, 23 GB, Q4_K_M) and `qwen3.6:35b-mlx` (Apple's MLX engine, 21 GB).
Architecture and capabilities match: completion, vision, tools, thinking.
No migration needed — it is one tag in `llm.model`.

Measured on M1 Max, 64 GB, three runs, median over warm ones
(`scripts/bench_models.py`):

| Case | `35b-a3b` (GGUF) | `35b-mlx` (MLX) | Muse Glimmer 30B (MLX) |
|---|---|---|---|
| short prompt | 0.3 s / 40.6 tok/s | **0.1 s / 49.5** | 5.7 s / 15.8 |
| text extraction | 0.3 s / 13.6 | **0.1 s / 46.7** | 13.3 s / 16.4 |
| long context (20k chars) | 0.4 s / 31.2 | **0.1 s / 50.8** | 10.1 s / 15.3 |
| cold start (weight load) | 9.2 s | 21 s | 72 s |

MLX won. Russian was checked separately on a real transcript: minutes of
comparable quality, clean language.

**Quality was measured on Aug 13, and the first run looked like a verdict
against MLX.** Across six meetings, the share of core quotes actually found in
the transcript: MLX 22 of 43 (51%), GGUF 35 of 43 (81%) — with an identical
volume of findings. Reading the raw output showed why: MLX stitched a quote out
of two distant fragments joined by an ellipsis (11 of 15 in the first run;
GGUF, none). Such a "quote" is never found by the anchor search, so the core
ends up without a verifiable basis while looking perfectly ordinary.

The cause was the wording, not the build. After tightening the prompt — "a
VERBATIM CONTIGUOUS fragment, 5-15 words in a row… you may not stitch pieces
from different places with an ellipsis" — across three meetings: **MLX 16 of 17
(94%)**, GGUF 16 of 20 (80%), same volume (21 decisions vs 19), median 52
seconds vs 107. So MLX is both better grounded and twice as fast.

**We stay on MLX.** Caveat on the numbers: the second run is three meetings and
seventeen cores; the effect is large and has a clear cause, but it is not ten
meetings.

Speed is half the answer, and `bench_models.py` measures only that half. The
other half is `scripts/bench_extract.py`: it runs the very extraction function
that feeds the graph over the same transcripts with different models. Beyond
volume (decisions, cores, people) it checks what a model gets wrong quietly —
**the share of core quotes that are actually found in the transcript** (by the
same search that anchors them in the graph) and the share of timestamps that
really occur in the text. An invented quote is an invented basis for a node:
the node itself looks fine, and there is no other way to catch the swap.
Substantive completeness ("did it catch the action items") is read by a human —
raw extractions land in `logs/bench_extract/`.

**Honest caveats.** Three ~21 GB models shared 64 GB during the run and
evicted each other — the "text extraction" row (13.6 vs 46.7) almost
certainly caught a GGUF reload; a 3.4× gap does not come from an engine
swap. A realistic estimate of the win is 20–30%. Quantisation differs too
(23 GB vs 21 GB), and quality was compared on a single task.

**What it costs.** Cold start doubles: 21 s against 9. After a long idle the
first prompt on a meeting arrives later; afterwards the model stays resident
via `keep_alive`.

**Rollback** is one line: put `qwen3.6:35b-a3b` back into `llm.model` and
`think_model`. The previous value is kept as a comment next to it.

**Muse Glimmer 30B** (the first open-weight model from Meta Superintelligence
Labs, Apache 2.0) is not usable as the chat model: 15–16 tok/s and 5 to 13
seconds to the first token. The reason is architectural — a dense 30B hits
memory bandwidth where an MoE computes with three billion active parameters.
We keep it in mind for agentic work (strict function calling, 131K context),
where format discipline matters more than latency.

Requires Ollama 0.32+: on 0.20 the MLX tags return 412 "requires a newer
version".

**`mlx_lm.server` as a separate engine (measured 2026-08-15).** The gateway
also speaks the OpenAI-compatible `mlx_lm.server` (`llm.engine:
mlx-server` in the config) — measured against the Ollama engine on the
same three meetings: anchors slightly better (quotes 34/35 vs 36/39,
timestamps 35/35), but one extraction chunk of the long meeting failed to
parse (no strict JSON mode on that server; Ollama with `format: "json"`
lost none), extraction is 15–35% slower, and the live thread gains nothing
from the prefix cache — its prompt is small by construction (the telegraph
thread plus a short tail), so there is nothing to cache. **The production
default stays `ollama`**; the engine remains a config option for
long-document Q&A sessions, to re-measure when `mlx_lm.server` grows a
strict JSON mode or Ollama grows a prefix cache.

## Tested, not adopted: Qwen3.8-27B (measured 2026-08-14)

The first open dense model of the Qwen3.8 family — hybrid attention (linear
on 48 of 64 layers), native VL, an MTP draft head, 262K context, Apache 2.0;
the `qwen3.8:27b-mlx` tag needs Ollama ≥ 0.32.12. Measured the day the
weights landed, same methodology as everything above: `bench_extract.py`
runs the production graph-extraction function over three real meetings
(147k / 35k / 25k chars), both models as MLX 4-bit under one runtime.

| | qwen3.6:35b-mlx | qwen3.8:27b-mlx |
|---|---|---|
| decisions / cores | 42 / 39 | 47 / 31 |
| core quotes found in the transcript | 36/39 (92%) | 30/31 (96%) |
| timestamps that exist in the text | 35/39 (89%) | 29/31 (93%) |
| median per meeting | **57 s** | 224 s |
| the 147k-char meeting | **254 s** | 931 s |

Better anchors — and still not the default:

- **3.7–3.9× slower end to end.** A dense 27B pays memory bandwidth where
  the MoE computes with three billion active parameters (the Muse Glimmer
  lesson again). Extraction chunks ran at the edge of the 300-second
  per-request timeout, and one chunk's JSON did not parse at all — half a
  meeting silently missing (the 31-vs-39 core gap is partly that, so the
  precision win is paid for with completeness).
- **Prefix caching does not work on it.** Three requests sharing a
  ~6.2k-token prefix through `mlx_lm.server`: 77.2 / 74.7 / 75.9 s
  end-to-end with identical short generations — a 1.0× "speedup". The 88×
  baseline is the same protocol on our full-attention MoE (measured
  2026-08-14: three consecutive questions over one 15.6k-token transcript
  via `mlx_lm.server`, prefill 29–34 s cold against 0.3–0.4 s cached,
  generation speed unchanged). Linear-attention layers carry recurrent
  state instead of a KV cache, so the server cannot resume from a prefix.
- Prefill is slow today too: ~95 tok/s against ~520 on the MoE — likely in
  part an immature hybrid-attention implementation in current runtimes.

Where it may still land: at 16.1 GB the 4-bit build leaves noticeably more
headroom on a 32 GB machine than the 21 GB default, so it stays a candidate
for the tighter presets — to be compared against full-attention 8–14B
models (which keep the caching win) before any preset changes. Re-measure
when runtimes learn its MTP draft head (speculative decoding may change the
speed verdict) or when `mlx_lm` learns to cache hybrid-attention state.

## Light model: qwen3.5:4b

Live theses, classification, draft minutes — everything that must run every
few seconds in parallel with the main model.

- **Our benchmark vs gemma4:e4b** (July 2026, real assistant tasks):
  more accurate question classification (e4b failed a direct question),
  theses in 2.9 s vs 3.3 s without filler preambles, and 3.4 GB RAM vs
  9.6 GB — almost 3x lighter next to the main model.
- The exception is **dialogue markup** (`markup_model`):
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

## Chinese meetings

- **SenseVoice Small** (`stt.backend: sensevoice`) — the specialized path:
  Chinese plus four more East Asian languages in one 228 MB int8 model,
  run through the same sherpa-onnx that is already installed for diarization
  — no new dependency. Text normalization is on (`use_itn`), so numbers and
  times arrive as digits («3点15分» → «3:15») rather than spelled out; for
  minutes and action items that is the difference between usable and
  rewrite-by-hand. Install: `scripts/get_models.py --stt sensevoice`.
- `whisper-large-v3-turbo` (`stt.backend: whisper`, `language: zh`) remains
  the fallback — multilingual, heavier, and a generalist on Chinese.

**What we measured (10.08, `scripts/stt_bench.py --compare`).** On English
synthesized phrases Whisper is more accurate: CER 0.064 against SenseVoice's
0.149 — so do not switch to SenseVoice for English «just in case». The
Chinese half of the comparison is still missing: the macOS Chinese voice is
listed but not downloaded on our machine, and `say` returns silence instead
of speech. Until that number exists, «SenseVoice is better on Chinese» is a
reasonable expectation from its design, not a result we can show.

The main LLM, Qwen, is native in Chinese either way.

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
| **4 GB** | — | — | GigaAM | Not enough for a local LLM. Run STT only (live transcript + saved minutes). Suggestions can go to Ollama on another machine you own — but that sends transcripts off this device, so it requires an explicit `llm.allow_remote: true` in the config and is refused under `CHAROITE_NO_CLOUD` (see PRIVACY.md). |
| **8 GB** | `qwen3.5:4b` (3.2 GB) | same model | GigaAM | Transcript, theses, draft minutes, basic suggestions. One model serves both roles; no parallel Claude layer. The graph works — see the 19.08 benchmark below — but déjà vu and the core revision are off: both pull in `bge-m3`, another 1.2 GB next to the system. |
| **16 GB** | `qwen3.5:4b` (3.2 GB) | same model | GigaAM | Full live loop: suggestions + theses + minutes in parallel, plus semantic memory (déjà vu and core revision: `bge-m3`, +1.2 GB). Same model as on 8 GB — in the benchmark it finds more than `gemma4:12b`, while 12b together with the embedder would hit 17–19 GB on a 16 GB machine. Recommended entry point. |
| **32 GB** | `qwen3.8:27b-mlx` (16.9 GB) | `qwen3.5:4b` | GigaAM | More accurate quotes (96%) at the price of a three-times-slower extraction — which is background work and now yields to a live meeting. 35B is not here on purpose: 20.4 GB of weights plus STT, the embedder and the system is 27–30 GB out of 32, i.e. swap on the first long extraction. |
| **64 GB+** | `qwen3.6:35b-mlx` (20.4 GB) | `qwen3.5:4b` | GigaAM | The working set: 42 decisions and 39 cores per meeting, 57 s median. Headroom for the optional cloud Claude layer, longer meetings, and offline transcript rebuild without eviction. |

Rules of thumb: below 8 GB, keep only STT locally. The `small_model` always
runs next to the main one, so budget for both at once.

**Dialogue markup and the light profiles.** The loop that splits a paragraph
into replies needs verbatim output: validation compares the words and drops
any answer where the model changed something. A 4B edits more often than
gemma, so on the 8 and 16 GB profiles markup fires less often — that is the
price of a light set, not a breakage. On 32 and 64 GB the profile puts the
main model here, the one that is resident anyway.

**The 30B floor is retired (benchmark 19.08).** The same
`scripts/bench_extract.py` on three real meetings (24–106k characters) gave:
`qwen3.5:4b` — 3/3 parsed, 31 decisions, 30 cores, 96% quotes, 113 s median;
`gemma4:12b` — 3/3, 28 decisions, 24 cores, 100% quotes, 353 s;
`gemma4:latest` (e4b) — 3/3, 16 decisions, 23 cores, 95%, 152 s;
`GigaChat3.1-10B` — 2/3, broken JSON in five parts out of six. So a 4B model
extracts the graph better than a 12B one, and what breaks the schema is a
particular model rather than the size class. `gemma4:latest` left the 16 GB
row for the same reason: it is heavier (8.9 GB against 7.0) and finds less.

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

## Cloud models (when the layer is on)

The cloud layer is off by default in full — which switch enables what is
described in [PRIVACY.md](../PRIVACY.md). This section is only about model
choice.

| Config key | Default | Where it runs |
|----|----|----|
| `cloud_model` | `claude-opus-5` | post-meeting debrief, nightly core and dossier reviews — not at conversation speed, so the strongest model is worth it |
| `cloud_live_model` | `claude-haiku-4-5` | answering a question mid-meeting: speed matters more |
| `cloud_hints_model` | `claude-haiku-4-5` | hint refinement: same, but more often |

Defaults live in one place — `src/cloud.py` — and match the example configs; a
mismatch fails a test. Previously the literal sat in every call site, and one
key (`cloud_model`) had two different defaults: with a trimmed config the
post-meeting debrief and the nightly review went to different models.

## Swapping models

Everything lives in `config/config.yaml`: `stt.backend`, `llm.model`,
`llm.small_model`; the embedding model is just the file
`models/diar/embedding.onnx`. On 16 GB machines start with
`llm.model: qwen3.5:4b` (see the profile table above) and a lighter STT backend.
