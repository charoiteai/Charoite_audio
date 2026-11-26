# 功能清单

*[English](../FEATURES.md) · [Русский](../ru/FEATURES.md) · **中文***

## 会议进行中

- **录音提醒**：可选日历集成，在日程会议开始时提醒开启录音。
- **开箱即用的系统音频（ScreenCaptureKit）**：直接捕获通话音频（macOS 15+ 同时捕获麦克风），无需虚拟声卡；命令行或权限受限时回退 BlackHole 并明确提示。
- **实时逐字稿**：按说话人合并为清晰段落，同一说话人连续发言不重复前缀，音频丢帧位置会显式标注。
- **实时说话人分离与姓名识别（`src/speaker_names.py`）**：开场即标注声音编号，并在自我介绍或点名后自动关联姓名（结合音高否决明显性别冲突；详见 [DIARIZATION.md](DIARIZATION.md)）。
- **会议线索（Meeting thread）**：随讨论实时生长的单一画布：`●` 主题、`-` 要点、`⚑` 决定/期限、`?` 开放问题、`⏮` 带日期的历史归档召回。开启 `sufler.live_feed: true` 可使用实验性本地时间线动态（见 [live-feed.md](../live-feed.md)）。
- **即时回答（`⚡`）与实时提示**：检测到对方提问时，在本地生成简明回答并融入会议线索，附带历史背景与未决事项。
- **实时归档上下文与会前简报**：随着议题展开自动检索知识图谱，并在开会前预填提示区。
- **自动要点与实时纪要草稿**：提炼 📌 关键节点与 💭 要点，约每 10 分钟刷新一次纪要草稿。
- **随时向助手提问**：会中直接输入问题，综合当前逐字稿与图谱记忆作答（`src/brain.py`）。
- **影子决策门（可选研究）**：`sufler.decision_gate_shadow: true` 仅记录评分元数据评估触发时机（`scripts/gate_bench.py`，见 [research/decision-gate.md](../research/decision-gate.md)）。
- **可选云端辅助（`☁️`，默认关闭）**：`cloud_live` 与 `cloud_hints`（`src/privacy.py`）提供第二意见或原地高亮修订线索条目。

## 会议结束后（自动）

- **两遍说话人分离重跑**：停止录音后对完整双声道重跑分离（默认 sherpa-onnx，可选 `sufler.diarize_backend: nemotron`，`src/diarize_nemotron.py`）。
- **分层文档输出**：按 `sufler.language: ru|en|zh`（支持自定义 `sufler.minutes_template`）输出纯 Markdown BLUF 文档：**一分钟摘要** → **纪要** → **复盘** → **完整逐字稿**，带便携会议卡片归档。
- **复盘核实**：启用 `sufler.refined_debrief` 后，复盘会根据实时讨论整理结论，并对照最终逐字稿核实人名、事实和约定。结论以最后的更正为准，有疑问的细节会在对应结论旁标注。原话保留在逐字稿标签页。
- **规范化待办**：自动草稿、`scripts/protocol.py` 和 MCP 生成的待办均确定性整理为 `- [ ]` 复选框并归属真实参会人；历史文件可用 `scripts/fix_action_items.py` 迁移。
- **自更新知识图谱（`src/charoite_graph/graph_nodes.py`）**：自动维护会议、人物、系统、核心与阻碍节点，不把占位标签或背景偶提名字建成人物节点。
- **保留手工编辑与异常恢复**：自动生成的纪要在重跑后平滑刷新（旧版备份至 `transcripts/.prev/`），手工修改过的文件仅回填解析出的姓名。
- **可选云端复盘与会后钩子**：显式开启 `cloud_enrich` 后执行受限云端复核（`scripts/cloud_review.py`）；支持自定义 `sufler.post_meeting_hook`。

## 会议之外

### 工作区、知识库与检索

- **统一 macOS 工作区（`app/`）**：单窗口包含「今天」（录音胶囊与会前准备）、「会议库」（卡片流、周历条、检索、安全摘要复制、重命名 `scripts/rename_meeting.py`、重试、彻底遗忘 `scripts/forget_meeting.py`）、「记忆」（流式归档问答与来源标签）、「任务」（汇总图谱复选框）与「设置」。
- **混合检索（`src/brain.py`、`src/charoite_graph/graph_search.py`）**：结合词法与 `bge-m3` 语义检索（`scripts/graph_search_index.py`），支持俄语词形变化、CJK、主题档案优先及单来源 40% 上限。
- **夜间周期（`scripts/nightly.sh`）**：可在设置中一键安装 `launchd` 任务，增量刷新主题档案（`src/charoite_graph/dossier.py`、`scripts/nightly_dossier.py`、可选 `scripts/nightly_dossier_review.py`）、核心复核（`src/tier3.py`、`src/nli.py`、`scripts/tier3_cores.py`）、图谱与归档去重（`scripts/dedup_graph.py`、`scripts/dedup_archive.py`）、图谱巡检（`scripts/graph_doctor.py`）及晨间简报（`scripts/morning_brief.py`）。
- **多聊天引擎（`src/llm.py`）**：支持 `ollama`（默认）、MLX 服务或可选云端网关（`sufler.cloud_engine: true`），并提供限定在 `CHAROITE_ROOT` 内的 MCP 服务器。

### 听写、笔记、导入与移动伴侣

- **全局听写（`⌥⌘D`）**：本地识别并粘贴回原窗口，自动恢复剪贴板；通过辅助功能识别密码框并拒绝粘贴；macOS 26+ 提供不抢焦点的实时草稿条。
- **语音笔记（`⌥⌘N`）与日记（`⌥⌘J`）**：本地模型整理后写入图谱或个人日记。
- **会议导入（`scripts/import_meeting.py`）**：支持导入音视频、文本与 Zoom/Teams 字幕，支持命令行、监控文件夹与 iPhone 语音备忘录桥接（`audio.voice_memos_bridge`）；支持自定义词典 `sufler.vocabulary` 与多语言 STT（GigaAM、SenseVoice、Parakeet、Whisper）。
- **iPhone（`app-ios/`）与 Android（`app-android/`）伴侣**：后台录音、队列投递（iCloud Drive 或任意文件夹同步工具；Android 无网络权限）、只读会议列表与任务复选框。
- **免终端安装、健康汇总与安全更新**：内置 CPython 运行时、按内存匹配模型预设（`scripts/get_models.py`、`scripts/install_engine.py`、`scripts/doctor.py`）、菜单栏健康汇总与带签名校验的应用内更新；统一界面设计规范见 [DESIGN.md](DESIGN.md)。

## 实用脚本（`scripts/`）

完整说明见 [scripts/README.md](scripts/README.md)：`install_engine.py`、`get_models.py`、`doctor.py`、`graph_doctor.py`、`dedup_graph.py`、`dedup_archive.py`、`merge_graphs.py`、`migrate_placeholders.py`、`fix_action_items.py`、`forget_meeting.py`、`rename_meeting.py`、`cloud_review.py`、`decision_evidence_audit.py`、`memory_bench.py`、`memory_case_suite.py`、`retrieval_compare.py`、`runtime_profile.py`、`bench_models.py`、`stt_bench.py`、`diar_bench.py`、`gate_bench.py`、`nemotron_shadow_replay.py`、`nemotron_shadow_check.py`。

## 文档格式

所有输出均为无需渲染即可阅读的纯 Markdown，遵循结论先行（BLUF）：一分钟摘要 → 纪要 → 复盘 → 逐字稿。
