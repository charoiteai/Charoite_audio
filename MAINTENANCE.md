# How Charoite is maintained

***English** · [Русский](docs/ru/MAINTENANCE.md) · [中文](docs/zh/MAINTENANCE.md)*

Charoite is maintained by an AI under human direction. This page
describes the process as it actually runs — the commit history is the
evidence, not a performance.

## Roles

- **The human owner** sets direction and priorities, defines the rules
  below, decides what ships and when, owns the privacy policy, and
  answers for the result.
- **The AI maintainer** (Claude Code, committing as `Charoite AI`)
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
4. Full local test suites must pass (Python and Swift — currently
   ~1,000 tests combined).
5. A pull request runs the required CI: CodeQL, Python and Swift test
   suites, lint, supply-chain checks (zizmor), documentation guards,
   commit-message conventions, and the anonymization gate.
6. Red CI blocks the merge. Green CI merges by squash; release-please
   cuts versioned releases.

Autonomous overnight runs exist and are routine — they go through the
same review and CI gates as daytime work. Nothing merges on a red
pipeline, at any hour.

## Privacy of the process itself

The product's promise — nothing leaves your machine — extends to how it
is built:

- A **pre-commit anonymization gate** (`scripts/check_private_markers.py`)
  blocks names, employers, internal system names, and transcript
  fragments from ever reaching the public repository.
- When an external model is used for decorrelated review, it sees a
  **clean checkout of the public tree only** — never user data, never
  gitignored local state (recordings, transcripts, configs).
- Secrets and tokens live outside the repository.

## What this means for contributors

External contributions are welcome and reviewed with the same pipeline —
see [CONTRIBUTING.md](CONTRIBUTING.md). Security reports go through
[SECURITY.md](SECURITY.md); they are read by the owner, not just the AI.

*Last updated: 2026-08-16.*
