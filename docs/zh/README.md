# Charoite（紫龙晶）

[![CI](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml) [![swift-tests](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml) [![CodeQL](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml) ![License](https://img.shields.io/badge/license-Apache--2.0-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey) ![Local](https://img.shields.io/badge/cloud-none%20by%20default-success) [![Release](https://img.shields.io/github/v/release/charoiteai/Charoite_audio)](https://github.com/charoiteai/Charoite_audio/releases)

**完全本地运行的 AI 会议助手：说话人分离 + 自更新知识图谱。任何数据都不会离开您的 Mac。**

*[English](../../README.md) · [Русский](../ru/README.md)*

> **[宣言：local-first ≠ local-only](MANIFESTO.md)** — 数月真实会议教会我们的
> 事：本地模型在哪里出色、为什么知识图谱由强模型维护。数字、边界，
> 以及能证明我们错了的那个测试。

![Charoite 应用 — 实时逐字稿、要点、档案问答](../img/app-main.png)

![会议任务 — 图谱中所有复选框汇于一窗](../img/app-tasks.png)

Charoite 倾听您的会议（麦克风 + 系统声音，无需机器人入会），本地转写、区分说话人、会议进行中即时回答问题，会后自动构建 Obsidian 知识图谱：人物、系统、决定和贯穿性主题在所有会议之间被持续记住。

## 为什么选 Charoite

- **默认 100% 本地。** 音频、语音识别、说话人分离、LLM 摘要——全部在您的机器上（Ollama + ONNX）。无云端、无遥测、无账号。可选的 Claude 云层默认关闭。
- **真正可用的说话人分离。** 会议中实时显示「说话人 1/2/…」标签，会后对完整录音离线重建——每位说话人的段落干净利落。有人自我介绍时姓名自动关联——绝不靠猜。
- **知识图谱，而不是笔记堆。** 会议成为episode；人物、系统、决定成为节点；反复出现的主题成为带状态和历史的「核心」。会议中 Charoite 会低声提示：「⏮ 7月15日讨论过，当时的状态是……」。
- **主题档案。** 夜间由本地模型为每个贯穿性主题生成综述：当前状态、时间线、已定事项、未决问题、相关人员——每一条都链接到来源节点。检索**优先**查询档案索引，因此「这个主题现在怎么样」由一份写好的综述回答，而不是十几个零散片段。重建是增量的：档案带有其来源的指纹，未变动的主题不会惊动模型。手工补充写在「作者修改」小节，重建时保留。
- **云端模型——可选，每一步都可选。** 默认全部关闭，逐项启用：会后复盘（`cloud_enrich`）、对话节奏中的第二意见（`cloud_live`）、带修改权限的档案复核（`cloud_edit_graph`）。基于订阅运行，环境中不放 API 密钥。哪些内容会上云、哪些永不上云——见 [PRIVACY.zh.md](PRIVACY.md)。
- **每场会议的分层文档**：一分钟摘要（含与过往会议的关联）→ 会议纪要 → 复盘 → 完整逐字稿。想读多深读多深。
- **实时辅助**：对方向您提问时的即时本地回答（⚡）、自动要点、实时纪要草稿、语音笔记和听写。
- **中文原生的 LLM。** 默认主模型是 Qwen（阿里巴巴出品）——中文就是它的母语；语音识别用 Whisper（`stt.backend: whisper`，`language: zh`）。

## 与其他方案的对比

|  | 云端会议助手 | 本地转写工具 | **Charoite** |
|---|:---:|:---:|:---:|
| 音频留在您的机器上 | ✗ | ✓ | ✓ |
| 无机器人加入通话 | 视情况 | ✓ | ✓ |
| 说话人分离 | ✓ | 少见 | ✓ |
| 会议进行中的实时提示 | ✓ | ✗ | ✓ |
| 跨会议记忆（知识图谱） | ✗ | ✗ | ✓ |
| 完全离线可用 | ✗ | ✓ | ✓ |
| 开源 | 少见 | 视情况 | ✓ |

云端助手很聪明，但您的对话存放在别人的服务器上。本地转写工具保护隐私，
却在会议结束的那一刻就忘掉它。Charoite 同时守住两个承诺：一切留在 Mac 上，
而且每场会议都让下一场更聪明。

## 系统要求

- Apple Silicon Mac（M1 及以上），应用需要 macOS 14+，默认模型建议 32 GB 内存
- [Ollama](https://ollama.com) — 哪些模型放得下，见下方内存表
- Python 3.11+（仅在从源码运行时需要——应用自带运行环境）
- 通话声音通过 macOS 自身（ScreenCaptureKit）捕获，无需设置：系统只会请求一次权限。仅在 macOS 13 之前或权限被拒绝时才需要 [BlackHole](https://existential.audio/blackhole/)
- 可选：[Obsidian](https://obsidian.md) 浏览图谱

## 按内存选模型

一切本地运行。STT（约1 GB）和说话人分离（约0.5 GB）恒定；随内存扩展的是 LLM。全程 `num_ctx: 8192`。语义搜索需另加 `bge-m3`（约1.2 GB）——16 GB 以上推荐开启。

| 内存 | 主 LLM | 轻量 LLM | 图谱 | 能得到什么 |
|----|----|----|----|----|
| **8 GB** | `qwen3.5:4b` | 同左 | 无 | 逐字稿、要点、纪要、基础提示 |
| **16 GB** | `gemma4:latest` | `qwen3.5:2b` | 慢 | 完整实时闭环——推荐入门配置 |
| **32 GB** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | 有 | 默认配置，基准测试即在此配置 |
| **64 GB+** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | 有 | 云端 Claude 层 + 超长会议的余量 |

16 GB 以下知识图谱关闭（30B 以下的模型会破坏 JSON 结构）；4 GB 只跑 STT，`llm.base_url` 指向另一台机器即可。

**iOS/iPadOS**：手机负责 STT 和轻量生成，更重的任务通过 REST API 交给 Mac。
在 iOS 26+ 上，内置的约 3B Foundation Models 免费承担要点提取。
完整的 macOS/iOS 模型表和选型依据：[docs/MODELS.zh.md](MODELS.md)。

## 快速开始

**方式 A — macOS 应用（推荐）。** Python 已内置于应用中：无需 `git clone`、
`venv` 或 `pip`。只需安装语言模型：

```bash
brew install ollama && brew services start ollama   # 具体用哪些模型由应用建议
```

**要么装 brew，要么装 Ollama.app，不要同时装。** 应用会启动自己的服务端并占用
11434 端口，此后 brew 服务只会悄悄停在 `error` 状态，其升级也不会生效——
详见 [SETUP.zh.md](SETUP.md)。

从[最新发布](https://github.com/charoiteai/Charoite_audio/releases/latest)下载
`Charoite.dmg`，把应用拖入「应用程序」—— 映像窗口会指明位置。应用使用
Developer ID 签名但未经公证，因此首次启动会被 macOS 拦截：系统设置 →
隐私与安全性 → *仍要打开*（或执行
`xattr -d com.apple.quarantine /Applications/Charoite.app`）。macOS 15+ 上
右键 → 打开已失效。

之后应用会自行更新：有新版本时，「今天」标签页会出现一行带按钮的提示。下载内容
会与发布中的校验和比对，旧副本在替换成功前一直保留；而在录制会议期间更新根本
不会开始 —— 重启会中断录音。映像旁边还有 `Charoite.app.zip`：应用更新用的就是它，
也可以手动解压。

其余步骤都在界面中完成：首次运行向导询问姓名与图谱文件夹，展示匹配本机内存的
模型方案并一键安装；麦克风与系统音频权限由 macOS 自行询问。

实时逐字稿、要点与提示、档案问答与简报、带图谱记忆的本地聊天、听写（⌥⌘D）
和语音笔记（⌥⌘N）。在提供录制按钮前，应用会检查真实会议链路：python 运行
环境、守护进程与配置、依赖、麦克风与音频输入、Ollama 模型以及图谱文件夹。
缺少 `bge-m3` 或可选图谱会明确显示为功能限制，而不会伪装成原因不明的“故障”。

**方式 B — 从源码运行**（开发、自定义构建）：

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install .
cp config/config.example.zh.yaml config/config.yaml   # 填入 user_name 和 graph_dir
.venv/bin/python src/main.py     # 终端里的实时逐字稿 + 提示
```

自行构建带内置运行环境的应用包：
`scripts/build_embedded_python.sh && app/make_app.sh`。

哪里不工作？一条命令告诉你缺什么、怎么修：

```bash
python3 scripts/doctor.py
```


**还没有会议？** 把 `graph_dir` 指向内置[英文演示图谱](../../demo)（demo/graph_en），问一句 "what did we decide about the payment provider?" ——录音之前就能看到产品的样子。一条命令验证整个检索闭环：`.venv/bin/python scripts/memory_bench.py --demo`。已有旧录音？一条命令把会议文件（音频/文本/Zoom字幕）导入档案和图谱：`.venv/bin/python scripts/import_meeting.py 文件 --date 2026-07-15`。或在应用里指定导入文件夹（设置 → 导入）——放进去的录音自动成为会议。替换词典（`sufler.vocabulary`）可修正 STT 总写错的术语——一处声明，处处生效。

STT 模型首次运行自动下载。实时说话人分离（按声音区分的「Собеседник 1/2/…」）只需一条命令：

```bash
.venv/bin/python scripts/get_models.py --diar    # 可选模型：--list
```

没有它 Charoite 也能工作，只是标签按声道区分（你 vs. 对方），并且守护进程会在会议开始时说明这一点。详见 [docs/DIARIZATION.zh.md](DIARIZATION.md)。

## iPhone 伴侣应用（app-ios/）

手机是桌上的麦克风，大脑仍在 Mac。SwiftUI 伴侣应用
（[app-ios/](../../app-ios)）录制会议、语音笔记和日记条目（后台安全，并在
灵动岛显示 Live Activity 计时器与「停止」按钮），通过设备本地的发件队列把文件放入你选定的
iCloud Drive 文件夹——同时把图谱读回来：会议动态与任务复选框，直接来自
Obsidian 和 Mac 应用所看到的同一批 markdown 文件。使用 XcodeGen 构建：
`cd app-ios && xcodegen generate`，然后打开 `CharoiteiOS.xcodeproj`。

录音途中来电是暂停而非丢失：麦克风暂时交给通话，通话结束后继续写入同一个文件。
若系统没有通知通话结束（iOS 并不保证），应用会自行检查输入——第一分钟之后每半分钟
一次——并在麦克风空闲时立即恢复录音。

## Android 伴侣应用（app-android/）

平板或 Android 手机上的同一角色（[app-android/](../../app-android)）：通过
前台服务后台录音、设备本地队列、来自同一批 markdown 文件的会议动态与任务
复选框。录音以 16 kHz 单声道 WAV 写入——正是识别所需要的，也是一种能挺过
崩溃的格式——并落入你只需选择一次的文件夹；Mac 通过 Syncthing 或任意同步
工具看到该文件夹。该应用完全不持有任何网络权限。构建：
`cd app-android && ./gradlew assembleDebug`。

## 文档

- [路线图](../../ROADMAP.md) · [参与贡献](../../CONTRIBUTING.md)

- [宣言](MANIFESTO.md) — 为什么流水线交给本地模型、图谱交给强模型
- [安装](SETUP.md) — 依赖、系统音频权限、首次运行
- [用户实用指南](USER_GUIDE.md) — 从就绪检查到结果卡片与重试的完整流程
- [数据与恢复](DATA_AND_RECOVERY.md) — 存储位置、保留期、备份与安全恢复
- [功能](FEATURES.md) — Charoite 在会议中和会后能做的一切
- [架构](ARCHITECTURE.md) — 守护进程、两遍说话人分离、图谱流水线
- [模型](MODELS.md) — 为什么是这些默认值，附基准测试；**macOS（4/8/16/32 GB）与 iOS 的内存预设**
- [说话人分离](DIARIZATION.md) — 声纹模型的安装与调优
- [设计](DESIGN.md) — macOS 与 iOS 共用的设计令牌和界面约定

## 隐私

见 [PRIVACY.zh.md](PRIVACY.md)。简而言之：无遥测，除本机 localhost 服务（Ollama）外无任何网络调用——代码开源，可自行验证。录音在 `record_keep_days` 天后自动删除。声纹向量只存在于会议进行时的内存中——不保存任何声纹。

## 状态

公开测试版——当前版本见[发布页](https://github.com/charoiteai/Charoite_audio/releases/latest)。欢迎 Issues 和反馈；提问请到 [Discussions](https://github.com/charoiteai/Charoite_audio/discussions)。原生 macOS 应用在 [app/](../../app)，构建命令 `app/make_app.sh`；iPhone 伴侣应用在 [app-ios/](../../app-ios)，安卓伴侣应用在 [app-android/](../../app-android)。macOS 界面、会议文档、图谱内容和提示已支持中文；非俄语摘要卡片的结构解析和 iOS 本地化仍在路线图上。路线图见 [ROADMAP.zh.md](ROADMAP.md)。

如果 Charoite 对您有用，一颗 ⭐ 能帮助更多人找到它。

## 许可证

Apache-2.0。
