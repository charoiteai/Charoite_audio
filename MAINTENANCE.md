# How Charoite is maintained

***English** · [Русский](docs/ru/MAINTENANCE.md) · [中文](docs/zh/MAINTENANCE.md)*

Charoite is maintained by an AI under human direction. This page
describes the process as it actually runs — the commit history is the
evidence, not a performance.

## Roles

- **The human owner** sets direction and priorities, defines the rules
  below, decides what ships and when, owns the privacy policy, and
  answers for the result.
- **The AI maintainer** (Claude Code, committing as `charoiteai`)
  writes the code: design, implementation, tests, documentation,
  releases — the full loop.

## How a change ships

1. The owner sets a task and the constraints that matter.
2. Claude implements it — code, tests, and documentation in the same
   change.
3. **Independent adversarial review.** Every substantive change is
   reviewed by a second head that did not write it: a separate Claude
   agent, or a different model entirely (decorrelated review). Findings
   are fixed and re-checked before the PR opens.
4. The local gate passes: `scripts/preflight.sh` runs lint, the layout
   guard, the privacy markers, the full Python suite, the Swift app tests
   when the app changed, and the mutation check on the changed lines, and
   ends with one verdict line. Measured on 2026-09-27: 3,936 Python tests,
   530 Swift tests of the macOS app, 67 iOS and 28 Android tests.
5. The pull request runs CI: `lint` and `pytest (src/)` (the layout gate
   included) are required; CodeQL, the mutation check on the changed lines,
   supply-chain checks (zizmor, dependency review), the documentation
   guard, the conventional PR title, the anonymization gate and — when
   their code changed — the Swift, iOS and Android builds report alongside
   ([RELEASING.md](docs/RELEASING.md) explains why only two block).
6. Red required checks block the merge, and a red advisory check is read
   before merging, not waved through. Green CI merges by squash; release-please cuts versioned
   releases, and each release stays a pre-release until the owner signs
   its update manifest.

### Guards are not to be silenced

A guard that goes red states a fact about the tree, and the maintainer's job is
to change the fact, not the guard. Three rules follow, and they bind the AI
maintainer as much as anyone:

- A failing check is never made green by regenerating the artifact it compares
  against, by widening a rule's scope, or by declaring an exception without a
  ticket and a written reason.
- An exception is a decision, so it is written down where a reviewer reads it —
  in the artifact, next to the thing it excuses, with the reason in plain words.
- Numbers in those reasons come from the measurement, not from memory. A count
  typed by hand drifts from the code the day after it is written, and nothing in
  the repository can catch it.

Autonomous overnight runs exist and are routine — they go through the
same review and CI gates as daytime work. Nothing merges on a red
pipeline, at any hour.

## Privacy of the process itself

The product's promise — nothing leaves your machine — extends to how it
is built:

- A **pre-commit anonymization gate** (`scripts/check_private_markers.py`)
  blocks names, employers, internal system names, and transcript
  fragments from ever reaching the public repository; a format-based
  second line in CI also covers pull requests from forks.
- When an external model is used for decorrelated review, it sees a
  **clean checkout of the public tree only** — never user data, never
  gitignored local state (recordings, transcripts, configs).
- Secrets and tokens live outside the repository; the key that signs
  update manifests never enters CI.

## What this means for contributors

External contributions are welcome and reviewed with the same pipeline —
see [CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through
[SECURITY.md](SECURITY.md); they are read by the owner, not just the AI.

*Last updated: 2026-09-27.*
