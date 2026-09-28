# 配置预设

*[English](../../../config/README.md) · [Русский](../../ru/config/README.md) · [**中文**]*

复制任一预设为 `config/config.yaml`，然后填入 `sufler.user_name` 和 `sufler.graph_dir`。

- `config.example.yaml` — 俄语默认：GigaAM STT（俄语 SOTA），俄语文档。以适合 8–16 GB 的轻量模型组合起步（所有角色均为 `qwen3.5:4b`）；32 GB 和 64 GB 的配置列在文件中。
- `config.example.en.yaml` — 英语：Parakeet STT，英语文档和提词角色。
- `config.example.zh.yaml` — 中文：开箱即用 Whisper STT（SenseVoice 的中文识别更好：`.venv/bin/python scripts/get_models.py --stt sensevoice`，然后设 `stt.backend: sensevoice`），中文文档；默认 LLM Qwen 的中文是母语级。

英语和中文预设仍以 `qwen3.6:35b-a3b`（约 23 GB）作为主模型，适合 32 GB 及以上的机器；内存更小的 Mac 请在应用中选用更轻的配置，或手动修改 `llm.model`——见[模型](../MODELS.md)。

应用的首次运行向导会替你完成复制：它从 `config.example.yaml`（应用包内唯一的预设）在数据文件夹中创建 `config/config.yaml`，并写入你的姓名、图谱文件夹和所选的模型组合。英语或中文会议请改从对应的预设开始：STT 后端、语言和提示词都不同。预编译应用自带的运行环境只能运行 GigaAM 和 SenseVoice——英语预设的 Parakeet 和中文预设默认的 Whisper 需要从源码安装，或用 `--extras` 构建内置运行环境（见[安装](../SETUP.md)）。

每个键都在文件内有注释说明。`config.yaml` 本身在 git-ignore 中——您的设置不会离开本机。

记忆基准（`scripts/memory_bench.py`）的用例也放在这里：

- `memory_bench.example.yaml` — 格式：一个问题及回答中必须包含的事实（`must`）。复制为 `config/memory_bench.yaml`，填入关于你自己图谱的问题；夜间流程会运行它。
- `memory_bench_demo.yaml`、`memory_bench_demo_en.yaml`、`memory_bench_demo_zh.yaml` — 演示图谱的用例（`--demo`、`--demo-en`、`--demo-zh`）；见[演示](../demo/README.md)。
