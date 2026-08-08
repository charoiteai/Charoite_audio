# 安装配置

*[English](../SETUP.md) · [Русский](../ru/SETUP.md) · **中文***

## 1. 依赖

**使用预编译应用（推荐）。** 从[发布页](https://github.com/charoiteai/Charoite_audio/releases)
下载的 Charoite.app 内置 python 运行环境：无需 git clone、venv 或 pip。只需安装
语言模型 Ollama：

```bash
brew install ollama
ollama pull qwen3.6:35b-a3b && ollama pull qwen3.5:4b && ollama pull gemma4:latest
```

其余一切由应用询问并自动完成：姓名与图谱文件夹在首次运行向导中设置，声纹分离
模型一键安装，权限通过系统对话框授予。

**从源码安装**（开发、自定义构建、非 Apple Silicon）：

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install .
cp config/config.example.yaml config/config.yaml
```

应用优先使用内置运行环境，若不存在则使用仓库旁的 `.venv`。自行构建带运行环境的
应用包：`scripts/build_embedded_python.sh && app/make_app.sh`。

具体使用哪些模型由应用建议：首次运行向导会读取本机内存，展示三套现成方案（「完整」「均衡」「轻量」）并标注推荐项，选定后写入配置并一键下载。模型详情见 [MODELS.md](MODELS.md)。

## 2. 配置：两个必填字段

在 `config/config.yaml` 中：

- `sufler.user_name`——你的名字：在逐字稿中标记你的麦克风，且绝不会被分配给其他声音。
- `sufler.graph_dir`——知识图谱文件夹（留空 = 图谱关闭，转写仍正常工作）。指向你的 Obsidian 仓库内的目录，例如 `~/Documents/Obsidian/Work`——目录结构由 Charoite 自行创建。

建议同时填写：`sufler.user_context`（用 1-2 句话介绍你的工作）——即时回答所用的上下文。

## 3. 系统音频（通话）— 无需任何设置

应用通过 macOS 自身（ScreenCaptureKit）捕获会议音频。首次录制时系统会询问一次
「屏幕与系统音频录制」权限——点击「允许」即可。就这样：不需要驱动、不需要
音频 MIDI 设置、不需要切换输出设备。声音照常从扬声器播放；在 macOS 15 及以上，
麦克风也通过同一数据流传入。

分离通道免费提供「你／对方」的说话人区分与回声过滤。

**备用方案 — BlackHole**（macOS 13 之前，或权限被拒绝）：

1. 安装 [BlackHole 2ch](https://existential.audio/blackhole/)。
2. 音频 MIDI 设置 →「+」→ 多输出设备 → 勾选扬声器和 BlackHole。
3. 系统输出 → 该多输出设备（既能听到声音，Charoite 也能收到）。

Charoite 自行选择音源：优先 ScreenCaptureKit，其次 BlackHole。会议状态栏会
显示当前使用的通道。

## 4. macOS 权限

- **麦克风**——首次运行时请求授权。
- **辅助功能（Universal Access）**（可选）——仅用于听写自动粘贴；没有该权限，文本只会留在剪贴板里。

## 5. 声纹说话人分离（可选）

将 ERes2Net 嵌入模型放到 `models/diar/embedding.onnx`——参见 [DIARIZATION.md](../DIARIZATION.md)。没有它时按声道标注（你/对方），有它时按声音标注（“Speaker 1/2/…”）。

## 6. 运行

```bash
.venv/bin/python src/main.py     # CLI: live transcript + hints
.venv/bin/python src/daemon.py   # daemon for UI integration (NDJSON)
```

首次运行会下载 STT 模型（约 1 分钟）。

## 故障排查

- **逐字稿为空**——检查输入设备：`python -c "import sounddevice as sd; print(sd.query_devices())"`。
- **回答慢**——`ollama ps`：模型必须常驻内存；配置中保持 `num_ctx: 8192`。
- **没有系统音频**——macOS 的输出必须是多输出设备。

## 语义搜索（推荐）

当 Ollama 中提供 `bge-m3` 嵌入模型时，应用的归档搜索会增加语义层：

```bash
ollama pull bge-m3   # ~1.2 GB; without it search is lexical-only
```

索引在首次搜索时于后台构建，并随图谱变化增量更新（存储于 `~/Library/Application Support/Charoite/semantic_index.json`）。

## 诊断

`python3 scripts/doctor.py` 会检查 Python、依赖、配置键、图谱文件夹、Ollama 及其模型（含 `bge-m3`）以及说话人分离——并为每个问题给出确切的修复方法。

## 夜间循环（可选）

`scripts/nightly.sh` 在你睡觉时保持图谱整洁：Tier-3 核心修订（去重、合并——均有备份）、晨间简报 `_Сегодня.md`（当天的现成上下文），以及记忆基准测试（质量回归信号）。用 launchd 设置定时任务：

```xml
<!-- ~/Library/LaunchAgents/ai.charoite.nightly.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.charoite.nightly</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>/PATH/TO/Charoite_audio/scripts/nightly.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>/tmp/charoite_nightly.log</string>
  <key>StandardErrorPath</key><string>/tmp/charoite_nightly.log</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/ai.charoite.nightly.plist
```
