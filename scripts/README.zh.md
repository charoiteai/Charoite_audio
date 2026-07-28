# 脚本

*[English](README.md) · [Русский](README.ru.md) · [**中文**]*

运维辅助脚本。全部本地，全部可选。

- `doctor.py` — 一条命令：缺什么（venv、模型、配置、Ollama）以及怎么修。
- `import_meeting.py` — 把已录制的会议（音频 / 文本 / Zoom 字幕）导入档案和图谱；`note_`/`diary_` 音频进入笔记流水线。
- `memory_bench.py` — 在演示图谱上对整个检索闭环做基准测试（`--demo`、`--demo-en`）。
- `tier3_cores.py` — 核心修订（重复项合并），加 `--apply` 生效；不加则为空跑演练。
- `morning_brief.py` — 由图谱和夜间复盘组装晨报。
- `nightly.sh`、`nightly_claude_cores.py` — 夜间循环：tier3 + 可选的云端核心复盘（云层未开启时不运行）。
