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
- `merge_graphs.py` — 重新缝合分裂的图谱；不加 `--apply` 只显示计划。应用前会验证全部冲突，只自动拼接 Markdown，创建恢复备份，并在部分失败时自动回滚。
- `memory_bench.py` — 在演示图谱上对整个检索闭环做基准测试（`--demo`、`--demo-en`）。
- `tier3_cores.py` — 核心修订（重复项合并），加 `--apply` 生效；不加则为空跑演练。
- `nightly_dossier.py` — 增量重建主题档案，或用 `--find` 查看检索结果。
- `morning_brief.py` — 由图谱和夜间复盘组装晨报。
- `nightly.sh`、`nightly_claude_cores.py` — 夜间循环：tier3 + 可选的云端核心复盘（云层未开启时不运行）。
- `nightly_dossier_review.py` — 对本地模型写好的档案做云端复核：Opus 能看出转述看不出的东西（某个决定被后来的推翻、期限已过、两个节点互相矛盾）。`--dry` 只显示不写入。
- `cloud_review.py` — 带超时和明确边界地运行会议的云端复盘，而不是把 `claude` 丢到后台就算完事。崩溃或截断的回答不再留下一个看起来像真的复盘文件。
- `get_models.py` — 一条命令装模型：`--diar`（说话人分离嵌入，没有它就无法按声音实时标注）、`--segmentation`、`--stt sensevoice`（中文识别，228 MB）。另有 `--list`、`--check`、`--url`。
- `diar_bench.py` — 说话人分离的 DER：被错误标注的语音时间占比。`--make` 在本地生成合成测试样本——本仓库不可能存放会议录音。
- `stt_bench.py` — 识别的 CER：识别错误的字符占比。`--compare` 用同一批合成语句把 SenseVoice 与 Whisper 跑一遍对比。与说话人分离同样的提醒：合成语音比真实语音干净，这是下限而非基准。
- `fix_action_items.py` — 一次性规范化守护进程开始规范化之前写下的纪要中的行动项格式；只改格式。默认为空跑。
- `check_private_markers.py` — 去标识化守卫（pre-commit 钩子）：同时检查新增的行与整个被跟踪的文件树，只打印位置、绝不打印标记本身。见[参与贡献](../CONTRIBUTING.md)。
- `build_embedded_python.sh` — 组装随 `Charoite.app` 一同发布的可移植 python 运行环境；自行构建应用包时先运行它，再运行 `app/make_app.sh`。

有依赖的脚本应通过 `.venv/bin/python` 运行；`doctor.py` 是特意设计的例外，
安装前也能用系统 `python3` 运行。任务流程与恢复顺序见
[用户实用指南](../USER_GUIDE.md)和[数据地图](../DATA_AND_RECOVERY.md)。
