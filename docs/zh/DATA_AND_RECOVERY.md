# 数据、保留与恢复

*[English](../DATA_AND_RECOVERY.md) · [Русский](../ru/DATA_AND_RECOVERY.md) · **中文***

Charoite 的 local-first 不只表示模型在本机运行。它的工作状态由可见的普通文件
组成，无需专有数据库就能复制和恢复。不过一场会议有多个生命周期不同的层。
这张地图帮助你区分临时来源与长期归档，避免删掉唯一可用于重试的材料。

## 一场会议的数据地图

| 位置 | 内容 | 生命周期 | 用途 |
|---|---|---|---|
| `recordings/` | 麦克风与系统声音的独立 PCM/WAV 声道 | `record_keep_days`，默认 2 天 | 精确重建的保险来源 |
| `transcripts/` | 实时与最终逐字稿、纪要、提示、复盘 | 直到明确删除 | 流水线输入与手动重试来源 |
| `<graph_dir>/Встречи/` | 带链接和事实的会议事件笔记 | 长期保留 | 图谱中的规范会议记忆 |
| `<graph_dir>/Встречи-архив/` | 可读会议文件夹：摘要、纪要、逐字稿等 | 长期保留 | Finder、Obsidian 与同步结果 |
| `<graph_dir>/Документация/` | 图谱节点引用的文档副本 | 长期保留 | 图谱来源 |
| `logs/meeting-status/` | 处理状态、逐字稿路径和错误 | 14 天 | 应用状态与“近期会议” |
| `~/Library/Application Support/Charoite/semantic_index_v2.bin` | 派生搜索索引 | 可重建 | 加速搜索，不是备份 |

`record_keep_days` 删除的是音频，不是会议文档。某条记录从“近期会议”中消失也
不会删除会议，只是状态记录过期。

## 哪些是事实来源

- 长期记忆以 `graph_dir` 中的 Markdown 为准。向量索引和界面历史都是派生物，
  可以重建。
- 重试处理需要 `transcripts/` 中的文件。只要它还在，即使没有实时会话也能再次
  运行流水线。
- 最准确的逐字稿重建还需要 `recordings/` 中未过期的录音。保留期删除后，文字
  仍在，但无法再从音频识别争议片段。
- `Встречи-архив` 是阅读层。启用 `sufler.dedup_files: true` 后，其中部分文件
  可能是指向 `Документация/` 原件的硬链接；从任一路径编辑都会修改同一内容。

安全原则很简单：可以编辑最终笔记，但移动或重命名会议各层时，应使用应用和
提供的命令，而不是分别手工操作。

## 自动删除什么

自动清理只覆盖明确标为临时的数据：

- `recordings/` 中早于 `audio.record_keep_days` 的 PCM/WAV；
- 同一保留期之外的 `logs/graph_*.log` 诊断日志；
- 早于 14 天的处理状态。

音频清理在守护进程启动时发生。因此 Charoite 长时间未运行时，文件可能超过名义
期限仍存在；下一次启动时则会立即消失。逐字稿、摘要、纪要、任务和图谱节点不受
这个保留参数影响。

## 导入来源是例外

`scripts/import_meeting.py` 会把导入的原始音频保存在图谱的会议材料旁。这份副本
已经不在 `recordings/` 中，因此不受 `record_keep_days` 约束。图谱使用 iCloud
同步时，原始音频也可能被同步。

若你的政策是“音频只保留两天”，需要在确认结果后单独删除导入来源，或彻底忘记
整场会议。

## 最小备份集合

为了从磁盘损坏中恢复，请保存：

1. 整个 `graph_dir`——长期记忆与最终文档；
2. `transcripts/`——重新处理的能力；
3. `config/config.yaml`——路径、模型与规则；
4. 需要重新识别时，再保存尚未过期的 `recordings/`。

仓库和模型可以重新下载。`logs/`、搜索索引和应用构建通常无需备份。配置中可能
包含工作路径和隐私选择，应像保护图谱一样保护它。

大规模手工修改图谱之前，请做普通文件副本，或提交到你的私有 Git 仓库。
Charoite 不代替 Time Machine，也不会为任意手工编辑自动做版本管理。

## 按症状恢复

| 症状 | 通常仍然完好的内容 | 下一步 |
|---|---|---|
| 停止后报错 | 通常逐字稿和录音仍在 | 打开逐字稿，然后重试 |
| “处理中”不再变化 | 状态可能来自已退出进程 | 运行 `doctor`，再重试 |
| 没有状态但逐字稿存在 | 只丢了界面状态 | 手动运行 `rebuild_transcript.py` |
| 崩溃后留下 PCM | 原始音频与实时逐字稿 | 下次启动守护进程会接手；不要删除 PCM |
| 只剩 WAV/M4A/VTT/SRT | 会议来源仍在 | 用 `import_meeting.py` 导入 |
| 同一会议有两个归档文件夹 | 两边的文档通常都在 | 先试运行 `dedup_archive.py`，再 `--apply` |
| 搜索找不到已知事实 | Markdown 可能完好，索引只是派生物 | 检查原文件并重新搜索 |

基础诊断：

```bash
python3 scripts/doctor.py
```

从已有逐字稿手动重建：

```bash
.venv/bin/python src/rebuild_transcript.py transcripts/<文件>.md
```

导入保留下来的来源：

```bash
.venv/bin/python scripts/import_meeting.py <音频|文本|字幕>
```

## 安全的维护命令

会修改数据的命令都可以先显示计划。

协同重命名所有层：

```bash
.venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "新标题"
.venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "新标题" --yes
```

整理旧的重复归档文件夹：

```bash
.venv/bin/python scripts/dedup_archive.py
.venv/bin/python scripts/dedup_archive.py --apply
```

应用后，多余文件夹会移动到 `Встречи-архив/_дубли/` 等待检查，而不会删除。

彻底忘记一场会议：

```bash
.venv/bin/python scripts/forget_meeting.py 2026-07-15
.venv/bin/python scripts/forget_meeting.py 2026-07-15_1400 --yes
```

第一次运行只列出受影响文件。`--yes` 会从逐字稿、录音、归档和图谱中删除会议；
仍需保留但要去掉引用的节点，会先复制到 `.forget_backup/`。这个文件夹不是被删
会议的回收站——确认后，会议自己的文件应视为已删除。

## 恢复时不要这样做

- 不要同时为同一会议运行两个 `rebuild_transcript.py`。
- 不要因为 PCM/WAV 看起来像技术文件就删除它。
- 不要把仍在增长的 WAV 重复复制到导入文件夹；等待第一次复制完成。
- 不要通过修改 `logs/meeting-status/` JSON 把会议“变成已就绪”；状态只报告结果，
  不会生成结果。
- 不要指望删除搜索索引能恢复缺失的 Markdown；索引是派生数据，不含完整图谱副本。

日常只需记住三层：`recordings/` 让你能重新识别，`transcripts/` 让你能重跑
流水线，`graph_dir` 保存长期记忆。
