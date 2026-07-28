# Charoite.app — macOS 配套应用

*[English](README.md) · [Русский](README.ru.md) · [**中文**]*

Charoite Python 守护进程之上的原生 SwiftUI 外壳：带说话人分离的实时逐字稿、
要点卡片、提示与 Claude 面板、档案问答与简报、带图谱记忆的本地聊天、
听写（⌥⌘D）和语音笔记（⌥⌘N）、菜单栏。一切本地运行——和守护进程本身一样。

## 构建

```bash
cd app
./make_app.sh          # swift build -c release + 打包 + ad-hoc 签名
open build/Charoite.app
```

要求：macOS 14+，Xcode Command Line Tools（`xcode-select --install`）。

## 首次设置

1. 先安装 Charoite_audio 本体（仓库根目录）：venv、模型、
   `config/config.yaml` — 见根目录 README。
2. 启动应用 → 设置（⌘,）：
   - **Charoite_audio 文件夹** — 仓库克隆到的位置（默认 `~/Charoite_audio`）；
   - **Ollama** — 服务器地址（默认 `http://localhost:11434`）；
   - 「检查」按钮会验证守护进程、Ollama 和图谱是否就绪。
3. 图谱路径从 `config/config.yaml`（`sufler.graph_dir`）读取——
   为守护进程配置一次，应用自动继承。

首次「聆听会议」时 macOS 会请求麦克风权限；若需听写自动插入当前输入框，
请授予应用辅助功能（Accessibility）权限。

## 代码结构

- `Sources/CharoiteApp/Views/Sufler` — 主窗口：逐字稿、要点/提示/Claude
  面板、档案问答与简报。
- `Sources/CharoiteApp/Views/LocalChat` — 与本地模型聊天；「记忆」开关
  混入图谱检索结果（文件搜索，无服务器）。
- `Sources/CharoiteApp/Services` — 守护进程桥接（NDJSON stdin/stdout、
  watchdog、自动重启）、听写、本地图谱搜索。

## 窗口

- **提词助手** — 逐字稿、要点、提示、档案回答、简报。
- **会议任务** — 图谱所有复选框汇于一列，勾选直接写回 markdown；
  工具栏显示未完成数量。
- **记忆聊天** — 本地模型 + 图谱事实；Ollama 模型列表实时获取；
  气泡内渲染 markdown；一键复制回答。
- 档案回答逐 token 流式输出；本次会话的过往问题折叠在当前回答之下；
  好的回答可一键存为图谱笔记。

## 档案搜索（v2）

问答与简报的排序毫不含糊：俄语词干化、IDF（罕见词权重更高）、查询覆盖率、
文件新鲜度（文件名中的日期）、图谱精炼文档优先于原始逐字稿、结果多样性
（单场会议不会占满所有位置）。弱匹配会被标注「⚠ 档案中可能没有此内容」——
模型不会拿不相关的片段编造答案。回答中的来源可点击，直接在 Obsidian 打开。

语义层（通过您的 Ollama 运行 bge-m3）同样作用于内置搜索：索引后台构建、
按 mtime 刷新（见 docs/SETUP — `ollama pull bge-m3`）。如果启动了可选的
brain 服务（端口 8100），搜索走它；两者都没有——纯词法搜索。

除 localhost 外应用不建立任何网络连接：您的 Ollama、可选的 brain
配套服务（:8100）和提词守护进程。
