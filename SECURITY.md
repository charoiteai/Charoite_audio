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

- **One request by default: the daily version check.** A public GET to
  api.github.com for the latest release number — no token, no data about
  you or your meetings; `sufler.check_updates: false` turns it off, and
  the `CHAROITE_NO_CLOUD` kill-switch covers it too. Everything else runs
  on localhost: STT, diarization, the LLM and embeddings. `src/privacy.py`
  is the single authority for every exit that can carry meeting data, and
  it treats only an explicit `true` in the config as consent.
- **The cloud layer is opt-in per capability, and the keys nest.**
  `cloud_live` gates mid-meeting answers (`cloud_hints` works only on top
  of it). `cloud_enrich` gates the post-meeting review — and the nightly
  graph reviews run under the same key: the cores revision and the dossier
  review send graph-derived text to Anthropic overnight. `cloud_edit_graph`
  (on top of `cloud_enrich`) is the only key that grants writing; files
  are backed up first and boundaries enforced afterwards, and the write
  right is dropped whenever that backup cannot be taken. `CHAROITE_NO_CLOUD=1`
  is a kill-switch that overrides any config on every path. The full key
  table: [PRIVACY.md](PRIVACY.md).
- The subscription CLI runs with `ANTHROPIC_API_KEY` scrubbed from its
  environment.

## Prompt injection

Meeting transcripts, cores and dossiers are other people's words, so every
headless `claude -p` the app spawns is isolated. Text-only calls carry a
tool denylist covering every file, command and network tool the CLI ships
today, plus `--setting-sources ""` and `--strict-mcp-config`
(`cloud.text_only_args()`) — the latter matters because without it the
machine owner's own `~/.claude/settings.json` allowlists would apply to
these calls. Honest limits: it is a denylist, extended as the CLI grows,
not an allowlist-grade guarantee. The one call that legitimately touches
files (the post-meeting cloud review) gets its rights from an explicit
privacy key and the same settings isolation — and drops the write right
whenever the pre-edit backup cannot be taken.
`tests/test_cloud_isolation.py` scans `src/` and `scripts/` for
identifier-style call sites — a safety net for the common case, not a
proof.

## Recordings are fail-closed

A recording in progress is the most valuable local asset, so operations
around it fail closed: the in-app updater re-checks for a live recording
right before swapping the bundle, and its replacement helper refuses to
touch the install while the app process is still alive; the desktop
daemon's recording sinks open exclusively (`"xb"`), so a filename
collision is a visible error rather than a silent overwrite; on stop the
audio is handed over through atomic renames. The iOS and Android
companions use their platforms' recorders and do not yet make the
exclusive-open guarantee. Mechanics:
[ARCHITECTURE.md](docs/ARCHITECTURE.md), "Surviving a crash".

## What the signed app trusts locally

The macOS app holds the microphone and screen-recording permissions the
person granted, and the Python daemon inherits them as a child process.
So *which* daemon code runs and *which* data folder it reads is a
privilege boundary, not a path preference.

- **Code** runs from the signed bundle whenever the bundle contains it.
  A local checkout is used only when a person picks that folder in
  Settings *and* switches on "Run the daemon code from this folder
  (development)". Before the 16.08 audit any process without TCC rights
  could drop `~/Charoite_audio/src/daemon.py` and have it executed with
  the app's permissions.
- **Data** (`config/config.yaml`, `models/`, transcripts) comes from
  Application Support when the code is bundled, or from the folder chosen
  in Settings. The clone in the home folder is no longer adopted
  automatically: `config.yaml` carries `sufler.post_meeting_hook`, a shell
  command run after every meeting, so a silently adopted writable folder
  was the same door as unsigned code (second-opinion review of #328).
  People whose data lives in `~/Charoite_audio` are asked once, visibly,
  and the answer is remembered; the daemon code stays bundled either way.

## Supply chain and release integrity

- All workflows run with least-privilege `permissions:` blocks and
  `persist-credentials: false`; user-controlled input reaches scripts via
  environment variables, not interpolation. In CI, zizmor gates workflow
  security, dependency review gates high-severity advisories, CodeQL runs
  on pushes to `main`, pull requests and a weekly cron (Python only for
  now).
- Actions are pinned to versioned tags under a documented policy
  (`.github/zizmor.yml`), with one exception: the two workflows that hold
  `contents: write` — `release-please` (admin PAT) and `release-app`
  (release assets) — pin actions by commit SHA. A hijacked mutable tag
  there would reach the artifacts users install, which is a different
  price from a read-only job.
- The embedded Python runtime installs from `requirements-runtime.lock`
  with `--require-hashes`, so the signed bundle contains exactly the
  packages recorded in the repository — not whatever PyPI served during
  the build, transitive dependencies included. Rebuild the lock with
  `scripts/lock_runtime_deps.py` after changing dependencies.
- Model weights are verified by sha256 recorded in `scripts/get_models.py`.
  The download URLs point at mutable refs (`resolve/main`, release
  assets), so a mirror owner could swap a file without changing the URL —
  and that file then listens to every meeting. A mismatch aborts the
  install and asks a human to decide; `--url` (your own mirror) skips the
  check, since our digest cannot apply to someone else's file.
- Dependabot updates actions, swift, gradle and pip weekly.
- Releases build strictly from the release tag. The embedded CPython is
  version-pinned and sha256-verified against upstream's published
  `SHA256SUMS`, and the build does not upgrade its pip from PyPI — the
  bundle installs only from the hash-locked requirements (until 16.08 an
  unpinned `pip install --upgrade pip` ran right before the locked
  install); `Charoite.app.zip` and `Charoite.dmg` ship with published
  sha256 files, and the in-app updater verifies the checksum before
  installing anything.
- **Known limitation:** builds are ad-hoc signed for now, so macOS blocks
  the first launch — System Settings → Privacy & Security → *Open Anyway*
  (right-click → Open no longer works on macOS 15+; details in the
  README). Developer ID signing and notarization are on the roadmap — the
  blocker is hardened-runtime entitlements for the embedded python
  daemon's microphone access.

## Anonymization of this repository

The product is developed against real meetings, so three gates keep
private data out of the public repo: a fail-closed local pre-commit gate
(the marker list itself lives outside the repo), a commit-author check,
and a format-based CI gate that also covers pull requests from forks.
