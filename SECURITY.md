# Security policy

***English** · [Русский](docs/ru/SECURITY.md) · [中文](docs/zh/SECURITY.md)*

Charoite runs fully on the user's machine, so most classic web attack
surface does not apply — but bugs in audio handling, file paths or the
local HTTP calls are still security-relevant.

**Cloud calls are prompt-injection-aware.** Meeting transcripts, cores and
dossiers are other people's words, so every headless `claude -p` the app
spawns is isolated: text-only calls carry a full tool denylist plus
`--setting-sources ""` and `--strict-mcp-config` (`cloud.text_only_args()`),
so injected instructions cannot read files, run commands or reach the
network — even when the machine owner's own `~/.claude/settings.json`
allowlists those tools. The one call that legitimately touches files (the
post-meeting cloud review) gets its rights from an explicit privacy key and
the same settings isolation; `tests/test_cloud_isolation.py` fails on any
new non-isolated call site.

**Report vulnerabilities privately** via GitHub: Security → Report a
vulnerability (private advisory), or email charoiteai@gmail.com.
Please do not open public issues for security problems.

You can expect an answer within a few days. Supported version: `main`.
