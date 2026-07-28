# 发布流程

*[English](RELEASING.md) · [Русский](RELEASING.ru.md) · **中文***

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

`release-app` 在 macos runner 上构建 `Charoite.app.zip` 并把它附加到发布上。共三个触发器：

- `release-please` 工作流之后的 `workflow_run` — 主路径。release-please 用 `GITHUB_TOKEN` 发布 release，而 GitHub 的防递归机制意味着这类事件不会在其他工作流里触发 `release: published`（v0.19.0 最初发布时就没带应用包 — 我们由此学到教训）。该 job 只在 release-please **成功**时才继续（失败的运行同样会发出 `completed`）。
- `release: published` — 保留给人工创建的发布。
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
