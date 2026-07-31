# 为 Charoite 做贡献

*[English](../../CONTRIBUTING.md) · [Русский](../ru/CONTRIBUTING.md) · **中文***

感谢你的关注！Charoite 是一款完全本地运行的会议助手 — 非常欢迎坚持本地优先的贡献。

## 基本规则

- **本地优先不容妥协。** 不调用云端、无遥测、无账号。唯一的网络目标是 localhost（Ollama、可选的大脑伴侣服务）— 需手动开启的 Claude 层是唯一例外，且默认关闭。
- **界面俄语为先，代码英语友好。** 目前 UI 字符串是俄语（英语 STT 可用；英语提示词已列入路线图）。代码、注释与提交信息使用英语。
- **不搞模式黑名单。** 分类决策通过本地模型完成，而不是硬编码的词表 — 模式会腐烂，模型才理解上下文。

## 工作流程

1. Fork 后从 `main` 拉分支：`feat/…`、`fix/…`、`docs/…`。
2. 约定式提交（`feat(app): …`、`fix(daemon): …`）。
3. 应用改动：`swift build` 干净通过、`swift test` 全绿（app/Tests）；守护进程：`python -m py_compile`；触碰搜索或提示词时运行 `scripts/memory_bench.py`。
4. **在同一个 PR 里更新文档** — 代码改动若不触及 `docs/`、`README*` 与 `CHANGELOG.md`，CI 会拦截（纯技术性改动可打 `skip-docs` 标签）。
5. PR 描述：改了什么、为什么改，可见之处附前后对比。

## 从哪里开始

- [ROADMAP.md](../../ROADMAP.md) — 我们接下来的计划
- 带 `good first issue` 标签的 issue
- `docs/ARCHITECTURE.md` — 守护进程、说话人分离与图谱流水线如何协同

## 发布

release-please 根据约定式提交管理版本号 — 请勿在 PR 里手动提升版本。
