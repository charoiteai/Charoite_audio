# Config presets

*[**English**] · [Русский](../docs/ru/config/README.md) · [中文](../docs/zh/config/README.md)*

Copy one preset to `config/config.yaml`, then set `user_name` and `graph_dir`.

- `config.example.yaml` — Russian default: GigaAM STT (Russian SOTA), Russian documents.
- `config.example.en.yaml` — English: Parakeet STT, English documents and copilot role.
- `config.example.zh.yaml` — Chinese: Whisper STT, Chinese documents; Qwen (the default LLM) is native in Chinese.

Every key is documented inline. `config.yaml` itself is git-ignored — your settings never leave the machine.
