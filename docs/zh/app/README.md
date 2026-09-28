# Charoite.app — macOS 配套应用

*[English](../../../app/README.md) · [Русский](../../ru/app/README.md) · [**中文**]*

Charoite Python 守护进程之上的原生 SwiftUI 外壳：带说话人分离的实时逐字稿、
会议脉络、提示与 Claude 面板、会前准备与简报、带结果卡片的会议库、外部录音
导入、任务、带图谱记忆的聊天、听写（⌥⌘D）、语音笔记（⌥⌘N）和日记（⌥⌘J）、
菜单栏。默认一切本地运行——和守护进程本身一样；少数可能离开本机的内容列在
文末。

## 构建

```bash
cd app
./make_app.sh          # swift build -c release (arm64) + 打包 + 签名
open build/Charoite.app
```

要求：Apple Silicon 上的 macOS 14+，Xcode Command Line Tools（`xcode-select --install`）。

签名：钥匙串中有 Developer ID Application 证书（或在 `CHAROITE_SIGN_IDENTITY`
中指定；`-` 强制 ad-hoc）时，使用它并启用 hardened runtime 和时间戳，否则为
ad-hoc。ad-hoc 签名下 macOS 会把每次重新构建视为新应用：麦克风与录屏权限需要
重新授予，首次启动需要「仍要打开」。在 hardened runtime 下守护进程不会继承应用
的麦克风权限，因此内嵌解释器用自己的 entitlements 签名
（`Resources/entitlements/embedded-python.entitlements`）。

应用包内带有守护进程代码（`src/`、`scripts/`、`config/config.example.yaml`），
若事先运行过 `scripts/build_embedded_python.sh`，还带有可移植 python 运行环境；
没有它时，应用使用其数据文件夹中的 `.venv`，与克隆安装相同。版本号取自最新的
git 标签，构建号取自提交数。发布构建（DMG、公证、签名的更新清单）见
[发布流程](../RELEASING.md)。

测试：在 `app/` 中运行 `swift test --filter '^CharoiteAppTests\.'`。`Probes/`
里是针对真实图谱和 Ollama 的实时探针；未设置相应环境变量时会跳过，只手动运行。

## 首次设置

1. 打开应用，其余交给首次运行向导：它会安装或启动 Ollama（有 Homebrew 时
   通过 Homebrew，否则打开下载页面），询问你的姓名与图谱文件夹，推荐匹配本机
   内存的模型方案并下载，并提供区分说话人的模型（约 80 MB）。Python 运行环境
   和守护进程的代码都在应用包内——不需要 `git clone`、venv 或 `pip`。
2. 需要改动时再进设置（⌘,）：
   - **数据文件夹** — `config/config.yaml`、逐字稿、录音和模型所在位置；默认
     `~/Library/Application Support/Charoite`。若所选文件夹是含
     `src/daemon.py` 的克隆，单独的开关「从此文件夹运行守护进程代码（开发）」
     会用这份代码代替已签名应用包中的代码；
   - **Ollama** — 服务器地址（默认 `http://localhost:11434`）；不指向本机的
     地址会被拒绝并显示原因，除非配置中设置了 `llm.allow_remote: true`；
   - 「检查」按钮会验证守护进程、Ollama、bge-m3 和图谱是否就绪；
   - 夜间流程（04:15 的 launchd 任务）、导入文件夹、日历和「离开本机的内容」
     也在这里。
3. 图谱路径写在数据文件夹内的 `config/config.yaml`（`sufler.graph_dir`）里——
   由向导写入，守护进程读取。相对路径从数据文件夹算起。

首次「聆听会议」时 macOS 会请求麦克风权限。通话另一方的声音通过
ScreenCaptureKit 采集：授予「录屏与系统录音」权限并重启应用——否则只录到你的
麦克风。若需听写自动插入当前输入框，请授予应用辅助功能（Accessibility）权限。
日历权限可选，只读取事件标题和时间。

第一段录音也是端到端检查：计时器应持续增加，双方声音应出现在逐字稿中，
按下停止后应显示真实处理阶段，最后出现会议结果卡片。完整流程与失败恢复见
[用户实用指南](../USER_GUIDE.md)。

## 代码结构

- `Sources/CharoiteApp/App` — 场景（主窗口、「记忆聊天」窗口、菜单栏、设置）、
  `charoite://` 链接处理和分区之间的跳转。
- `Sources/CharoiteApp/Views/Workspace` — 主窗口：侧边栏、「今天」、会议库、
  「外部录音」。
- `Sources/CharoiteApp/Views/Sufler` — 「会议」分区（逐字稿、脉络、提示、
  Claude 面板）和首次运行向导。
- `Sources/CharoiteApp/Views/Meetings`、`Prep`、`Tasks`、`LocalChat`、
  `MenuBar`、`Settings`、`Dictation` — 会议卡片、会前准备、任务、「记忆」、
  菜单栏、设置和听写预览。
- `Sources/CharoiteApp/Services` — 守护进程桥接（NDJSON stdin/stdout、
  watchdog、自动重启）、系统音频采集、录音生命周期与处理状态、听写、本地图谱
  搜索与语义索引、导入、日历、更新。
- `Sources/CharoiteApp/L10n.swift` — 俄、英、中三种语言的界面文字。

## 主窗口与菜单栏

一个带侧边栏的窗口；菜单栏、会议卡片和会前准备切换的是分区，而不是各开窗口。

- **今天** — 带就绪状态的录音按钮、当前会议的状态（录音中、处理中、结果就绪）、
  为下一个日历事件做的准备（该主题以往的会议、未完成任务、该主题的上次会议）、
  最近的结果、昨夜处理是否完成以及正在运行的版本、更新提示。
- **会议** — 逐字稿、持续增长的会议脉络、提示与对方提问的即时回答、按需的
  「摘要」（⌘⏎）和「纪要」、针对本场会议与图谱的提问、可选的 Claude 云层
  （标注「离开本机」）、录音计时器和停止后的真实处理阶段；记忆聊天以侧面板打开。
- **会议记录** — 会议库：按天排列的卡片，含状态、时长、参会者和任务，一周日期条，
  在摘要、纪要、分析和图谱笔记中搜索。旁边的会议卡片显示主题、日期、时长、
  参会者、概要、决策和任务，分四种深度（摘要 · 纪要 · 分析 · 逐字稿），可复制、
  打开逐字稿或 Obsidian、协同重命名会议文件、重试失败的处理。列表是状态历史，
  最近 14 天最多 20 条运行记录，不替代完整图谱归档。
- **外部录音** — 拖入手机录音、别人的通话录音或 Zoom 导出：文件被复制到导入
  文件夹（原件仍归你），队列显示哪些在等待、哪些失败（重试、逐字稿、访达）以及
  处理后的副本何时删除。
- **任务** — 纪要和图谱笔记中的所有任务汇于一列；勾选直接写回 markdown。
  「我的」排在最前，旧任务折叠，可按会议或按期限分组，并显示未完成数角标。
- **记忆** — 与本地模型聊天（Ollama 模型列表实时获取）；「图谱记忆」开关混入
  图谱检索结果，每个回答都带来源标签和出处行，「记忆掌握的内容」一栏列出核心。
  同一聊天也可作为独立的「记忆聊天」窗口打开；历史共享。

主窗口关闭后菜单栏仍是状态中心：记录的数据有风险时图标变为红色三角，出现
不丢数据的降级时变为黄色圆圈；菜单显示录音计时、处理、就绪和失败状态，并提供
开始/停止、最新结果、重试、近期会议、「今天」、向本地模型快速提问、听写、
语音笔记和日记。会议会被编入 Spotlight 索引，`charoite://record/start`、
`stop`、`toggle`、`charoite://meeting/<id>`、`charoite://tasks` 和
`charoite://today` 可从快捷指令或终端控制应用。存储位置与保留规则见
[《数据与恢复》](../DATA_AND_RECOVERY.md)。

## 档案搜索（v2）

问答与简报的排序毫不含糊：俄语词干化（另有轻量英语词干化和中文二元组）、
IDF（罕见词权重更高）、查询覆盖率、文件新鲜度（文件名中的日期）、图谱精炼
文档优先于原始逐字稿、结果多样性（单场会议不会占满所有位置）。弱匹配会被标注
「⚠ 图谱匹配较弱」——模型不会拿不相关的片段编造答案。回答下方的来源标签
可点击：会议打开其会议卡片，图谱笔记或档案直接打开文件本身。

语义层（通过您的 Ollama 运行 bge-m3）同样作用于内置搜索：索引为每个文件的
每个块保存一个向量，后台构建、按 mtime 刷新（见 docs/SETUP — `ollama pull bge-m3`）。
如果启动了可选的 brain 服务（端口 8100），搜索走它；两者都没有——纯词法搜索。

## 离开本机的内容

默认情况下应用只与 localhost 通信：您的 Ollama、可选的 brain 配套服务
（:8100）和提词守护进程。有两个例外由你控制：最多每四小时一次向 GitHub 公共 API 询问
最新发布版本号（`sufler.check_updates`，设置中的开关）；你接受的更新从 GitHub
发布页下载，只有在校验和、所有者对清单的签名以及 Developer ID 签名都通过后才会
替换应用。设置 `CHAROITE_NO_CLOUD` 时两者都关闭。云端层——会议中的 Claude、
云端聊天引擎——只有你开启才会启用，并在守护进程中运行；见[隐私](../PRIVACY.md)。
