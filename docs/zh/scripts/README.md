# 脚本

*[English](../../../scripts/README.md) · [Русский](../../ru/scripts/README.md) · [**中文**]*

运维辅助脚本。全部本地，全部可选。

- `doctor.py` — 一条命令：缺什么（venv、模型、配置、Ollama）以及怎么修。
- `import_meeting.py` — 把已录制的会议（音频 / 文本 / Zoom 字幕）导入档案和图谱；`note_`/`diary_` 音频进入笔记流水线。
- `protocol.py` — 从摘要与纪要生成适合发给参会者的协议；去掉 wiki 语法，绝不包含原始逐字稿。
- `rename_meeting.py` — 协同重命名逐字稿、归档、图谱笔记和应用状态；默认只显示计划，`--yes` 才应用。
- `forget_meeting.py` — 从逐字稿、录音、归档和图谱引用中删除一场会议；默认只显示计划，`--yes` 才应用。
- `dedup_archive.py` — 整理历史重复归档；默认只显示计划，`--apply` 把多余文件夹移到 `Встречи-архив/_дубли/`，而不是删除。
- `dedup_graph.py` — 经明确允许后，把逐字节相同的归档副本换成硬链接；应用前先阅读报告。
- `memory_bench.py` — 在演示图谱上对整个检索闭环做基准测试（`--demo`、`--demo-en`）。
- `tier3_cores.py` — 核心修订（重复项合并），加 `--apply` 生效；不加则为空跑演练。
- `nightly_dossier.py` — 增量重建主题档案，或用 `--find` 查看检索结果。
- `morning_brief.py` — 由图谱和夜间复盘组装晨报。
- `nightly.sh`、`nightly_claude_cores.py` — 夜间循环：tier3 + 可选的云端核心复盘（云层未开启时不运行）。

有依赖的脚本应通过 `.venv/bin/python` 运行；`doctor.py` 是特意设计的例外，
安装前也能用系统 `python3` 运行。任务流程与恢复顺序见
[用户实用指南](../USER_GUIDE.md)和[数据地图](../DATA_AND_RECOVERY.md)。
