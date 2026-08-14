# Security policy

***English** · [Русский](docs/ru/SECURITY.md) · [中文](docs/zh/SECURITY.md)*

Charoite runs fully on the user's machine, so most classic web attack
surface does not apply — but bugs in audio handling, file paths or the
local HTTP calls are still security-relevant.

**Cloud calls are prompt-injection-aware.** Meeting transcripts, cores and
dossiers are other people's words, so every headless `claude -p` the app
spawns is isolated. Text-only calls use an empty built-in tool set, deny all
MCP tools, and ignore user/project settings. The post-meeting review is the
only call that legitimately touches files: its visible tools are explicit,
`Read(/**)` and optional `Edit(/**)` are anchored to `cwd=graph`, and
`dontAsk` rejects paths outside that graph instead of prompting. Shell,
network and MCP tools remain absent. `tests/test_cloud_isolation.py` and
`tests/test_cloud_enrich_permissions.py` fail on a broad or non-isolated
grant.

**Report vulnerabilities privately** via GitHub: Security → Report a
vulnerability (private advisory), or email charoiteai@gmail.com.
Please do not open public issues for security problems.

You can expect an answer within a few days. Supported version: `main`.
