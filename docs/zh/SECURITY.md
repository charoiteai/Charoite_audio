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

- **默认情况下，什么都不会。**STT、说话人分离、LLM 和向量嵌入都跑在
  localhost；`src/privacy.py` 是所有网络出口的唯一裁决者，只有配置里显式的
  `true` 才算同意。
- **云端层按能力逐项开启** — 实时回答、提示润色、会后复查和图谱修改各有
  自己的开关；图谱修改还会先备份文件并限定模型可触碰的边界。
  `CHAROITE_NO_CLOUD=1` 是总闸，在任何路径上覆盖任何配置。
- 订阅 CLI 启动时会从环境中清除 `ANTHROPIC_API_KEY`。开关明细与承诺：
  [PRIVACY.md](../../PRIVACY.md)。

## Prompt injection

会议逐字稿、核心和档案都是他人的话，因此应用启动的每个 headless
`claude -p` 都被隔离。「纯文本」调用带上完整的工具禁用清单，外加
`--setting-sources ""` 和 `--strict-mcp-config`（`cloud.text_only_args()`）：
被注入的指令无法读文件、执行命令或访问网络 — 即使机主自己的
`~/.claude/settings.json` 允许了这些工具。唯一合法接触文件的调用
（会后云端复查）从显式的 privacy 开关获得权限，并采用同样的设置隔离。
`tests/test_cloud_isolation.py` 会扫描源码，任何新的未隔离调用都会挂测试。

## 录音是 fail-closed 的

正在进行的录音是本机最宝贵的资产，所以围绕它的操作都朝安全一侧失败：
内置更新器在替换 bundle 之前会再次检查是否有正在进行的录音，替换 helper
在应用进程仍然存活时拒绝动安装目录；录音文件以独占方式打开（`"xb"`），
文件名冲突是可见的错误而不是静默覆盖；停止时音频通过原子重命名交接。
机制详见 [ARCHITECTURE.md](ARCHITECTURE.md) 的「Surviving a crash」。

## 供应链与发布完整性

- 所有 workflow 以最小权限 `permissions:` 和 `persist-credentials: false`
  运行；用户可控输入通过环境变量而非字符串插值进入脚本。CI 里 zizmor
  把关 workflow 安全，dependency review 拦截高危通告，CodeQL 随每次
  push 运行。
- Actions 按成文政策（`.github/zizmor.yml`）固定到版本标签；dependabot
  每周更新 actions、swift、gradle 和 pip。
- 发布严格从发布标签构建。内嵌的 CPython 固定版本并对照上游发布的
  `SHA256SUMS` 校验 sha256；`Charoite.app.zip` 和 `Charoite.dmg` 附带公开的
  sha256，内置更新器在安装前先核对校验和。
- **已知限制：**目前构建为 ad-hoc 签名（首次安装需右键 → 打开；README
  已说明）。Developer ID 签名与公证在路线图上 — 阻碍是内嵌 python 守护
  进程麦克风权限所需的 hardened runtime entitlements。

## 本仓库的匿名化

产品在真实会议上开发，因此有三道闸门阻止私人数据进入公开仓库：
fail-closed 的本地 pre-commit 检查（标记清单本身放在仓库之外）、提交作者
校验，以及按格式匹配的 CI 关卡 — 后者对来自 fork 的 PR 同样生效。
