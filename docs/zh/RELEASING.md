# 发布流程

*[English](../RELEASING.md) · [Русский](../ru/RELEASING.md) · **中文***

版本号与 `CHANGELOG.md` 由 [release-please](https://github.com/googleapis/release-please) 自动维护。changelog 永远不用手工编辑 — 只需写 `fix:`/`feat:` 约定式提交，其余的在合并进 `main` 时自动完成。

## 工作原理

1. 每次向 `main` 推送都会运行 `release-please` 工作流。
2. 它把上次发布以来的 `fix:`/`feat:` 提交收集成一个标题为 `chore(main): release X.Y.Z` 的**发布 PR**，同时更新 `CHANGELOG.md` 与 `.github/.release-please-manifest.json`。
3. 合并该 PR 会给提交打标签（`vX.Y.Z`）并创建 GitHub Release。

当前版本记录在 `.github/.release-please-manifest.json` 里 — 而不是仓库根目录的某个 `version.txt`。Git 标签是权威来源。

## Squash 合并：PR 标题就是那条提交

使用 squash 合并时，`main` 恰好收到一条提交，其主题就是 **PR 标题**。如果该标题不是约定式提交（`fix: …`、`feat: …`），release-please 就完全看不到这些工作：没有发布 PR、没有 CHANGELOG 条目、没有版本号提升。这个坑我们一天内踩了四次（#83–#86 全部以 `Fix/<branch name>` 的形式合并），导致一整天已交付的修复在 changelog 里不可见。

规则：

1. 确认 squash 之前，在合并对话框里把标题改成约定式格式。
2. 一个 PR 含多个用户可见变更时：在 squash 的**正文**里额外添加普通的 `fix(scope): …` 行（不要 `* ` 项目符号 — 带项目符号的行不会被解析）— release-please 会把每一行登记为独立条目。
3. 已经用错误标题合并了：推送一条“载体提交”，其提交信息里带上漏掉的约定式行（可以是维护者本机的空提交，也可以是通过 PR 的小型真实改动）。

## 一次性配置：RELEASE_PLEASE_TOKEN

发布 PR 必须由**个人访问令牌**（personal access token）创建，而不是内置的 `GITHUB_TOKEN`。GitHub 有意不对内置令牌创建的分支运行 CI（防循环保护），因此发布 PR 会缺少必需检查、一直停在 `BLOCKED` 状态。PAT 让分支被视为“人类”创建，`lint`/`analyze` 就能正常运行。

配置方式（仓库所有者，一次性）：

1. 创建一个仅限本仓库的 **fine-grained PAT**，权限：
   - **Contents: Read and write**（打标签 + changelog 提交）
   - **Pull requests: Read and write**（打开发布 PR）
2. 把它添加为名为 **`RELEASE_PLEASE_TOKEN`** 的仓库 secret（Settings → Secrets and variables → Actions → New repository secret）。

secret 缺失时工作流会回退到 `GITHUB_TOKEN`，所以在此期间什么都不会坏 — 只是在 PAT 就位之前，发布 PR 需要手动点一次 “Approve and run”。

## 每次发布都附带应用包

`release-app` 在 macos runner 上构建 `Charoite.dmg`（首次安装用的安装器）、
`Charoite.app.zip`（已安装应用据此更新）以及两者的 `.sha256` —— 没有已发布的校验和，
应用内更新会拒绝安装下载到的文件。构建完成后全部附加到发布上。共三个触发器：

- `release-please` 工作流之后的 `workflow_run` — 主路径。release-please 用 `GITHUB_TOKEN` 发布 release，而 GitHub 的防递归机制意味着这类事件不会在其他工作流里触发 `release: published`（v0.19.0 最初发布时就没带应用包 — 我们由此学到教训）。该 job 只在 release-please **成功**时才继续（失败的运行同样会发出 `completed`）。
- `release: published` — 保留给人工创建的发布。配置了 `RELEASE_PLEASE_TOKEN`（PAT）后，release-please 自己创建的发布也会触发它，因此一次发布会有两次运行：`concurrency` 把它们串行化，第二次看到 `Charoite.dmg` 就以 `build=false` 退出。同时也监听 `released`——pre-release 变为稳定版（由签名脚本或在网页里手动操作）时，会再次经过下文的签名门禁。
- 带 `tag` 输入的 `workflow_dispatch` — 给旧发布手动重新上传（见下文 v0.19.0 事后复盘）。手动运行总是重新构建并用 `--clobber` 覆盖资产。

job 内部的顺序是刻意安排的：**第一步**先解析哪个标签需要资产、以及资产是否已存在 — 然后才开始任何构建。`release-please` 在每次向 `main` 推送时都会跑完，但通常并不创建发布，所以绝大多数链式运行必须在解析步骤上几秒内结束，而不是跑完一整趟 macOS 构建。

构建检出的是 `refs/tags/<tag>` — **是该发布自身的代码，而不是 `main` 的最新提交**。`make_app.sh` 用 `git describe` 打版本号，在标签检出上它恰好就是该标签，因此 `CFBundleShortVersionString` 与发布一致。要让 `git describe` 看得到标签，必须完整检出（`fetch-depth: 0`）。

改动上述任何内容后的验证：下载资产，`ditto -x -k`，`codesign -dv`，检查 `CFBundleShortVersionString` 与标签一致。

## 事后复盘：v0.19.0 双重事故

两个缺陷在同一个发布上相遇：

1. v0.19.0 发布时没带应用包（即上文防递归机制的教训）。
2. 第一版修复用 `gh release list --limit 1` 解析标签，构建的却是**当时的 `main`** — 检出没有指定 `ref`。等这个修复自己被合并时，`main` 已经领先于 `v0.19.0` 标签，于是更新代码的构建被挂到了旧标签上。下载 “0.19.0” 的用户拿到的应用包代码比发布本身更新，而 `Info.plist` 却一直声称是 0.19.0 — `main` 上的 `git describe` 仍解析到最后一个标签。

由此得出的不变量 — *资产必须从其自身标签的代码构建* — 现在由 `tests/test_workflows.py` 强制保证，工作流也显式检出标签。

**重新上传正确的 0.19.0 资产：** Actions → release-app → Run workflow → `tag: v0.19.0`。手动运行会从 `v0.19.0` 标签重新构建并替换错误资产（`--clobber`）。然后按上文验证：解压出的应用必须报告 `CFBundleShortVersionString` 为 0.19.0。


## 分支保护：什么会阻止合并，以及为什么

`main` 上的必需检查是 **`lint`** 和 **`pytest (src/)`**。这个简短的列表背后有两个有意的决定。

**测试现在会阻止合并。** 此前并不会：必需检查是 `lint` 和 `analyze`，因此失败的
`pytest` 也能顺利合并。全部 123 个测试都只是参考性的，包括守护隐私承诺的那些哨兵。

**`analyze`（CodeQL）是参考性的，而非必需。** 它由 `pull_request` 事件触发；而对于与
`main` 冲突的 PR，GitHub 不会创建 merge ref，因此根本不会启动任何 `pull_request`
工作流。必需的检查上下文永远不会到达，PR 会永远卡在
“Expected — Waiting for status to be reported”，而且无从重跑。这正是过去那些
“幽灵检查”的全部原因——只能靠从 `main` 重建分支来解决。`lint` 和 `pytest` 也会在
`push` 时运行，所以即使 PR 存在冲突，它们的上下文依然存在。

**`strict`（要求分支为最新）已关闭。** 在一天发布四次的节奏下，每次合入 `main` 都会把
所有开启的 PR 推入 BEHIND，而 `required_linear_history` 使修复方式变成 rebase——
新的 SHA、全部检查重跑、又一次冲突的机会。它换来的是对语义冲突的防护，而这里本就没有
任何机制实现这种防护。

**`swift test (app)` 与 `build (app-ios)` 保持参考性**，只要其工作流仍带 `paths:`
过滤器。一个在没有 Swift 改动的 PR 上永不启动的必需检查，会像 `analyze` 一样把它挂死。

## 更新清单签名（每次发布之后）

更新器不会安装没有所有者密钥签名的版本：校验和就放在压缩包旁边，能改压缩包的人
也能改校验和，所以需要一个独立于 GitHub 的锚点。私钥永远不进 CI。

发布（release-please + release-app）完成后，在所有者的机器上执行一条命令
（需要 `gh` ≥ 2.28）：

    .venv/bin/python scripts/sign_release_manifest.py vX.Y.Z

脚本下载 `Charoite.app.zip`，用 `codesign --strict` 核对 bundle 签名（陌生的压缩包
会被拒绝，而不是被签名），自己生成清单 `<版本>  <sha256>`，用
`~/.config/charoite/update_manifest_ed25519.pem` 做 raw ed25519 签名，把
`Charoite.app.zip.manifest` 和 `.manifest.sig` 附加到 release，最后取消
pre-release 标记（`gh release edit --prerelease=false`；只有当标签不早于当前 latest 时才加 `--latest`）。

### 门禁：没有签名的版本不会成为 latest（PR #375）

更新器查询 `/releases/latest`，GitHub 不会把 pre-release 放进去。因此在所有者
签名之前，release 一直保持 pre-release：

- release-please 把每个版本创建为 pre-release（`.github/release-please-config.json`
  中的 `"prerelease": true`；该规则只对 0.x 生效，1.0 之后由测试
  `test_release_gate_tripwire_pre_major_versions_only` 提醒重新决定）。
- release-app（CI）：未签名的稳定版会被改回 pre-release；每次重新构建后，旧的
  `.manifest`/`.manifest.sig` 会被删除（它们签的是另一个压缩包），release 保持
  pre-release 直到重新签名。CI 不再上传 `.manifest`。
- 签名脚本按 release 状态行事：对已签名的稳定版重新签名时会先临时隐藏为
  pre-release；遇到未签名的稳定版会照样签名，但以退出码 3 报警；只有当标签不早于
  当前 latest 且不带版本后缀（v0.58.0-rc.1 不会超过 v0.58.0）时才加 `--latest`。

不要绕过脚本手动执行 `gh release edit --latest`——门禁防的正是这个。
私钥丢失意味着所有用户的更新都会停止：由所有者生成新密钥对，把公钥写入
`UpdateAuthenticity.swift`（常量 `manifestKeyBase64`），再发布新版本。

