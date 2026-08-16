# 安全策略

*[English](../../SECURITY.md) · [Русский](../ru/SECURITY.md) · **中文***

Charoite 完全运行在用户本机上，因此大多数经典 Web 攻击面并不适用 — 但音频
处理、文件路径或本地 HTTP 调用里的缺陷仍然事关安全。

**请通过私密渠道报告漏洞**：在 GitHub 上 Security → Report a vulnerability
（私密安全通告），或发邮件到 charoiteai@gmail.com。请不要为安全问题开公开
issue。

通常几天内会得到答复。受支持的版本：`main`。

## 一段话威胁模型

资产是用户自己的会议：录音、逐字稿和由它们构建的知识图谱。没有服务端。
剩余的攻击面：**离开**本机的数据（可选的云端层）；**进入**模型的他人话语
（来自逐字稿的 prompt injection）；**更新与依赖**通道；以及可能在本地毁掉
一段录音的缺陷。以下逐一对应各自的防护。

## 什么会离开本机

- **默认只有一个请求：每日版本检查。** 向 api.github.com 发一个公开 GET
  查询最新版本号 — 不带令牌，不带任何关于您或您会议的数据；
  `sufler.check_updates: false` 可关闭，`CHAROITE_NO_CLOUD` 总闸同样覆盖它。
  其余一切都跑在 localhost：STT、说话人分离、LLM 和向量嵌入。
  `src/privacy.py` 是所有可能携带会议数据的出口的唯一裁决者，只有配置里
  显式的 `true` 才算同意。
- **云端层按能力逐项开启，且开关相互嵌套。** `cloud_live` 打开会议中的
  实时回答（`cloud_hints` 只能在它之上生效）。`cloud_enrich` 打开会后
  复查 — 夜间的图谱审阅也走同一个开关：核心审阅与档案审阅会在夜里把
  从图谱汇集的文本发送到 Anthropic。`cloud_edit_graph`（在 `cloud_enrich`
  之上）是唯一授予写权限的开关；写之前先备份文件、写之后校验边界，
  而当备份无法完成时写权限会被收回。`CHAROITE_NO_CLOUD=1` 是总闸，
  在任何路径上覆盖任何配置。完整开关表：[PRIVACY.zh.md](PRIVACY.md)。
- 订阅 CLI 启动时会从环境中清除 `ANTHROPIC_API_KEY`。

## Prompt injection

会议逐字稿、核心和档案都是他人的话，因此应用启动的每个 headless
`claude -p` 都被隔离。「纯文本」调用带上覆盖当前 CLI 全部文件、命令和
网络工具的禁用清单，外加 `--setting-sources ""` 和 `--strict-mcp-config`
（`cloud.text_only_args()`）— 后者尤为重要：没有它，机主自己
`~/.claude/settings.json` 里的允许清单会作用到这些调用上。诚实的边界：
这是随 CLI 演进而扩充的禁用清单，不是 allowlist 级别的保证。唯一合法
接触文件的调用（会后云端复查）从显式的 privacy 开关获得权限并采用同样
的设置隔离 — 且每当写前备份无法完成时，写权限即被收回。
`tests/test_cloud_isolation.py` 扫描 `src/` 与 `scripts/` 中的标识符
式调用 — 这是针对常见情形的保险，不是证明。

## 录音是 fail-closed 的

正在进行的录音是本机最宝贵的资产，所以围绕它的操作都朝安全一侧失败：
内置更新器在替换 bundle 之前会再次检查是否有正在进行的录音，替换 helper
在应用进程仍然存活时拒绝动安装目录；桌面守护进程的录音文件以独占方式
打开（`"xb"`），文件名冲突是可见的错误而不是静默覆盖；停止时音频通过
原子重命名交接。iOS 与 Android 伴侣应用使用平台自带的录音器，暂不提供
独占打开的保证。机制详见 [ARCHITECTURE.md](ARCHITECTURE.md) 的
「会议如何挺过崩溃」。

## 供应链与发布完整性

- 所有 workflow 以最小权限 `permissions:` 和 `persist-credentials: false`
  运行；用户可控输入通过环境变量而非字符串插值进入脚本。CI 里 zizmor
  把关 workflow 安全，dependency review 拦截高危通告，CodeQL 在推送到
  `main`、PR 和每周定时任务上运行（目前仅覆盖 Python）。
- Actions 按成文政策（`.github/zizmor.yml`）固定到版本标签，但有一个例外：
  两个持有 `contents: write` 的工作流——`release-please`（管理员 PAT）和
  `release-app`（发布资产）——按提交 SHA 固定。在那里劫持可变标签会波及
  用户实际安装的产物，代价与只读任务完全不同。
- 内嵌 Python 从 `requirements-runtime.lock` 以 `--require-hashes` 安装：
  签名包中的内容正是仓库中记录的那些包（含传递依赖），而不是构建当刻
  PyPI 提供的版本。改动依赖后请用 `scripts/lock_runtime_deps.py` 重建 lock。
- 模型权重按 `scripts/get_models.py` 中记录的 sha256 校验。下载地址指向可变
  引用（`resolve/main`、发布资产）：镜像所有者可以在不改 URL 的情况下替换
  文件——而该文件随后会听到你的每一场会议。校验和不符会中止安装并把决定权
  交给人；`--url`（你自己的镜像）会跳过校验，因为我们的摘要不适用于别人的文件。
- Dependabot 每周更新 actions、swift、gradle 和 pip。
- 发布严格从发布标签构建。内嵌的 CPython 固定版本并对照上游发布的
  `SHA256SUMS` 校验 sha256；`Charoite.app.zip` 和 `Charoite.dmg` 附带公开的
  sha256，内置更新器在安装前先核对校验和。
- **已知限制：** 目前构建为 ad-hoc 签名，macOS 会拦截首次启动 —
  系统设置 → 隐私与安全性 → *仍要打开*（macOS 15+ 上「右键 → 打开」
  已失效；详见 README）。Developer ID 签名与公证在路线图上 — 阻碍是
  内嵌 python 守护进程麦克风权限所需的 hardened runtime entitlements。

## 本仓库的匿名化

产品在真实会议上开发，因此有三道闸门阻止私人数据进入公开仓库：
fail-closed 的本地 pre-commit 检查（标记清单本身放在仓库之外）、提交作者
校验，以及按格式匹配的 CI 关卡 — 后者对来自 fork 的 PR 同样生效。
