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

**最简单的方式是在应用里完成。** 首次运行向导会询问你的姓名和图谱文件夹，
并自行写入 `config/config.yaml`；文件夹通过面板选择。若配置文件尚不存在，
向导会从安装包内的示例创建它，并自行建立 `config/` 目录。如果写入失败
（数据目录无写权限、安装包损坏缺少示例），向导会直接说明原因，而不是显示
「已保存」：在这里静默失败意味着用户配置了个寂寞，并卡在永远红色的就绪状态。
下面手动编辑文件的做法，是给不使用界面安装的人准备的。

在 `config/config.yaml` 中：

- `sufler.user_name`——你的名字：在逐字稿中标记你的麦克风，且绝不会被分配给其他声音。
- `sufler.graph_dir`——知识图谱文件夹（留空 = 图谱关闭，转写仍正常工作）。指向你的 Obsidian 仓库内的目录，例如 `~/Documents/Obsidian/Work`——目录结构由 Charoite 自行创建。

建议同时填写：`sufler.user_context`（用 1-2 句话介绍你的工作）——即时回答所用的上下文。

## 3. 系统音频（通话）— 一次授权加一次重启

应用通过 macOS 自身（ScreenCaptureKit）捕获会议音频。首次录制时系统会询问一次
「屏幕与系统音频录制」权限——点击「允许」，**然后重启 Charoite**。

重启并非多此一举：macOS 仅对重新启动后的进程应用已授予的权限。在重启之前，
系统设置里的勾选已经打上，捕获却依然失败——这场会议将录不到对方的声音。首次
运行的就绪面板会用单独一行提示需要重启。

重启之后就完成了：不需要驱动、不需要音频 MIDI 设置、不需要切换输出设备。声音
照常从扬声器播放；在 macOS 15 及以上，麦克风也通过同一数据流传入。

分离通道免费提供「你／对方」的说话人区分与回声过滤。

**备用方案 — BlackHole**（macOS 13 之前，或权限被拒绝）：

1. 安装 [BlackHole 2ch](https://existential.audio/blackhole/)。
2. 音频 MIDI 设置 →「+」→ 多输出设备 → 勾选扬声器和 BlackHole。
3. 系统输出 → 该多输出设备（既能听到声音，Charoite 也能收到）。

Charoite 自行选择音源：优先 ScreenCaptureKit，其次 BlackHole。会议状态栏会
显示当前使用的通道。

## 4. macOS 权限

- **麦克风**——首次运行时请求授权。
- **屏幕与系统音频录制**——首次录制会议时请求授权；没有它只能听到麦克风
  （或你已配置的 BlackHole）。
- **辅助功能（Universal Access）**（可选）——仅用于听写自动粘贴；没有该权限，文本只会留在剪贴板里。

## 5. 声纹说话人分离（可选）

将 ERes2Net 嵌入模型放到 `models/diar/embedding.onnx`——参见 [DIARIZATION.zh.md](DIARIZATION.md)。没有它时按声道标注（你/对方），有它时按声音标注（“Speaker 1/2/…”）。

## 6. 运行

```bash
.venv/bin/python src/main.py     # CLI: live transcript + hints
.venv/bin/python src/daemon.py   # daemon for UI integration (NDJSON)
```

首次运行会下载 STT 模型（约 1 分钟）。

第一次成功的录音应当以一张会议卡片收尾，而不仅仅是一个逐字稿文件。请按
[用户实用指南](USER_GUIDE.md)里的端到端检查走一遍。临时音频、逐字稿、图谱
文档与保留期的完整地图见[数据与恢复](DATA_AND_RECOVERY.md)。

## 7. 各文件的位置

- `transcripts/` — 逐字稿与会议的工作文件
- `recordings/` — 完整录音（按 `record_keep_days` 自动删除）
- `<graph_dir>/Встречи-архив/` — 每场会议一个「日期 — 标题」文件夹：
  摘要、纪要、逐字稿、问答、复盘

这只是一张简图。凡是涉及删除期限、事实来源和故障后的恢复顺序，请使用
[完整的数据地图](DATA_AND_RECOVERY.md)。

## 故障排查

- **逐字稿为空**——检查输入设备：`python -c "import sounddevice as sd; print(sd.query_devices())"`。
- **回答慢**——`ollama ps`：模型必须常驻内存；配置中保持 `num_ctx: 8192`。
- **没有系统音频**——检查权限：系统设置 → 隐私与安全性 → 屏幕与系统音频录制，
  Charoite 必须在列表中并处于开启状态。更换应用版本后有时需要重新授权：取消
  勾选再重新勾选。若你把 BlackHole 用作备用路径，则 macOS 的输出必须是多输出
  设备，而不是直接输出到扬声器。

## 语义搜索（推荐）

当 Ollama 中提供 `bge-m3` 嵌入模型时，应用的归档搜索会增加语义层：

```bash
ollama pull bge-m3   # ~1.2 GB; without it search is lexical-only
```

索引在首次搜索时于后台构建，并随图谱变化增量更新（存储于 `~/Library/Application Support/Charoite/semantic_index_v2.bin`）。

## 诊断

`python3 scripts/doctor.py` 会检查 Python、依赖、配置键、图谱文件夹、Ollama 及其模型（含 `bge-m3`）以及说话人分离——并为每个问题给出确切的修复方法。

报告的后半部分关心的是运行，而不是安装：模型能否响应一次**生成**探测（卡住的
Ollama 会瞬间返回模型列表，而推理原地不动——这是区分两者的唯一方法）、有没有
会议卡在通往图谱的路上、导入文件夹里还有多少文件在排队、磁盘还剩多少空间。
任何「Charoite 没反应」都从这里开始查。

doctor 是唯一一个用任何 Python 都能跑的脚本：它刻意写成零依赖，好在依赖装好
之前就能回答问题。其余脚本都通过 `.venv/bin/python` 运行——若用系统 Python
启动，得到的会是一行修复建议，而不是一段堆栈回溯（`src/deps.py`）。

## 版本：应用、代码与发布

从仓库安装时存在三样东西，而它们会悄然分叉：应用（`~/Applications` 中的 `.app`）、
工作目录中的代码（守护进程与夜间处理实际运行的就是它），以及 GitHub 上的最新发布。
0.47.0 已发布而应用仍是 0.46.0，看上去完全正常；落后十来个提交的目录同样如此。
等你花半天去修一个上游早已不存在的错误时才会发现。

应用会比对这三者，一旦分叉就在「今天」标签页说明。版本一致是常态，不会占用一行：
关于常态的提醒，一周之内就没人再看。代码版本取自工作目录的 git 标签；发布号则是每天
一次对 GitHub 公共 API 的普通 GET —— 无令牌、不含任何关于你的字节，网络出错时保持沉默。
不需要就在配置中设置 `sufler.check_updates: false`；总开关 `CHAROITE_NO_CLOUD`
同样会关闭这项检查。

## 夜间循环（可选）

`scripts/nightly.sh` 在你睡觉时保持图谱整洁：Tier-3 核心修订（去重、合并——均有备份）、晨间简报 `_Сегодня.md`（当天的现成上下文），以及记忆基准测试（质量回归信号）。夜间处理会等待会议解析结束，并且只使用一个模型。8 月 12 日两者撞在一起：转写、
内核修订与档案生成同时进行 —— 64 GB 中仅剩 14 GB 可用，另有 17 GB 已被压缩。
本地服务开始来回换入换出模型（一次处理加载 41 次），请求开始挂起 2-6 分钟，
随后彻底宕掉：258 个主题没有得到分析。等待上限为一小时（`NIGHTLY_WAIT`，秒）：
整夜跳过比在拥挤中工作更糟。

步骤顺序是为了保证简报无论如何都能在早晨就绪：它在重活之前先写一次，
结束时在整理过的核心之上再写一次。8 月 13 日的教训代价是整个上午——
图谱已长到三百个核心，全量修订跑到第五个小时，而简报仍排在最后等待。
现在工作日的修订按增量运行（`--since-last`：只判定自上次运行以来变动过的核心），
全量比对放在周日，或用 `NIGHTLY_TIER3_FULL=1` 手动触发。

用 launchd 设置定时任务：

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

夜间处理是否跑过，可在应用「今天」标签页近期会议一栏的底部看到。夜间工作天生不可见：
人在睡觉，而早上整理过的图谱和没动过的图谱看起来一模一样。因此脚本会把结果写入数据旁边的
`logs/nightly.json`（launchd 日志位于 `/tmp`，重启即消失，「从未运行」与「文件被清掉」
无从分辨），再由应用读取。成功的一次只是一行平静的时间；正在进行的处理、失败的步骤、被中断的运行
以及被跳过的夜晚都会高亮显示。

只有毫无差错的夜晚才算成功。模型沉默会被单独捕捉：本地服务在中途宕掉时，档案会在
无内容可依的情况下生成 —— 主题得不到分析，而步骤仍以零退出。这样的夜晚会标记为
`досье(модель-молчала)`，否则图谱会在无人察觉中变陈旧。

另请单独检查代理指向的路径：若仓库搬过家，`plist` 仍会从旧位置启动脚本 —— 每晚都在用
旧版代码改图谱，而没有状态文件就无从察觉。
