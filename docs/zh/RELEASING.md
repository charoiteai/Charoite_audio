# 发布流程

*[English](../RELEASING.md) · [Русский](../ru/RELEASING.md) · **中文***

版本号与 `CHANGELOG.md` 由 [release-please](https://github.com/googleapis/release-please) 自动维护。changelog 永远不用手工编辑 — 只需写 `fix:`/`feat:` 约定式提交，其余的在合并进 `main` 时自动完成。

## 工作原理

1. 每次向 `main` 推送都会运行 `release-please` 工作流。
2. 它把上次发布以来的 `fix:`/`feat:` 提交收集成一个标题为 `chore(main): release X.Y.Z` 的**发布 PR**，同时更新 `CHANGELOG.md`、`.github/.release-please-manifest.json` 以及 `app-ios/project.yml` 中 iPhone 伴侣应用的 `MARKETING_VERSION`（`.github/release-please-config.json` 里的 `extra-files` 条目）。
3. 合并该 PR 会给提交打标签（`vX.Y.Z`）并创建 GitHub Release — 在所有者签名之前它是 pre-release（见下文门禁）。

当前版本记录在 `.github/.release-please-manifest.json` 里 — 而不是仓库根目录的某个 `version.txt`。Git 标签是权威来源。

文档守卫按 diff 的内容、而不是按分支名放行发布 PR：只包含 `CHANGELOG.md`、manifest 和 `app-ios/project.yml` 的 diff 无需文档。新增一个 `extra-files` 条目会让守卫变红，直到有人有意把它登记进去。

## Squash 合并：PR 标题就是那条提交

使用 squash 合并时，`main` 恰好收到一条提交，其主题就是 **PR 标题**。如果该标题不是约定式提交（`fix: …`、`feat: …`），release-please 就完全看不到这些工作：没有发布 PR、没有 CHANGELOG 条目、没有版本号提升。这个坑我们一天内踩了四次（#83–#86 全部以 `Fix/<branch name>` 的形式合并），导致一整天已交付的修复在 changelog 里不可见。

规则：

1. 确认 squash 之前，在合并对话框里把标题改成约定式格式。
2. 一个 PR 含多个用户可见变更时：在 squash 的**正文**里额外添加普通的 `fix(scope): …` 行（不要 `* ` 项目符号 — 带项目符号的行不会被解析）— release-please 会把每一行登记为独立条目。
3. 已经用错误标题合并了：推送一条“载体提交”，其提交信息里带上漏掉的约定式行（可以是维护者本机的空提交，也可以是通过 PR 的小型真实改动）。

## 一次性配置：RELEASE_PLEASE_TOKEN

发布 PR 必须由**个人访问令牌**（personal access token）创建，而不是内置的 `GITHUB_TOKEN`。GitHub 有意不对内置令牌创建的分支运行 CI（防循环保护），因此发布 PR 会缺少必需检查、一直停在 `BLOCKED` 状态。PAT 让分支被视为“人类”创建，必需的 `lint` 和 `pytest (src/)` 就能正常运行。

配置方式（仓库所有者，一次性）：

1. 创建一个仅限本仓库的 **fine-grained PAT**，权限：
   - **Contents: Read and write**（打标签 + changelog 提交）
   - **Pull requests: Read and write**（打开发布 PR）
2. 把它添加为名为 **`RELEASE_PLEASE_TOKEN`** 的仓库 secret（Settings → Secrets and variables → Actions → New repository secret）。

secret 缺失时工作流会回退到 `GITHUB_TOKEN`，所以在此期间什么都不会坏 — 只是在 PAT 就位之前，发布 PR 需要手动点一次 “Approve and run”。

## 每次发布都附带应用包

`release-app` 构建 `Charoite.dmg`（首次安装用的安装器）、`Charoite.app.zip`（已安装应用据此更新）以及两者的 `.sha256` —— 没有已发布的校验和，应用内更新会拒绝安装下载到的文件。构建在 `macos-26` runner 上进行（与 `swift-tests` 相同的 SDK：实时听写草稿需要 SDK 26），完成后全部附加到发布上。job 上限为 90 分钟：公证要等 Apple 两次，每次最多 35 分钟（`scripts/notarize.sh`），状况不好的日子里应当带着 Apple 的日志失败，而不是被 runner 的时限掐断。共三个触发器：

- `release-please` 工作流之后的 `workflow_run` — 主路径。release-please 用 `GITHUB_TOKEN` 发布 release，而 GitHub 的防递归机制意味着这类事件不会在其他工作流里触发 `release: published`（v0.19.0 最初发布时就没带应用包 — 我们由此学到教训）。该 job 只在 release-please **成功**时才继续（失败的运行同样会发出 `completed`）。
- `release: published` — 保留给人工创建的发布。配置了 `RELEASE_PLEASE_TOKEN`（PAT）后，release-please 自己创建的发布也会触发它，因此一次发布会有两次运行：`concurrency` 把它们串行化，第二次看到 `Charoite.dmg` 就以 `build=false` 退出。同时也监听 `released`——pre-release 变为稳定版（由签名脚本或在网页里手动操作）时，会再次经过下文的签名门禁；每次签名之后都会多一次短暂的空跑。
- 带 `tag` 输入的 `workflow_dispatch` — 给旧发布手动重新上传（见下文 v0.19.0 事后复盘）。手动运行总是重新构建并用 `--clobber` 覆盖资产；自 PR #375 起，重新构建的发布在所有者签署新压缩包之前会变为 pre-release —— 包括从未签过名的历史发布（它们将永久保持 pre-release；不影响任何人，它们不是 latest）。

job 内部的顺序是刻意安排的：**第一步**先解析哪个标签需要资产、以及资产是否已存在 — 然后才开始任何构建。`release-please` 在每次向 `main` 推送时都会跑完，但通常并不创建发布，所以绝大多数链式运行必须在解析步骤上几秒内结束，而不是跑完一整趟 macOS 构建。链式运行的事件里没有标签，它取最新的发布并排除草稿：草稿有标签名却没有 git 标签，检出它会让运行在草稿存在期间一直失败。

构建检出的是 `refs/tags/<tag>` — **是该发布自身的代码，而不是 `main` 的最新提交**。`make_app.sh` 用 `git describe` 打版本号，在标签检出上它恰好就是该标签，因此 `CFBundleShortVersionString` 与发布一致。要让 `git describe` 看得到标签，必须完整检出（`fetch-depth: 0`）。发布 job 按提交 SHA 固定 actions，且不恢复任何构建缓存：它构建的东西会被签名并发给每一位用户，恢复的缓存就是混入交付物的外来产物（zizmor：cache-poisoning）。每次发布多花几分钟更划算。

应用包内的内嵌 CPython（`scripts/build_embedded_python.sh`）从 python-build-standalone 下载，版本与 build 标签均固定，并在解包前对照该发布公开的 `SHA256SUMS` 校验 sha256 —— 不匹配（包括被污染的本地缓存）会让构建失败，而不是把未经校验的解释器发给用户。运行时依赖只从 `requirements-runtime.lock` 以 `--require-hashes` 安装，构建也不会从 PyPI 升级 pip：签名包里正是仓库记录的内容，含传递依赖。改动 `pyproject.toml` 中的依赖后，用 `.venv/bin/python scripts/lock_runtime_deps.py` 重建 lock —— lock 落后于 `pyproject.toml` 时 `tests/test_runtime_lock.py` 会变红，早于发布构建以含糊的 “no hash” 失败。

改动上述任何内容后的验证：下载资产，`ditto -x -k`，`codesign -dv`，检查 `CFBundleShortVersionString` 与标签一致。

## 签名与公证

`release-app` 仅在下列 secret 存在时才签名并公证。没有它们时照旧以 ad-hoc 构建并打印 notice；有证书但缺少公证 secret 时打印 warning（已签名但未公证的包仍过不了 Gatekeeper）。不会仅因缺少 secret 而变红。

| Secret | 内容 |
|---|---|
| `APPLE_DEVELOPER_ID_P12` | 带私钥的 *Developer ID Application* 证书，从“钥匙串访问”导出为 `.p12`，再做 base64：`base64 -i developer-id.p12 \| pbcopy` |
| `APPLE_DEVELOPER_ID_P12_PASSWORD` | 导出时设置的密码 |
| `APPLE_NOTARY_KEY_P8` | App Store Connect API 密钥（`AuthKey_XXXXXXXXXX.p8`，Users and Access → Integrations → App Store Connect API → *Team keys*，Developer 角色即可），base64 编码 |
| `APPLE_NOTARY_KEY_ID` | 其 Key ID |
| `APPLE_NOTARY_ISSUER_ID` | 同一页面上的 Issuer ID |

Settings → Secrets and variables → Actions → New repository secret。证书在运行期间放入一个随机密码的临时钥匙串，并在 `always()` 步骤中删除；API 密钥存放在 `$RUNNER_TEMP`，同样被删除。身份名称（«Developer ID Application: <所有者> (<团队>)»）被注册为日志掩码，并通过 `$RUNNER_TEMP` 中的文件传给构建脚本（其环境中的 `CHAROITE_SIGN_IDENTITY`）—— CI 日志是公开的，否则 `spctl -vv` 会把所有者姓名打印进去。签名本身里它仍然可读。

流水线如何使用它们（`app/make_app.sh`、`scripts/make_dmg.sh`、`scripts/notarize.sh`）：

1. 内嵌 python 中的每个 Mach-O —— 按文件头魔数识别，而不是按文件名或可执行位，所以权限为 644 的 `libfoo.so.1` 也不会漏掉 —— 逐一签名，先库和 wheel 自带工具，再 `bin/*`，带 hardened runtime 和安全时间戳；解释器使用 `app/Resources/entitlements/embedded-python.entitlements`（音频输入、未签名可执行内存、关闭库校验）。任一签名失败都会让构建失败：未签名的 `.so` 在用户的 Mac 上就是 Gatekeeper 拒绝，而不是警告；
2. 应用包用 `app/Resources/entitlements/Charoite.entitlements`（音频输入、日历）签名，并以 `--deep --strict` 校验；
3. `Charoite.app.zip` 通过 `notarytool submit --wait` 提交，公证票据装订（staple）到 `.app`，再从装订后的包重建 zip（应用内更新器安装的正是这个 zip —— 它必须能离线工作）；
4. `Charoite.dmg` 单独构建、带时间戳签名、公证并装订，之后重新计算两个 `.sha256` —— 装订会改变 DMG，而更新器核对的是已发布的校验和；
5. 对应用和 DMG 执行 `spctl --assess` —— 即 Gatekeeper 在用户 Mac 上做的那项检查。

被拒的公证会打印 Apple 的日志：其中点名文件与原因（未签名的二进制、没有 hardened runtime、没有时间戳）。

为什么 hardened runtime 需要给 python 的 entitlements：公证要求包内*每个*可执行文件都启用 hardened runtime，而在其之下子进程不会从应用继承任何东西 —— 读麦克风的守护进程会得到一片静音，却没有任何错误。已在签名构建上验证：带 `audio-input` 的 `Contents/Resources/python/bin/python3` 能录到声音。第一个签名发布仍值得在应用里手动检查一次麦克风。

应用包必须与公证时逐字节一致。过去唯一会在运行时写入包内的，是内嵌 python 的 `__pycache__`；`AppDelegate.keepBundleSealed()` 在第一个子进程启动前把 `PYTHONPYCACHEPREFIX` 指向 `~/Library/Caches/ai.charoite.app/pycache`（回归测试 `BundleSealTests`）。任何会从包内运行 python 的改动之后都要检查：在一份已安装并用过的副本上执行 `codesign --verify --deep --strict` —— 必须仍然通过。

添加证书前需要知道两件事：

- 证书所有者的名字是公开的 —— 对下载的应用执行 `codesign -dv` 会显示 «Developer ID Application: <姓名> (<团队>)»，某些系统对话框也会显示。个人 Apple Developer 账号在那里放的是个人姓名，组织账号放的是组织名；
- ad-hoc 签名构建的指定要求是 `cdhash H"…"`，所以每次重建对 macOS 来说都是另一个应用，权限（麦克风、系统音频、日历）会丢失。Developer ID 让要求变成「标识符 + 团队」—— 权限能在更新后保留。

本地构建行为相同：`app/make_app.sh` 若登录钥匙串中有 Developer ID 就用它，否则 ad-hoc；`CHAROITE_SIGN_IDENTITY` 可覆盖这一选择。Developer ID 构建需要联网 —— 每次签名都要向 Apple 取时间戳；离线时用 `CHAROITE_SIGN_IDENTITY=- app/make_app.sh` 做 ad-hoc 构建。工作流会把每个发布的签名方式追加到其发布说明中 —— README 引用的正是那一行。

## 事后复盘：v0.19.0 双重事故

两个缺陷在同一个发布上相遇：

1. v0.19.0 发布时没带应用包（即上文防递归机制的教训）。
2. 第一版修复用 `gh release list --limit 1` 解析标签，构建的却是**当时的 `main`** — 检出没有指定 `ref`。等这个修复自己被合并时，`main` 已经领先于 `v0.19.0` 标签，于是更新代码的构建被挂到了旧标签上。下载 “0.19.0” 的用户拿到的应用包代码比发布本身更新，而 `Info.plist` 却一直声称是 0.19.0 — `main` 上的 `git describe` 仍解析到最后一个标签。

由此得出的不变量 — *资产必须从其自身标签的代码构建* — 现在由 `tests/test_workflows.py` 强制保证，工作流也显式检出标签。

**重新上传正确的 0.19.0 资产：** Actions → release-app → Run workflow → `tag: v0.19.0`。手动运行会从 `v0.19.0` 标签重新构建并替换错误资产（`--clobber`）。然后按上文验证：解压出的应用必须报告 `CFBundleShortVersionString` 为 0.19.0。

## 分支保护：什么会阻止合并，以及为什么

`main` 上的必需检查是 **`lint`** 和 **`pytest (src/)`**。布局门禁（`scripts/layout_map.py --check`）是 `pytest (src/)` 的一个步骤，因此它同样阻止合并；即使 pytest 为红它也会运行，以免测试收集失败掩盖它的结论。其余检查只报告、不阻止：`analyze`、`mutation (changed lines)`、文档守卫、PR 标题检查、供应链 job、Swift 与 iOS 构建以及 Android job。这个简短的列表背后有两个有意的决定。

**测试现在会阻止合并。** 此前并不会：必需检查是 `lint` 和 `analyze`，因此失败的 `pytest` 也能顺利合并。全部 123 个测试都只是参考性的，包括守护隐私承诺的那些哨兵。

**`analyze`（CodeQL）是参考性的，而非必需。** 它由 `pull_request` 事件触发；而对于与 `main` 冲突的 PR，GitHub 不会创建 merge ref，因此根本不会启动任何 `pull_request` 工作流。必需的检查上下文永远不会到达，PR 会永远卡在 “Expected — Waiting for status to be reported”，而且无从重跑。这正是过去那些“幽灵检查”的全部原因——只能靠从 `main` 重建分支来解决。`lint` 和 `pytest` 也会在 `push` 时运行，所以即使 PR 存在冲突，它们的上下文依然存在。所有只在 `pull_request` 上运行的 job ——`mutation (changed lines)`、文档守卫、PR 标题检查、dependency review——同理，因此也保持参考性；红了仍要在合并前读一遍。

**`strict`（要求分支为最新）已关闭。** 在一天发布四次的节奏下，每次合入 `main` 都会把所有开启的 PR 推入 BEHIND，而 `required_linear_history` 使修复方式变成 rebase——新的 SHA、全部检查重跑、又一次冲突的机会。它换来的是对语义冲突的防护，而这里本就没有任何机制实现这种防护。

**`swift test (app)`、`build (app-ios)` 与 Android job（`test, lint, assemble`）保持参考性**，只要其工作流仍带 `paths:` 过滤器。一个在没有 Swift 或 Kotlin 改动的 PR 上永不启动的必需检查，会像 `analyze` 一样把它挂死。

## 发布前的人工验收（界面与声音）

CI 既无法重现真实设备，也无法重现与大型本地模型（完整配置下 20+ GB 权重）争抢资源的情形，因此涉及实时回路或 UI 改动的发布，必须先在真实机器上做一次简短的人工检查：

1. 开始录音 → 两个声道各 2 分钟真实讲话 → 滚动文本在走，说话人标签合理，计时器在跳。
2. 大声问一个问题 → ⚡ 提示到达。
3. 停止 → 纪要生成，录音文件在位，通知已到达。
4. `tail -40 <数据>/logs/daemon.err.log` —— 没有新错误，也没有 `stt-health state=stalled`。

具体检查了什么，用一行写进发布 PR 的描述。不涉及界面/声音的改动（文档、流水线、脚本）无需验收。

## 更新清单签名（每次发布之后）

更新器不会安装没有所有者密钥签名清单的版本 —— 这是独立于 GitHub 的锚点（卡片 №24）：校验和就放在压缩包旁边，能改压缩包的人也能改校验和。私钥永远不进 CI。

发布（release-please + release-app）完成后，在所有者的机器上执行一条命令（需要 `gh` ≥ 2.28 —— `--latest` 参数；GitHub 令牌从 `~/.config/charoite/gh_token` 读取）：

    .venv/bin/python scripts/sign_release_manifest.py vX.Y.Z

没有私钥时脚本以退出码 2 拒绝，什么都不签。`--file <路径>` 改为签名本地文件（签名以 `.sig` 放在旁边，不经过 `gh`）。

脚本读取发布状态，下载 `Charoite.app.zip`，解包并**核对**应用包签名（codesign --strict，团队 AR7PDJQNR4）—— 签名前被调包的压缩包会被拒绝，而不是被签名；`Charoite.app` 是符号链接的压缩包同样被拒绝（codesign 会跟随链接，为链接目标——例如已安装的应用——作保）。随后它**自己**生成清单 `<版本>  <sha256>`（被签名的文件携带版本号——裸哈希允许把旧的诚实三件套挂到新标签下重放），用 `~/.config/charoite/update_manifest_ed25519.pem` 对其原始字节做签名（raw ed25519 → base64），把 `Charoite.app.zip.manifest` 和 `.manifest.sig` 附加到 release，最后一步取消 pre-release 标记（`gh release edit --prerelease=false`；当标签不早于当前 latest 时再加 `--latest`）。

### 门禁：没有签名的版本不会成为 latest（PR #375）

更新器查询 `/releases/latest`，GitHub 不会把 pre-release 放进去。因此在所有者签名之前，release 一直保持 pre-release —— 从两侧保证：

- **release-please** 把每个版本创建为 pre-release（`.github/release-please-config.json` 中的 `"prerelease": true`）—— 完全没有窗口期。release-please 的规则是 `prerelease && (版本后缀 || major == 0)`：在 0.x 上自动生效，到 1.0.0 就会停止；绊线测试 `test_release_gate_tripwire_pre_major_versions_only` 会在第一个 ≥ 1.0 的版本上失败，让人有意识地做决定（CI 门禁仍在，窗口期等于 Actions 的延迟），而不是悄无声息地丢掉它。
- **release-app**（CI）兜住其余一切。运行开始时：没有 `.manifest.sig` 的稳定版会被改为 pre-release（人工发布、手动取消的标记、1.0+）；触发器也监听 `released`，以捕捉在网页里取消的标记。每次构建之后：旧的 `.manifest`/`.manifest.sig` 会被删除（它们签的是**另一个**压缩包，已不再匹配），release 保持 pre-release 直到重新签名。CI 不上传 `.manifest`：没有签名，它对应用毫无用处。
- **签名脚本**按 release 状态行事。pre-release 且未签名 —— 常规路径。稳定且已签名 —— 重新签名：替换这对文件期间先把 release 隐藏为 pre-release（两次上传不是原子的，其间更新器会看到带着别人签名的清单）。稳定却**未**签名 —— 门禁失效：脚本照样签名（用户反正已被挡住），但会向 stderr 大声报警并以退出码 3 退出 —— 请查清这个 release 从何而来。只有当标签不早于当前 latest 且不带版本后缀时才加 `--latest`：在 v0.58.0 之后签署历史版本 v0.57.0 不会让 `/releases/latest` 倒退，v0.58.0-rc.1 也不会超过 v0.58.0。

在所有者签名之前，用户停留在上一个版本；签名上传或取消标记失败时，release 保持 pre-release，脚本会打印重试命令。绕过脚本手动执行 `gh release edit --latest` 恰恰是门禁要防的事：不要这样做（CI 会在下一个事件时恢复 pre-release，但在此之前的窗口期由你负责）。

私钥丢失意味着所有用户的更新都会停止：由所有者生成新密钥对，把公钥写入 `UpdateAuthenticity.swift`（常量 `manifestKeyBase64`），再发布新版本。
