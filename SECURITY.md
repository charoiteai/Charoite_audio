# Security policy

***English** · [Русский](docs/ru/SECURITY.md) · [中文](docs/zh/SECURITY.md)*

Charoite runs fully on the user's machine, so most classic web attack
surface does not apply — but bugs in audio handling, file paths or the
local HTTP calls are still security-relevant.

**Report vulnerabilities privately** via GitHub: Security → Report a
vulnerability (private advisory), or email charoiteai@gmail.com.
Please do not open public issues for security problems.

You can expect an answer within a few days. Supported version: `main`.

## Threat model in one paragraph

The assets are the user's own meetings: recordings, transcripts and the
knowledge graph built from them. There is no server side. What remains as
attack surface: data **leaving** the machine (the opt-in cloud layer),
other people's words **entering** models (prompt injection from
transcripts), the **update and dependency** channel, and bugs that can
destroy a recording locally. Each gets its own defenses below.

## What leaves the machine

- **Nothing, by default.** STT, diarization, the LLM and embeddings run on
  localhost; `src/privacy.py` is the single authority for every network
  exit and treats only an explicit `true` in the config as consent.
- **The cloud layer is opt-in per capability** — live answers, hint
  refinement, the post-meeting review and graph edits each have their own
  key; graph edits additionally back files up first and enforce boundaries
  on what the model may touch. `CHAROITE_NO_CLOUD=1` is a kill-switch that
  overrides any config on every path.
- The subscription CLI runs with `ANTHROPIC_API_KEY` scrubbed from its
  environment. The exact key table and guarantees: [PRIVACY.md](PRIVACY.md).

## Prompt injection

Meeting transcripts, cores and dossiers are other people's words, so every
headless `claude -p` the app spawns is isolated. Text-only calls carry a
full tool denylist plus `--setting-sources ""` and `--strict-mcp-config`
(`cloud.text_only_args()`), so injected instructions cannot read files,
run commands or reach the network — even when the machine owner's own
`~/.claude/settings.json` allowlists those tools. The one call that
legitimately touches files (the post-meeting cloud review) gets its rights
from an explicit privacy key and the same settings isolation.
`tests/test_cloud_isolation.py` scans the sources and fails on any new
non-isolated call site.

## Recordings are fail-closed

A recording in progress is the most valuable local asset, so operations
around it fail closed: the in-app updater re-checks for a live recording
right before swapping the bundle, and its replacement helper refuses to
touch the install while the app process is still alive; recording sinks
open exclusively (`"xb"`), so a filename collision is a visible error
rather than a silent overwrite; on stop the audio is handed over through
atomic renames. Mechanics: [ARCHITECTURE.md](docs/ARCHITECTURE.md),
"Surviving a crash".

## Supply chain and release integrity

- All workflows run with least-privilege `permissions:` blocks and
  `persist-credentials: false`; user-controlled input reaches scripts via
  environment variables, not interpolation. In CI, zizmor gates workflow
  security, dependency review gates high-severity advisories, CodeQL runs
  on every push.
- Actions are pinned to versioned tags under a documented policy
  (`.github/zizmor.yml`); dependabot updates actions, swift, gradle and
  pip weekly.
- Releases build strictly from the release tag. The embedded CPython is
  version-pinned and sha256-verified against upstream's published
  `SHA256SUMS`; `Charoite.app.zip` and `Charoite.dmg` ship with published
  sha256 files, and the in-app updater verifies the checksum before
  installing anything.
- **Known limitation:** builds are ad-hoc signed for now (first install
  needs right-click → Open; documented in the README). Developer ID
  signing and notarization are on the roadmap — the blocker is
  hardened-runtime entitlements for the embedded python daemon's
  microphone access.

## Anonymization of this repository

The product is developed against real meetings, so three gates keep
private data out of the public repo: a fail-closed local pre-commit gate
(the marker list itself lives outside the repo), a commit-author check,
and a format-based CI gate that also covers pull requests from forks.
