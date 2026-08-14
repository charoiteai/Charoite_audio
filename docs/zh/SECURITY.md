# 安全策略

*[English](../../SECURITY.md) · [Русский](../ru/SECURITY.md) · **中文***

Charoite 完全运行在用户本机上，因此大多数经典 Web 攻击面并不适用 — 但音频处理、文件路径或本地 HTTP 调用里的缺陷仍然事关安全。

**云端调用会防范提示注入。** 逐字稿、内核和档案都属于不可信文本。纯文本调用不提供任何 built-in 或 MCP 工具，也不加载用户设置。唯一可接触文件的会后复盘只看到显式工具集；`Read(/**)` 与可选的 `Edit(/**)` 锚定在 `cwd=graph`，`dontAsk` 会拒绝图谱以外的路径。Shell、网络和 MCP 工具始终不可用；对应边界由 `test_cloud_isolation.py` 与 `test_cloud_enrich_permissions.py` 守护。

**请通过私密渠道报告漏洞**：在 GitHub 上 Security → Report a vulnerability（私密安全通告），或发邮件到 charoiteai@gmail.com。请不要为安全问题开公开 issue。

通常几天内会得到答复。受支持的版本：`main`。
