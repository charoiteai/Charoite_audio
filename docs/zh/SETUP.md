# 安装配置

*[English](../SETUP.md) · [Русский](../ru/SETUP.md) · **中文***

## 1. 依赖

**使用预编译应用（推荐）。** 从[发布页](https://github.com/charoiteai/Charoite_audio/releases)
下载的 Charoite.app 内置 python 运行环境：无需 git clone、venv 或 pip。只需安装
语言模型 Ollama：

```bash
brew install ollama
brew services start ollama
ollama pull qwen3.5:4b   # 随附配置的模型方案（8–16 GB）
```

32 GB 时主模型换成 `qwen3.8:27b-mlx`，64 GB 及以上换成 `qwen3.6:35b-mlx`
（`qwen3.5:4b` 仍作轻量模型）——首次运行向导会按本机内存选定方案并自行下载，见下文。

**两者只装其一：要么用 brew，要么用 Ollama.app，不要同时装。** 应用会启动自己的服务端并占用 11434 端口；此后 brew 服务无法启动，只会悄悄停在 `error` 状态，而 brew 的升级也形同虚设——真正在跑的仍是旧版服务端。症状看上去人畜无害：`ollama --version` 会提示客户端与服务端版本不一致。若已装了应用而你想用 brew：退出应用，关掉它的开机自启（`launchctl disable gui/$(id -u)/com.ollama.ollama`）并删除它——服务会自行起来。

⚠️ **代理。** Ollama 读取环境变量 `HTTP_PROXY`/`HTTPS_PROXY`，而不是 macOS 的系统设置。由 `brew services` 启动的服务不会继承这些变量，因此直连——8 月 13 日实测：直连 6.8 MB/s，走本地代理 39 KB/s，相差一百七十倍。如果你在配置了代理的终端里手动运行 `ollama serve`，下载模型会花上几个小时。

云端层的情况正相反：从应用启动的 `claude -p` 没有 shell 环境，因此 Charoite 会从
`~/.claude/settings.json` 的 `env` 小节自行为它注入代理（会后复盘、夜间复核和实时
回答共用这一处）。如果会后复盘报错「403 Request not allowed」，说明请求直接发往了
api.anthropic.com：请检查那里的 `HTTPS_PROXY`。

**运行时可一键安装。** 首次启动的就绪检查会区分三种状态：正在运行、已安装但
未启动、完全未安装。前两种情况下应用会自行启动它（brew 安装用
`brew services start`，Ollama.app 则直接启动应用）；第三种情况下，若有
Homebrew 就通过它安装，否则打开下载页面。两者都装时启动的是 brew 服务，而不是
第二个实例——否则它们又会争抢端口。

其余一切由应用询问并自动完成：姓名与图谱文件夹在首次运行向导中设置，声纹分离
模型一键安装，权限通过系统对话框授予。

**从源码安装**（开发、自定义构建、非 Apple Silicon）：

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install .
cp config/config.example.zh.yaml config/config.yaml
```

这是中文预设；英文或俄文会议请改为复制 `config/config.example.en.yaml` 或
`config/config.example.yaml`（见 [config/README](config/README.md)），然后按 README
内存表中适合你 Mac 的那一行设置 `llm.model` 与 `llm.small_model`。

应用优先使用内置运行环境，若不存在则使用仓库旁的 `.venv`。自行构建带运行环境的
应用包：`scripts/build_embedded_python.sh && app/make_app.sh`。内置运行环境有意不带
`parakeet-mlx` 和 `mlx-whisper`（它们会拉进约半个 GB 的 torch）：俄语由 GigaAM 识别，
中文由 SenseVoice 识别。若要用 Parakeet（英语）或 Whisper 预设，请从源码安装，或用
`scripts/build_embedded_python.sh --extras` 构建运行环境。

**数据放在哪里。** 应用包自带代码（已签名、只读），因此预编译应用把你的数据——
`config/`、`transcripts/`、`recordings/`、`models/`、`logs/`——放在
`~/Library/Application Support/Charoite`。若要从克隆目录工作，请在 设置 → 连接 →
数据文件夹 中指定它；`~/Charoite_audio` 克隆不会被自动采用——「今天」页面会提示一次。
同时运行克隆目录中的代码由同一栏下另一个明确可见的开关决定：「从此文件夹运行守护
进程代码（开发）」——在那里亲自选定的文件夹默认开启；若是通过「今天」页面的提示采用
克隆目录，则保持关闭，因为那个提示只涉及数据。

具体使用哪些模型由应用建议：首次运行向导会读取本机内存，展示四套现成方案（「完整」
64 GB 起、「精确」32 GB 起、「均衡」16 GB 起、「轻量」8 GB 起）并标注推荐项，选定后
写入配置并一键下载。模型详情见 [MODELS.md](MODELS.md)。

## 2. 配置：两个必填字段

**最简单的方式是在应用里完成。** 首次运行向导会询问你的姓名和图谱文件夹，
并自行写入数据文件夹中的 `config/config.yaml`；文件夹通过面板选择。若配置文件
尚不存在，向导会从安装包内的示例创建它，并自行建立 `config/` 目录。如果写入失败
（数据目录无写权限、安装包损坏缺少示例），向导会直接说明原因，而不是显示
「已保存」：在这里静默失败意味着用户配置了个寂寞，并卡在永远红色的就绪状态。
下面手动编辑文件的做法，是给不使用界面安装的人准备的。

在 `config/config.yaml` 中：

- `sufler.user_name`——你的名字：在逐字稿中标记你的麦克风，且绝不会被分配给其他声音。在线上会议中你的发言会署上这个名字，因为麦克风与对话者所在的系统音频是两条独立音轨。有三种情况不会署名，且 Charoite 会明确告知：线下会议（扬声器里没有声音——一个麦克风录下整个房间，无从区分）、你的麦克风里有多个不同的声音（身边坐着同事），以及与中性标签无法区分的名字（「Собеседник」「Собеседник 2」）——这类名字会被直接拒绝。
- `sufler.graph_dir`——知识图谱文件夹（留空**或上级目录不存在** = 图谱关闭，转写仍正常工作：路径写错只会少了图谱，不会让会议变成处理失败）。指向你的 Obsidian 仓库内的目录，例如 `~/Documents/Obsidian/Work`——目录结构由 Charoite 自行创建。相对路径（`demo/graph`）由应用和所有脚本从 Charoite 数据文件夹（`config/` 所在处）起算，而不是从脚本碰巧启动的目录起算；环境变量 `CHAROITE_GRAPH_DIR`（或旧名 `SUFLER_GRAPH_DIR`）会覆盖该设置，用于在另一个图谱上试运行，同时把图谱发现范围收窄到该图谱所在的 vault（不读取 iCloud 目录）。

建议同时填写：`sufler.user_context`（用 1-2 句话介绍你的工作）——即时回答所用的上下文。

**语言。** 界面和会议文档都跟随 `sufler.language`（`ru`、`en` 或 `zh`），而不是系统
区域设置。应用自带的示例配置是俄文的，因此要用中文或英文，请在配置中设置该键并重启
应用。语音识别是另一对键：`stt.backend` 与 `stt.language`——内置运行环境能运行哪些
后端，见第 1 节。

## 3. 系统音频（通话）— 一次授权加一次重启

应用通过 macOS 自身（ScreenCaptureKit）捕获会议音频。首次录制时系统会询问一次
「屏幕与系统音频录制」权限——点击「允许」，**然后重启 Charoite**。

重启并非多此一举：macOS 仅对重新启动后的进程应用已授予的权限。在重启之前，
系统设置里的勾选已经打上，捕获却依然失败——这场会议将录不到对方的声音。首次
运行的就绪面板会用单独一行提示需要重启。

重启之后就完成了：不需要驱动、不需要音频 MIDI 设置、不需要切换输出设备。声音
照常从扬声器播放；在 macOS 15 及以上，麦克风也通过同一数据流传入。

分离通道免费提供「你／对方」的说话人区分与回声过滤。

**备用方案 — BlackHole**（权限被拒绝，或不经应用直接在终端运行：ScreenCaptureKit
音频流由应用建立，守护进程只负责读取）：

1. 安装 [BlackHole 2ch](https://existential.audio/blackhole/)。
2. 音频 MIDI 设置 →「+」→ 多输出设备 → 勾选扬声器和 BlackHole。
3. 系统输出 → 该多输出设备（既能听到声音，Charoite 也能收到）。

Charoite 自行选择音源：优先 ScreenCaptureKit，其次 BlackHole。会议状态栏会
显示当前使用的通道。

如果完全没有对方的音频通道（「屏幕录制」权限被撤销、BlackHole 未配置），或者设备
存在但在开始时无法打开（被占用、被撤销），录制
仍会开始——但只有麦克风——并且 Charoite 会在开始的那一刻立即告知：会议状态栏
中的一行提示，加上带声音的系统通知，并说明原因。这行提示会一直保持红色直到会议
结束——普通状态更新不会把它挤走。不会再出现悄无声息的「会议
录制时没有对方声音」。对方音频通道在会议中途失效（连续两次重启失败，或重启卡死）时，
会发出同样的警报——每次中断仅一次，并给出建议；通道恢复后，这行提示会被
撤下并予以说明。带声音的通知每场会议最多三次，状态栏提示则每次中断都有。例外是 `config.yaml` 中的 `device: mic`：那是有意选择
麦克风，不会发出关于对方声道的警告。

您自己的麦克风也一样：它中途失效（耳机断开、扩展坞消失、音频流无法重启）或在对方声道正常时启动失败——同样的警报：「录音中没有您的麦克风，此后只录对方」，
带通知，并写入 capture.log。建议不同：不要重启录音，而是检查麦克风——看门狗会自行重启声道，提示随之消失。两个声道都丢失（macOS 15+ 上它们共用一个流）显示「录音为空」。
红色提示行对所有声道只有一条：每次丢失时重新组合，只有全部恢复录制才会撤下；一个声道恢复而另一个仍失效时，提示会说明仍缺少什么。

每一次丢失与恢复也会留在会议本身：会议脉络和逐字稿笔记中各有一行，纪要里则有一条
「录音不完整」的说明——这样事后不会把空白误当成沉默。

## 4. macOS 权限

- **麦克风**——首次运行时请求授权。
- **屏幕与系统音频录制**——首次录制会议时请求授权；没有它只能听到麦克风
  （或你已配置的 BlackHole）。
- **通知**——首次录制时请求：声道丢失警报与自动停止预告以横幅送达；没有它只剩应用
  内的那一行提示。
- **辅助功能**（可选）——仅用于听写：自动粘贴到你正在输入的字段，以及在 macOS 26
  上说话时的实时草稿面板；没有该权限，文本只会留在剪贴板里。
- **日历**（可选）——仅在开启 设置 → 日历 →「简报与录制提醒」时需要：只在本地读取
  事件的标题和时间。
- **完全磁盘访问**（可选）——仅用于「语音备忘录」桥接（`audio.voice_memos_bridge`）：
  语音备忘录的容器受系统保护。该授权范围很广（整个用户资源库），由你来决定。

## 5. 声纹说话人分离（可选）

一条命令放好两个声纹模型：ERes2Net 嵌入模型在 `models/diar/embedding.onnx`，
分段模型在 `models/diar/segmentation.onnx`（在应用中是首次运行向导里的
「区分不同说话人」按钮）：

```bash
.venv/bin/python scripts/get_models.py --diar    # 嵌入模型可选：--list
```

详情与调优见 [DIARIZATION.md](DIARIZATION.md)。没有它们时按声道标注（你/对方），两个都有时按声音标注（“Speaker 1/2/…”）。只有嵌入、没有分段时，实时标注处于简化模式，会后也不会重新标注说话人。

可选的 Nemotron 引擎（Apple Silicon，会后说话人分离）从终端安装，并指明数据文件夹——当代码位于应用包内时，使用应用自带的 Python：

```bash
CHAROITE_ROOT=<数据文件夹> <应用 Python> scripts/install_engine.py nemotron          # 安装
CHAROITE_ROOT=<数据文件夹> <应用 Python> scripts/install_engine.py nemotron --check  # 查看已装内容，不联网
CHAROITE_ROOT=<数据文件夹> <应用 Python> scripts/install_engine.py nemotron --plan   # 以一行 JSON 输出计划，不联网
```

退出码如实反映结果：0 —— 已安装且探测通过；12（`EXIT_INSTALL_BUSY`）—— 机器正忙或另一次安装正在进行；13（`EXIT_INSTALL_CANCELLED`）—— 已取消；1 —— 拒绝。详情见 [DIARIZATION.md](DIARIZATION.md)。

## 6. 运行

```bash
CHAROITE_ROOT="$PWD" .venv/bin/python src/main.py     # CLI：实时逐字稿 + 提示
# 或用于 UI 集成的守护进程（通过 stdout/stdin 传 NDJSON）。它不会猜测数据
# 放在哪里——由启动者指明根目录：
CHAROITE_ROOT="$PWD" .venv/bin/python src/daemon.py
```

首次运行会下载 STT 模型（约 1 分钟）。说句话——逐字稿会出现在控制台里。

`CHAROITE_ROOT` 是数据文件夹。守护进程、`src/main.py`、逐字稿重建、导入以及流水线的
其他入口都不会根据代码所在位置推断它：没有它，它们会在做任何事之前停下，给出一行修复
方法并以退出码 5 退出（`src/exit_codes.py` 中的 `EXIT_ROOT_UNNAMED`）——不同于 argparse
的 2，好让 launchd 和脚本区分「没开始」与「崩溃」。应用总会自行传入根目录。

第一次成功的录音应当以一张会议卡片收尾，而不仅仅是一个逐字稿文件。请按
[用户实用指南](USER_GUIDE.md)里的端到端检查走一遍。临时音频、逐字稿、图谱
文档与保留期的完整地图见[数据与恢复](DATA_AND_RECOVERY.md)。

## 7. 各文件的位置

相对于数据文件夹（预编译应用：`~/Library/Application Support/Charoite`；从源码运行：
`CHAROITE_ROOT` 指明的克隆目录）：

- `transcripts/` — 逐字稿与会议的工作文件
- `recordings/` — 完整录音（按 `record_keep_days` 自动删除）
- `<graph_dir>/Встречи-архив/` — 每场会议一个「日期 — 标题」文件夹：
  摘要、纪要、逐字稿、问答、复盘

这只是一张简图。凡是涉及删除期限、事实来源和故障后的恢复顺序，请使用
[完整的数据地图](DATA_AND_RECOVERY.md)。

## 故障排查

- **逐字稿为空**——检查输入设备：`.venv/bin/python -c "import sounddevice as sd; print(sd.query_devices())"`。
- **回答慢**——`ollama ps`：模型必须常驻内存；配置中保持 `num_ctx: 8192`。
- **没有系统音频**——检查权限：系统设置 → 隐私与安全性 → 屏幕与系统音频录制，
  Charoite 必须在列表中并处于开启状态。更换应用版本后有时需要重新授权：取消
  勾选再重新勾选。若你把 BlackHole 用作备用路径，则 macOS 的输出必须是多输出
  设备，而不是直接输出到扬声器。

## 语义搜索（推荐）

当 Ollama 中提供 `bge-m3` 嵌入模型时，应用的归档搜索会增加语义层：

```bash
ollama pull bge-m3   # 约 1.2 GB；没有它搜索只做词法匹配
```

索引在首次搜索时于后台构建，并随图谱变化增量更新（存储于 `~/Library/Application Support/Charoite/semantic_index_v2.bin`）。

## 诊断

`python3 scripts/doctor.py` 会检查 Python、依赖、配置键、图谱文件夹、Ollama 及其模型（含 `bge-m3`）、选用 SenseVoice 后端时的模型，以及说话人分离——并为每个问题给出确切的修复方法。

报告的后半部分关心的是运行，而不是安装：模型能否响应一次**生成**探测（卡住的
Ollama 会瞬间返回模型列表，而推理原地不动——这是区分两者的唯一方法）、有没有
会议卡在通往图谱的路上、导入文件夹里还有多少文件在排队、磁盘还剩多少空间。
任何「Charoite 没反应」都从这里开始查。

doctor 读取 `CHAROITE_ROOT` 指明的数据文件夹，没有时读取它所在的克隆目录。要检查
预编译应用的数据，请在克隆目录中运行
`CHAROITE_ROOT="$HOME/Library/Application Support/Charoite" python3 scripts/doctor.py`。
它自己不会修改任何东西；唯一的例外是显式的 `--restart-llm`：当卡住的生成占着模型
服务、而流水线自身的看门狗又没有触发时，用它紧急重启模型服务。

doctor 是唯一一个用任何 Python 都能跑的脚本：它刻意写成零依赖，好在依赖装好
之前就能回答问题。其余脚本都通过 `.venv/bin/python` 运行——若用系统 Python
启动，得到的会是一行修复建议，而不是一段堆栈回溯（`src/deps.py`）。

## Claude Code 工具（可选）

`src/mcp_server.py` 是一个 MCP 服务器，把进行中（或最近一场）的会议作为工具交给
Claude Code：状态、实时逐字稿、笔记、提示、由本地模型生成的纪要以及图谱更新。注册时
请指明数据文件夹：

```bash
claude mcp add sufler -e CHAROITE_ROOT="$PWD" -- "$PWD/.venv/bin/python" "$PWD/src/mcp_server.py"
```

若注册时没有 `CHAROITE_ROOT`，服务器照样启动，但每个工具都会以工具错误作答，并附上
这条命令（以及供其他 MCP 客户端使用、带 `env` 的 JSON 块）：在根目录被指明之前，
不读取任何内容、不调用模型，也不启动图谱更新。

## 版本：应用、代码与发布

从仓库安装时存在三样东西，而它们会悄然分叉：应用（`~/Applications` 中的 `.app`）、
工作目录中的代码（守护进程与夜间处理实际运行的就是它），以及 GitHub 上的最新发布。
0.47.0 已发布而应用仍是 0.46.0，看上去完全正常；落后十来个提交的目录同样如此。
等你花半天去修一个上游早已不存在的错误时才会发现。

应用会比对这三者，一旦分叉就在「今天」标签页说明。版本一致是常态，不会占用一行：
关于常态的提醒，一周之内就没人再看。代码版本取自工作目录的 git 标签；发布号则在你
回到应用时向 GitHub 公共 API 发一次普通 GET 获取，最多每四小时一次（过去一天一次的
间隔让发布日变成了等待日）—— 无令牌、不含任何关于你的字节，网络出错时保持沉默。
不需要就在 设置 →「向 GitHub 查询新版本」中关闭，或在配置中设置
`sufler.check_updates: false`；总开关 `CHAROITE_NO_CLOUD` 同样会关闭这项检查。

## 夜间循环（可选）

`scripts/nightly.sh` 在你睡觉时保持图谱整洁：图谱健康检查、Tier-3 核心修订（去重、合并——均有备份）、主题档案、晨间简报 `_Сегодня.md`（当天的现成上下文），以及记忆基准测试（质量回归信号）。夜间处理会等待会议解析结束，并且只使用一个模型。8 月 12 日两者撞在一起：转写、
内核修订与档案生成同时进行 —— 64 GB 中仅剩 14 GB 可用，另有 17 GB 已被压缩。
本地服务开始来回换入换出模型（一次处理加载 41 次），请求开始挂起 2-6 分钟，
随后彻底宕掉：258 个主题没有得到分析。等待上限为一小时（`NIGHTLY_WAIT`，秒）：
整夜跳过比在拥挤中工作更糟。夜间处理也必须在早上之前结束：`NIGHTLY_MAX_H`（默认
4 小时）是整次运行的上限——长步骤会在主题之间停下，剩下的由下一夜接着做。

步骤顺序是为了保证简报无论如何都能在早晨就绪：它在重活之前先写一次，
结束时在整理过的核心之上再写一次。8 月 13 日的教训代价是整个上午——
图谱已长到三百个核心，全量修订跑到第五个小时，而简报仍排在最后等待。
现在工作日的修订按增量运行（`--since-last`：只判定自上次运行以来变动过的核心），
全量比对放在周日，或用 `NIGHTLY_TIER3_FULL=1` 手动触发。

对于从克隆目录安装的情况，应用可以替你设置定时：设置 → 夜间流程 → 开启 会写入下面的
launchd 代理（04:15，日志在数据旁的 `logs/nightly.log`）。手动设置：

```xml
<!-- ~/Library/LaunchAgents/ai.charoite.nightly.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.charoite.nightly</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>/PATH/TO/Charoite_audio/scripts/nightly.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>/PATH/TO/Charoite_audio/logs/nightly.log</string>
  <key>StandardErrorPath</key><string>/PATH/TO/Charoite_audio/logs/nightly.log</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/ai.charoite.nightly.plist
```

夜间处理是否跑过，可在应用「今天」标签页近期会议一栏的底部看到。夜间工作天生不可见：
人在睡觉，而早上整理过的图谱和没动过的图谱看起来一模一样。因此脚本会把结果写入数据旁边的
`logs/nightly.json`（日志是给人读的，不是给应用做判断的；而且 launchd 日志过去放在
`/tmp`，重启即消失，「从未运行」与「文件被清掉」无从分辨），再由应用读取。成功的一次只是一行平静的时间；正在进行的处理、
失败的步骤、被中断的运行、Mac 睡过去的夜晚以及被跳过的夜晚都会高亮显示——出问题的夜晚
还会把菜单栏图标染成黄色。

只有毫无差错的夜晚才算成功。模型沉默会被单独捕捉：本地服务在中途宕掉时，档案会在
无内容可依的情况下生成 —— 主题得不到分析，而步骤仍以零退出。这样的夜晚会标记为
`досье(модель-молчала)`，否则图谱会在无人察觉中变陈旧。

应用还会检查代理指向的路径：若仓库搬过家，`plist` 仍会从旧位置启动脚本 —— 每晚都在用
旧版代码改图谱。这时「今天」上的那一行会说明夜间处理来自另一个文件夹，并给出脚本路径。
