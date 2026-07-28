# 架构

*[English](ARCHITECTURE.md) · [Русский](ARCHITECTURE.ru.md) · **中文***

## 概览

```
mic ───────┐                        ┌─ live transcript (per-voice paragraphs)
           ├─ AudioHub ─ STT ─ daemon ┼─ loops: ⚡ answers · theses · minutes
BlackHole ─┘   (3s chunks)          │   · déjà vu · names · dialogue markup
                                    └─ NDJSON stdout ←→ stdin commands (UI)

Stop → recording rebuild → graph update → archive + Summary → [Claude debrief]
```

一切都在你自己的机器上运行；网络调用仅指向 localhost（Ollama）。
云端层是一个独立的、默认关闭的选项。

## 守护进程（src/daemon.py）

单个进程，多个线程循环围绕一个共享加锁的 `Transcript` 运转：STT 循环、
即时回答、自动要点/提示、实时会议纪要、基于核心（Cores）的既视感
（déjà vu）、人名解析、对话标注、云端循环、心跳。事件以行式 JSON 流向
stdout（`{"type": "transcript"|"thesis"|"hint"|…}`）；命令从 stdin 传入
（`hint`、`ask <question>`、`summary`、`stop`）。任何 UI 都可以架在这套
协议之上；第二个实例会被 flock 阻止。

## 说话人分离：两遍处理

1. **实时**：每个音频块都被嵌入（ERes2Net，512 维）→ 带滞回的声音
   跟踪器（0.45 阈值、灰区、相对切换规则、新声音需两个一致的音频块
   确认）。嵌入向量仅驻留在内存中。
2. **停止后的离线处理**（src/rebuild_transcript.py）：对完整录音按声道
   重新做说话人分离；麦克风中的扬声器回声按重叠切除，短于 10 秒的声音
   并入相邻声音，片段重新转写，人名由 LLM 指派（所有者 = 自己麦克风上
   时长最长的声音）。实时版本保留为草稿。

## 会后流水线（src/graph_updater.py）

1. LLM 从逐字稿中抽取 JSON：标题（2-3 个词）、参与者、主题、决策、
   行动项、实体、核心（Cores）。
2. 图谱更新：带 `[[Folder/Name|Name]]` 链接的会议笔记，对 People/Systems
   节点做 upsert（事实带日期，历史永不抹除），核心（Cores）——“Status”
   整体重写，“Chronicle” 持续累积。每一行 Chronicle 都带出处：谁说的、
   几点说的、逐字引文。引文会对照逐字稿校验：先做词级精确匹配；若模型
   转述了原话，则用模糊搜索找到最接近的逐字稿窗口（difflib，0.75 阈值），
   写入图谱的是逐字稿本身的切片，绝不是模型的措辞；低于阈值的一律按
   捏造丢弃。
3. 归档（src/meeting_archive.py）：“日期 — 标题” 文件夹、人类可读的
   文件名、由提示日志汇编出的 Q&A、结合历史上下文生成的 Summary
   （核心 + 之前两份摘要；未来绝不泄漏到过去——按会议日期截断）。
4. 可选：云端 Claude 将会议纪要与逐字稿交叉核对，并为图谱补充只有
   从历史视角才能看到的链接。

## 知识图谱（一个 Obsidian 文件夹）

```
<graph_dir>/
  Meetings/…       ← episodes (raw material, never lost)
  People/ Systems/ ← entities with backlinks
  Cores/           ← cross-meeting topics: Status + Chronicle
  Notes/           ← voice notes
  Meeting-archive/ ← the reading layer (Finder-friendly)
  _MOC.md          ← the map of content
```

这是三层 “情节 → 实体 → 社区”（episodes → entities → communities）方案
（同 Graphiti/Zep），落在纯 markdown 之上：grep、Obsidian、git 和任何
编辑器开箱即用。被取代的事实只是标上日期，而不会被删除。

## 为什么选这些模型

基准测试与来源——[MODELS.md](MODELS.md)。要点：主模型保持在 30B 级别
（图谱抽取的下限），轻量模型与它一同常驻内存，`num_ctx` 始终显式指定。

## 记忆模型一页速览

- **文件是事实来源。** 不以图数据库或向量存储作为主载体：只有归用户
  所有的纯 Markdown。每条 Chronicle 事实都带出处（谁、何时、逐字稿
  原文引用）。
- **唯一嵌入模型——bge-m3**（Ollama）：负责语义搜索与核心修订预过滤。
  刻意不设第二个嵌入模型。
- **精度——本地 NLI**（src/nli.py，ONNX）：要点去重与核心修订判定。
  仅用于可容忍延迟的循环；实时提示与既视感（déjà vu）跑在廉价的词干
  匹配上。
- **云端——仅限自愿开启（opt-in）的会后增强**，通过订阅实现（环境中
  没有 API key）。默认情况下会议数据绝不离开本机；没有云端记忆 SaaS，
  也没有此类计划。
