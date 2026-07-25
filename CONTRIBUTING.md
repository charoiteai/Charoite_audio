# Contributing to Charoite

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
3. `swift build` clean for app changes; `python -m py_compile` for the
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
