# Config presets

*[**English**] · [Русский](../docs/ru/config/README.md) · [中文](../docs/zh/config/README.md)*

Copy one preset to `config/config.yaml`, then set `sufler.user_name` and `sufler.graph_dir`.

- `config.example.yaml` — Russian default: GigaAM STT (Russian SOTA), Russian documents. Starts with the light model set for 8–16 GB (`qwen3.5:4b` in every role); the profiles for 32 and 64 GB are listed in the file.
- `config.example.en.yaml` — English: Parakeet STT, English documents and copilot role.
- `config.example.zh.yaml` — Chinese: Whisper STT out of the box (SenseVoice recognizes Chinese better: `.venv/bin/python scripts/get_models.py --stt sensevoice`, then `stt.backend: sensevoice`), Chinese documents; Qwen (the default LLM) is native in Chinese.

The English and Chinese presets still start with `qwen3.6:35b-a3b` (about 23 GB) as the main model, which suits 32 GB and up; on a smaller Mac apply a lighter profile in the app or set `llm.model` by hand — see [Models](../docs/MODELS.md).

The app's first-run wizard does the copying for you: it creates `config/config.yaml` in the data folder from `config.example.yaml` — the only preset inside the bundle — and writes your name, the graph folder and the model set it picked. For English or Chinese meetings start from the matching preset instead: the STT backend, the language and the prompts differ. The prebuilt app's own runtime runs GigaAM and SenseVoice only — the English preset's Parakeet and the Chinese preset's default Whisper need a source install or an embedded runtime built with `--extras` (see [Setup](../docs/SETUP.md)).

Every key is documented inline. `config.yaml` itself is git-ignored — your settings never leave the machine.

Memory benchmark cases (`scripts/memory_bench.py`) live here too:

- `memory_bench.example.yaml` — the format: a question and the facts (`must`) the answer has to contain. Copy it to `config/memory_bench.yaml` and fill it with questions about your own graph; the nightly cycle runs it.
- `memory_bench_demo.yaml`, `memory_bench_demo_en.yaml`, `memory_bench_demo_zh.yaml` — the cases for the demo graphs (`--demo`, `--demo-en`, `--demo-zh`); see [demo](../demo/README.md).
