# 安全策略

*[English](../../SECURITY.md) · [Русский](../ru/SECURITY.md) · **中文***

Charoite 完全运行在用户本机上，因此大多数经典 Web 攻击面并不适用 — 但音频
处理、文件路径或本地 HTTP 调用里的缺陷仍然事关安全。

**云端调用会防范提示注入。** 逐字稿、内核和档案都是他人的话，因此应用启动的每个 headless `claude -p` 都被隔离。纯文本调用使用空的 built-in 工具集，拒绝全部 MCP 工具，也不加载用户与项目设置。唯一可接触文件的调用——会后复盘——只看到显式工具集；`Read(/**)` 与可选的 `Edit(/**)` 锚定在它的工作目录——图谱，或在编辑模式下图谱的沙箱副本——`dontAsk` 会拒绝其之外的路径而不是询问。Shell、网络和 MCP 工具始终不可用；`tests/test_cloud_isolation.py` 与 `tests/test_cloud_enrich_permissions.py` 会在授权过宽或未隔离时失败。

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

- **默认只有一个请求：版本检查。** 向 api.github.com 发一个公开 GET 查询最新版本号，应用运行期间最多每四小时一次（以及按下检查按钮时）—— 不带令牌，不带任何关于您或您会议的数据；`sufler.check_updates: false` 可关闭，`CHAROITE_NO_CLOUD` 总闸同样覆盖它。其余一切都跑在 localhost：STT、说话人分离、LLM 和向量嵌入。`src/privacy.py` 是所有可能携带会议数据的出口的唯一裁决者，只有配置里显式的 `true` 才算同意。
- **云端层按能力逐项开启，且开关相互嵌套。** `cloud_live` 打开会议中的实时回答（`cloud_hints` 只能在它之上生效）。`cloud_enrich` 打开会后复查 — 夜间的图谱审阅也走同一个开关：核心审阅与档案审阅会在夜里把从图谱汇集的文本发送到 Anthropic。`cloud_edit_graph`（在 `cloud_enrich` 之上）是唯一授予写权限的开关；云端修改的是图谱的副本，只有通过边界检查的内容才会搬进真正的图谱。`cloud_engine` 与 `llm.engine: cloud` 一起把整个对话发送到 OpenAI 兼容网关 —— 仅限 https，密钥放在单独文件里，绝不写入配置。`CHAROITE_NO_CLOUD=1` 是总闸，在任何路径上覆盖任何配置。完整开关表：[PRIVACY](PRIVACY.md)。
- **LLM 地址是一项隐私决定。** `llm.base_url` 与 `llm.mlx_base_url` 只有在回环地址上才自由放行；另一台机器需要显式的 `llm.allow_remote: true`，明文 http 只在你自己的网络内被接受（私有或链路本地地址、解析到它们的 `.local` 之类的名字），更远的地址必须 https，而总闸会拒绝任何非回环地址。应用自己的「Ollama」字段遵循该规则中关于回环 / `allow_remote` / 总闸的部分；http 与 https 的检查由守护进程完成（`src/privacy.py`）。拒绝（包括语法无法解析的地址）会出现在就绪检查和守护进程的错误状态中（`privacy_refused`，退出码 11）。若就绪检查未能核对地址（例如代码根比应用更旧），则没有这一行——地址由守护进程在启动时检查。
- **有意的下载。** 更新只在按下按钮时下载（来自 GitHub 发布，按下文所述校验）；模型在首次使用时或通过安装按钮获取 —— 各自访问哪里见 [PRIVACY](PRIVACY.md)。
- 订阅 CLI 启动时会从环境中清除 `ANTHROPIC_API_KEY`。

## Prompt injection

会议逐字稿、核心和档案都是他人的话，因此应用启动的每个 headless
`claude -p` 都被隔离。「纯文本」调用从 `cloud.text_only_args()` 获得同一组参数：`--tools ""`（可见的 built-in 工具集为空）、覆盖全部文件、命令与网络工具外加 `mcp__*` 的禁用清单作为第二层、`--permission-mode dontAsk`（未授予的一律拒绝，而不是等待一个无人能回答的确认），以及 `--setting-sources ""` 和 `--strict-mcp-config` —— 后者尤为重要：没有它，机主自己 `~/.claude/settings.json` 里的允许清单、钩子和 MCP 服务器会作用到这些调用上。唯一合法接触文件的调用（会后云端复查）从显式的 privacy 开关获得权限并采用同样的设置隔离：它只看到被授予的工具，没有 `cloud_edit_graph` 时只能读，有了它 CLI 在图谱的沙箱副本中工作 —— 除通过搬运检查的内容外，没有任何东西进入真正的图谱。`tests/test_cloud_isolation.py` 扫描 `src/` 与 `scripts/` 中的标识符式调用 — 这是针对常见情形的保险，不是证明。

## 录音是 fail-closed 的

正在进行的录音是本机最宝贵的资产，所以围绕它的操作都朝安全一侧失败：
内置更新器在替换 bundle 之前会再次检查是否有正在进行的录音，替换 helper
在应用进程仍然存活时拒绝动安装目录；桌面守护进程的录音文件以独占方式
打开（`"xb"`），文件名冲突是可见的错误而不是静默覆盖；守护进程只运行一个实例（`logs/daemon.lock` 上的独占锁 —— 第二个会拒绝启动，而不是写进同一份逐字稿）；停止时音频通过
原子重命名交接。iOS 与 Android 伴侣应用使用平台自带的录音器，暂不提供
独占打开的保证。机制详见 [ARCHITECTURE](ARCHITECTURE.md) 的
「会议如何挺过崩溃」。

## 已签名应用在本机信任什么

macOS 应用持有用户授予的麦克风与屏幕录制权限，Python 守护进程作为子进程
继承它们。因此运行*哪一份*守护进程代码、读取*哪一个*数据文件夹，是权限边界，
而非路径偏好。

- **代码**只要包内存在就从已签名的应用包运行。仅当用户在设置中选择了本地
  目录*并且*开启「从此文件夹运行守护进程代码（开发）」时才使用本地检出。
  在 16.08 审计之前，任何没有 TCC 权限的进程都可以放置
  `~/Charoite_audio/src/daemon.py`，并让它以应用的权限执行。
- **数据**（`config/config.yaml`、`models/`、逐字稿）在代码内置时来自
  Application Support，否则来自设置中选择的文件夹。主目录中的克隆不再被
  自动采用：`config.yaml` 含有 `sufler.post_meeting_hook`——每次会议后执行的
  shell 命令，因此静默采用一个可写文件夹与运行未签名代码是同一扇门
  （#328 的第二意见评审）。数据位于 `~/Charoite_audio` 的用户会被明确询问
  一次并记住答案；无论如何守护进程代码仍来自应用包。
  从旧的 bundle 标识符迁移过来的设置只把所选文件夹保留为*数据*：
  从该文件夹运行守护进程代码需要再次明确开启（DeepSeek 审计，16.08）。
- **数据文件夹本身就是信任边界，无论它在哪里。** 其中的 `config.yaml`
  由持有应用权限的进程读取，而 `sufler.post_meeting_hook` 按设计就是一条
  shell 命令。任何能写入该文件夹的东西（包括 Application Support——它不受
  TCC 保护）都可以借麦克风与屏幕录制授权执行命令。Charoite 将其私有子文件夹——`config/`、`transcripts/`、`recordings/`、`logs/`、`data/`、`backups/`——保持为 `0700`、文件为 `0600`（每次守护进程启动时 `harden_existing`），也从不静默采用文件夹；但它无法
  防御以你的身份运行、并能写入你自己文件的另一个进程。不用钩子就把它留空。
- **不顺着符号链接进入流水线。** 导入文件夹中的符号链接会被跳过（链接会把别人的文件拉进图谱和 LLM 流水线），其 `done/` 必须是真实目录；云端复盘向 CLI 屏蔽图谱中的每一个符号链接——在任何模式下连读取也屏蔽，因为目标位于图谱之外——并在构建命令之前用 `O_NOFOLLOW` 一次性检查沙箱；清单签名脚本拒绝 `Charoite.app` 是链接的压缩包。
- **`charoite://record/start` 是正门，不是后门。** 这个 URL scheme 是为
  Shortcuts 和终端准备的，任何本机进程或网页都能打开它。它做不到的是悄悄
  录音：每次调用都会把窗口连同状态和计时器置于最前，浏览器在打开 scheme
  前会先询问，`stop` 只需一次点击。确认对话框会破坏这个 scheme 存在的意义
  ——免手动启动——所以防线是可见性，而不是弹窗（16.08 审计，第 6 项）。

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
- Dependabot 每周更新 actions、swift、gradle 和 pip；CI 闸门工具本身的固定版本（ruff、semgrep、mypy、pytest、zizmor）由人手动升级，并配一次验证它们的运行。
- 发布严格从发布标签构建。内嵌的 CPython 固定版本并对照上游发布的
  `SHA256SUMS` 校验 sha256，且构建不会从 PyPI 升级自身的 pip——应用包只从带
  哈希的锁定文件安装（16.08 之前，锁定安装前曾运行未固定版本的
  `pip install --upgrade pip`）；`Charoite.app.zip` 和 `Charoite.dmg` 附带公开的
  sha256，内置更新器在安装前先核对校验和。
- **两个不在 GitHub 上的更新锚点。** 压缩包旁的校验和只能证明下载完整：能替换压缩包的人（泄漏的 CI 令牌、被攻破的账号）也能替换校验和。因此在替换应用包之前，更新器还要求 (1) 用所有者 ed25519 密钥签名的清单 `<版本>  <sha256>` —— 私钥只在所有者的机器上，既不在仓库也不在 GitHub Secrets；签名的版本必须与标签一致并且比已安装的更新，因此旧的诚实发布无法挂在新标签下重放 —— 以及 (2) 下载的应用包上带有本团队 Developer ID 的 Apple 签名。在所有者签名之前，发布保持 pre-release（更新器看不到它）。机制见 [RELEASING](RELEASING.md)「更新清单签名」。
- **签名与公证：** 发布版本以 Developer ID 证书签名，包内每个可执行文件都
  启用 hardened runtime（内嵌 python 解释器带有自己的 entitlements —— 音频输入、
  未签名可执行内存、关闭库校验 —— 守护进程因此仍能听到麦克风），带时间戳，经
  `notarytool` 公证并装订；DMG 单独签名并公证。每个嵌套 Mach-O 逐一签名 ——
  任一签名失败都会让构建失败，而不是把会被 Gatekeeper 拒绝的包发给用户。
  没有签名密钥时流水线以 ad-hoc 构建并在日志中说明；这样的构建首次启动需要
  *仍要打开*（见 README）。应用从不写入自身的包：内嵌 python 的字节码放在
  `~/Library/Caches/ai.charoite.app/pycache`（启动时设置 `PYTHONPYCACHEPREFIX`，
  所有子进程继承）—— 在已公证的包内写入 `__pycache__` 会破坏资源封印，下次启动
  时 Gatekeeper 会报告应用已损坏（0.52.0 发现，0.52.1 修复）。
  详情：[RELEASING](RELEASING.md)「签名与公证」。

## 本仓库的匿名化

产品在真实会议上开发，因此有三道闸门阻止私人数据进入公开仓库：
fail-closed 的本地 pre-commit 检查（标记清单本身放在仓库之外）、提交作者
校验，以及按格式匹配的 CI 关卡 — 后者对来自 fork 的 PR 同样生效。
