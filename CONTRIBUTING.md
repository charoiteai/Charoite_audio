# Contributing to Charoite

***English** · [Русский](CONTRIBUTING.ru.md) · [中文](CONTRIBUTING.zh.md)*

Thanks for your interest! Charoite is a fully local meeting assistant —
contributions that keep it local-first are very welcome.

## Ground rules

- **Local-first is non-negotiable.** No cloud calls, no telemetry, no
  accounts. The only network targets are localhost (Ollama, the optional
  brain companion) — the opt-in Claude layer is the single exception and
  stays off by default.
- **Russian-first UI, English-friendly code.** UI strings are Russian
  today (English STT works; English prompts are on the roadmap). Code,
  comments and commit messages are in English.
- **No pattern blacklists.** Classification decisions go through the
  local model, not through hardcoded word lists — patterns rot, models
  understand context.

## Workflow

1. Fork, branch from `main`: `feat/…`, `fix/…`, `docs/…`.
2. Conventional commits (`feat(app): …`, `fix(daemon): …`).
3. `swift build` clean and `swift test` green (app/Tests) for app changes; `python -m py_compile` for the
   daemon; run `scripts/memory_bench.py` if you touch search or prompts.
4. **Update docs in the same PR** — CI blocks code changes that leave
   `docs/`, `README*` and `CHANGELOG.md` untouched (label `skip-docs`
   for purely technical changes).
5. PR description: what changed, why, before/after where visible.

## Where to start

- [ROADMAP.md](ROADMAP.md) — what we plan next
- Issues labeled `good first issue`
- `docs/ARCHITECTURE.md` — how the daemon, diarization and the graph
  pipeline fit together

## Releases

release-please manages versions from conventional commits — no manual
version bumps in PRs, please.

## The de-identification guard

This repository is the public product; a private project sits behind it, and
nothing personal from there may leak in — names, employer, internal systems,
paths. `scripts/check_private_markers.py` runs as a pre-commit hook and blocks
a commit that adds any of them.

The marker list itself is private and lives outside git
(`~/.config/charoite/private_markers.txt`) — a list of what must not be
published is sensitive on its own. Without the file the hook fails closed
locally and skips in CI, so contributors are never blocked by a list they
cannot have.

The hook checks **two** things: the lines a commit adds, and the whole tracked
tree. The second one matters because a marker added to the list *later* leaves
its earlier occurrences untouched forever — the diff of every following commit
is clean, and the leak lives on in `main`. Two such lines were found this way.
A full scan on demand:

```bash
python3 scripts/check_private_markers.py --all   # prints places, never the marker
```

For already-published files the report is `path:line` without the text: that
output ends up in CI logs and other people's terminals, and it should not become
another copy of what we are hiding.

Markers of four characters or fewer are matched on word boundaries: a
three-letter abbreviation otherwise matches inside ordinary words, and a guard
that cries wolf is a guard people learn to bypass.
