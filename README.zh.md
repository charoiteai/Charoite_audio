# Charoite（紫龙晶）

[![CI](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/ci.yml) [![swift-tests](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/swift-tests.yml) [![CodeQL](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml/badge.svg)](https://github.com/charoiteai/Charoite_audio/actions/workflows/codeql.yml) ![License](https://img.shields.io/badge/license-Apache--2.0-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon-lightgrey) ![Local](https://img.shields.io/badge/cloud-none%20by%20default-success) [![Release](https://img.shields.io/github/v/release/charoiteai/Charoite_audio)](https://github.com/charoiteai/Charoite_audio/releases)

**完全本地运行的 AI 会议助手：说话人分离 + 自更新知识图谱。任何数据都不会离开您的 Mac。**

*[English](README.md) · [Русский](README.ru.md)*

![Charoite 应用 — 实时逐字稿、要点、档案问答](docs/img/app-main.png)

![会议任务 — 图谱中所有复选框汇于一窗](docs/img/app-tasks.png)

Charoite 倾听您的会议（麦克风 + 系统声音，无需机器人入会），本地转写、区分说话人、会议进行中即时回答问题，会后自动构建 Obsidian 知识图谱：人物、系统、决定和贯穿性主题在所有会议之间被持续记住。

## 为什么选 Charoite

- **默认 100% 本地。** 音频、语音识别、说话人分离、LLM 摘要——全部在您的机器上（Ollama + ONNX）。无云端、无遥测、无账号。可选的 Claude 云层默认关闭。
- **真正可用的说话人分离。** 会议中实时显示「说话人 1/2/…」标签，会后对完整录音离线重建——每位说话人的段落干净利落。有人自我介绍时姓名自动关联——绝不靠猜。
- **知识图谱，而不是笔记堆。** 会议成为episode；人物、系统、决定成为节点；反复出现的主题成为带状态和历史的「核心」。会议中 Charoite 会低声提示：「⏮ 7月15日讨论过，当时的状态是……」。
- **主题档案。** 夜间由本地模型为每个贯穿性主题生成综述：当前状态、时间线、已定事项、未决问题、相关人员——每一条都链接到来源节点。检索**优先**查询档案索引，因此「这个主题现在怎么样」由一份写好的综述回答，而不是十几个零散片段。重建是增量的：档案带有其来源的指纹，未变动的主题不会惊动模型。手工补充写在「作者修改」小节，重建时保留。
- **云端模型——可选，每一步都可选。** 默认全部关闭，逐项启用：会后复盘（`cloud_enrich`）、对话节奏中的第二意见（`cloud_live`）、带修改权限的档案复核（`cloud_edit_graph`）。基于订阅运行，环境中不放 API 密钥。哪些内容会上云、哪些永不上云——见 [PRIVACY.zh.md](PRIVACY.zh.md)。
- **每场会议的分层文档**：一分钟摘要（含与过往会议的关联）→ 会议纪要 → 复盘 → 完整逐字稿。想读多深读多深。
- **实时辅助**：对方向您提问时的即时本地回答（⚡）、自动要点、实时纪要草稿、语音笔记和听写。
- **中文原生的 LLM。** 默认主模型是 Qwen（阿里巴巴出品）——中文就是它的母语；语音识别用 Whisper（`stt.backend: whisper`，`language: zh`）。

## 系统要求

- Apple Silicon Mac（M1 及以上），默认模型建议 32 GB 内存
- [Ollama](https://ollama.com) — 哪些模型放得下，见下方内存表
- Python 3.11+
- 可选：[BlackHole](https://existential.audio/blackhole/) 捕获系统声音（线上会议），[Obsidian](https://obsidian.md) 浏览图谱

## 按内存选模型

一切本地运行。STT（约1 GB）和说话人分离（约0.5 GB）恒定；随内存扩展的是 LLM。全程 `num_ctx: 8192`。语义搜索需另加 `bge-m3`（约1.2 GB）——16 GB 以上推荐开启。

| 内存 | 主 LLM | 轻量 LLM | 图谱 | 能得到什么 |
|----|----|----|----|----|
| **8 GB** | `qwen3.5:4b` | 同左 | 无 | 逐字稿、要点、纪要、基础提示 |
| **16 GB** | `gemma4:latest` | `qwen3.5:2b` | 慢 | 完整实时闭环——推荐入门配置 |
| **32 GB** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | 有 | 默认配置，基准测试即在此配置 |
| **64 GB+** | `qwen3.6:35b-a3b` | `qwen3.5:4b` | 有 | 云端 Claude 层 + 超长会议的余量 |

16 GB 以下知识图谱关闭（30B 以下的模型会破坏 JSON 结构）；4 GB 只跑 STT，`llm.base_url` 指向另一台机器即可。

完整的 macOS/iOS 模型表和选型依据：[docs/MODELS.zh.md](docs/MODELS.zh.md)。

## 快速开始

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config/config.example.zh.yaml config/config.yaml   # 填入 user_name 和 graph_dir
```

哪里不工作？一条命令告诉你缺什么、怎么修：

```bash
python3 scripts/doctor.py
```

**方式 A — macOS 应用（推荐）：**

从[最新发布](https://github.com/charoiteai/Charoite_audio/releases/latest)下载 `Charoite.app.zip`（ad-hoc 签名，未经公证 — 首次启动会被 macOS 拦截：
执行 `xattr -d com.apple.quarantine /Applications/Charoite.app`，或前往
系统设置 → 隐私与安全性，点击 *仍要打开*。macOS 15+ 上右键 → 打开已失效），或自行构建：

```bash
./app/make_app.sh && open app/build/Charoite.app
```

实时逐字稿、要点与提示、档案问答与简报、带图谱记忆的本地聊天、听写（⌥⌘D）和语音笔记（⌥⌘N）。

**方式 B — 命令行：**

```bash
.venv/bin/python src/main.py     # 终端里的实时逐字稿 + 提示
```

**还没有会议？** 把 `graph_dir` 指向内置[英文演示图谱](demo/)（demo/graph_en），问一句 "what did we decide about the payment provider?" ——录音之前就能看到产品的样子。一条命令验证整个检索闭环：`python3 scripts/memory_bench.py --demo`。已有旧录音？一条命令把会议文件（音频/文本/Zoom字幕）导入档案和图谱：`python3 scripts/import_meeting.py 文件 --date 2026-07-15`。或在应用里指定导入文件夹（设置 → 导入）——放进去的录音自动成为会议。替换词典（`sufler.vocabulary`）可修正 STT 总写错的术语——一处声明，处处生效。

STT 模型首次运行自动下载。实时说话人分离需将 ERes2Net 声纹模型放到 `models/diar/embedding.onnx`（见 docs/DIARIZATION.md）。

## 文档

- [路线图](ROADMAP.md) · [参与贡献](CONTRIBUTING.md)

- [安装](docs/SETUP.zh.md) — 依赖、线上会议用 BlackHole、权限、首次运行
- [功能](docs/FEATURES.zh.md) — Charoite 在会议中和会后能做的一切
- [架构](docs/ARCHITECTURE.zh.md) — 守护进程、两遍说话人分离、图谱流水线
- [模型](docs/MODELS.zh.md) — 为什么是这些默认值，附基准测试；**macOS（4/8/16/32 GB）与 iOS 的内存预设**
- [说话人分离](docs/DIARIZATION.md) — 声纹模型的安装与调优（英文）

## 隐私

见 [PRIVACY.zh.md](PRIVACY.zh.md)。简而言之：无遥测，除本机 localhost 服务（Ollama）外无任何网络调用——代码开源，可自行验证。录音在 `record_keep_days` 天后自动删除。声纹向量只存在于会议进行时的内存中——不保存任何声纹。

## 状态

公开测试版，当前版本 0.38.0。欢迎 Issues 和反馈。原生 macOS 应用在 [app/](app/)，构建命令 `app/make_app.sh`；iPhone 伴侣应用在 [app-ios/](app-ios/)。界面语言目前为俄语（界面本地化在路线图上）；会议文档、图谱内容和提示已支持中文（`language: zh`）。路线图见 [ROADMAP.zh.md](ROADMAP.zh.md)。

## 许可证

Apache-2.0。
