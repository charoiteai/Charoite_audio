# 架构说明

*[English](../ARCHITECTURE.md) · [Русский](../ru/ARCHITECTURE.md) · **中文***

## 总览

```
麦克风 ───────┐                         ┌─ 实时逐字稿（按说话人分段）
              ├─ AudioHub ─ STT ─ 守护进程 ┼─ 循环：⚡ 即时回答 · 要点 · 纪要
系统音频 ─────┘  （3 秒分块）            │   · 姓名 · 对话标注 · 实时上下文
（ScreenCaptureKit；BlackHole 后备）     └─ NDJSON stdout ←→ stdin 命令（UI）

停止 → 录音重跑 → 图谱更新 → 归档与摘要 → [可选云端复核]
```

默认一切在本机运行，LLM 与向量嵌入请求仅访问回环地址（Ollama / MLX）。云端层默认关闭（详见 [PRIVACY.md](PRIVACY.md)）。应用本身在会议之外唯一的网络请求是可选的 GitHub 版本检查。

## 守护进程（`src/daemon.py`）

单进程内围绕加锁共享的 `Transcript`（`src/transcript.py`，运行时模块不导入 `src/main.py`）运行多个线程循环：STT、快速提问触发、即时回答、自动提示、要点提炼、会议线索、实时纪要、姓名解析、对话标注、归档实时上下文、自动停止、可选云端循环和心跳。

- **协议**：stdout 输出 NDJSON 事件（`transcript`、`thesis`、`hint`、`status` 等），stdin 接收命令（`hint`、`ask`、`expand`、`cloud`、`summary`、`set`、`stop`）。通过 `flock`（`logs/daemon.lock`）防止多实例冲突。
- **错误与内容分离**：异常状态带 `"error": true`，模型失败绝不会覆盖屏幕上最后一条有效提示。
- **实验性时间线动态（可选）**：启用 `sufler.live_feed: true` 后，由 `grounded_feed` 在 `LocalLoopbackAuthority` 约束下纯本地运行，停止时导出 `<transcript>.grounded.json`。详见 [live-feed.md](../live-feed.md)。

### 音频采集与统一时间轴

macOS 应用通过 ScreenCaptureKit 捕获系统音频（macOS 15+ 同时捕获麦克风）并写入 `data/sck_stream.json` 描述的流文件，由 `AudioHub` 尾随读取；无有效清单时回退到 BlackHole + PortAudio 麦克风。`ChannelAxis.swift` 使用 `synchronizationClock` 转换主机时间对齐双声道，短缺口（0.1–2 秒）补静音，长断层重新锚定而不裁切音频。

### 声道标签与机主（`src/channel_labels.py`）

`ChannelLabels` 从配置一次性生成原始声道标签、机主署名和词匹配名，避免不同模块对机主身份的判断分叉。

### 实时会议优先与调度（`src/live_gate.py`、`src/machine_power.py`）

实时会议始终优先于后台任务（重跑、图谱抽取、夜间档案与核心复核）：
- 录音期间持有 `logs/daemon.lock`（命令行 `src/main.py` 同时持 `logs/console.lock`），后台任务以非阻塞方式检测并让位。
- `logs/rebuild.lock` 保证单机同时只执行一次逐字稿重跑。
- `src/llm.py` 对 `503`/`429`/`502` 做退避重试，把流内 `{"error": ...}` 视为错误，并维护生成租约以区分正常推理与卡死。
- 守护进程内即时提示优先于后台会议线索；`src/machine_power.py` 感知低电量模式。

### 影子决策门（`src/decision_gate.py`）

可选研究开关（`sufler.decision_gate_shadow: true`），在即时回答旁仅记录数值评分与判定元数据（不记录对话文本），由 `scripts/gate_bench.py` 评测。详见 [research/decision-gate.md](../research/decision-gate.md)。

### 可选云端层（`src/cloud.py`、`src/privacy.py`）

五项云端开关（`cloud_enrich`、`cloud_live`、`cloud_hints`、`cloud_edit_graph`、`cloud_engine`）全部默认关闭：
- CLI 循环通过订阅调用 `claude -p`，校验退出码与输出。
- 云端图谱复核只在副本沙盒中执行（`src/file_locks.py`），校验符号链接与破坏性改动后再在图谱锁下原子落盘。
- 云端聊天（`llm.engine: cloud` + `sufler.cloud_engine: true`）将密钥放在独立 `0600` 文件中（`llm.cloud_key_file`），错误输出自动脱敏；STT、说话人分离、嵌入与 NLI 始终保持本地。

## 两遍说话人分离

1. **实时遍（`src/diarize_live.py`）**：分声道 ERes2Net + pyannote ONNX 追踪及可选流式 Nemotron（`src/live_nemotron.py`）。
2. **会后遍（`src/rebuild_transcript.py`、`src/diarize.py`、`src/diarize_nemotron.py`）**：对完整录音重跑分离、过滤回声、识别机主与合并碎片。详见 [DIARIZATION.md](DIARIZATION.md)。

## 会后流水线（`src/graph_updater.py`）

停止录音后依次执行：录音重跑 → 生成纪要/复盘/要点 → 写入归档（`src/meeting_archive.py`）→ 更新知识图谱 → 可选云端复核。

- **派生文档护照（`src/transcript_origin.py`）**：记录源逐字稿哈希；重跑后自动刷新未手工改动的文档（旧版留存 `transcripts/.prev/`），手工编辑过的文档只更新说话人姓名。
- **幂等保护（`src/once.py`）**：防止重试或崩溃恢复时重复写入。
- **待办规范化**：生成后确定性整理为标准 `- [ ]` 复选框以进入任务窗口。

## 知识图谱与主题档案（`src/charoite_graph/`）

知识图谱是普通 Obsidian Markdown 文件夹，磁盘文件即唯一真相源：
- 目录结构（`src/charoite_schema.py`、`src/graph_links.py`）：会议、人物、系统、核心、阻碍与档案，支持多领域分目录。
- `src/charoite_graph/frontmatter.py` 管理 YAML 头，`src/charoite_graph/redirects.py` 处理合并节点重定向，`src/charoite_graph/safe_write.py` 在共享锁下原子写入。
- 图谱巡检与去重由 `scripts/graph_doctor.py`、`scripts/dedup_graph.py`、`scripts/migrate_placeholders.py` 完成。
- **夜间主题档案（`scripts/nightly.sh`、`scripts/night_gate.py`）**：按 1-hop 邻域增量构建主题摘要，通过源指纹跳过未变化主题，保留「作者编辑」区，并写入词法索引供搜索优先查询。

## 记忆模型与检索（`src/brain.py`、`src/charoite_graph/graph_search.py`）

- 统一 LLM 网关（`src/llm.py`），生成温度固定为 `0` 保证可复现。
- 统一向量模型 `bge-m3`（Ollama）与本地 ONNX NLI 裁判（`src/nli.py`，详见 [MODELS.md](MODELS.md)）。
- `brain.search(profile, query)` 按标题分块、按相对路径建立邻接关系、显式处理 iCloud `UF_HIDDEN` 文件，并将单个来源占比限制在 40% 以内；支持会议实时上下文（`src/threads.py`）。
- MCP 服务器（`src/mcp_server.py`）仅在指定的 `CHAROITE_ROOT` 范围内向客户端暴露实时会议能力。
- 质量评估：`scripts/memory_bench.py` 与 `scripts/retrieval_compare.py`。

## 崩溃恢复、分层边界与更新

- **崩溃恢复**：会话共享单一时间戳，异常中断后的 `.pcm` 会在下次启动自动补齐；音频缓冲与降级契约见 [INVARIANTS.md](INVARIANTS.md)。
- **分层边界（`docs/design/layout.json`）**：由 `scripts/layout_map.py` 和 `tests/test_import_boundaries.py`（在 `scripts/preflight.sh` 与 CI 中运行）强制校验模块分层、导入方向、入口点、运行契约、`mutation_critical` 门以及代码/文档中提到的可执行路径，并生成 `docs/design/layout.md`。
- **就绪检查与更新**：`scripts/doctor.py` 独立检测运行环境与模型；应用更新校验维护者 ed25519 签名清单、Apple Developer ID 签名且绝不打断录音（见 [RELEASING.md](RELEASING.md)）。

## 代码根与数据根分离

- **代码根**：`src/`、`scripts/`、示例配置、`pyproject.toml` 与 `requirements-nemotron.lock`，由 `app/stage_code.sh` 与 `app/make_app.sh` 打包进只读 `.app`。
- **数据根**：`config/config.yaml`、`recordings/`、`transcripts/`、`logs/`、`models/`、`engines/`、`backups/`，由 `src/charoite_paths.py` 通过 `CHAROITE_ROOT` 显式声明（未声明退出码 `5`，远程模型被隐私策略拒绝退出码 `11`，禁止位于 `.app` 内，禁止导入期快照）。
- **图谱物理身份与兼容锁**：图谱写入通过打开的文件描述符确定真实物理目录，`GraphAddresses` 在单次变更内统一锁定主路径与经证实的历史锁路径，避免别名导致锁分裂。
