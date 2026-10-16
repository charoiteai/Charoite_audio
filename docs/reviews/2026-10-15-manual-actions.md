# Manual actions for the owner/admin (audit 2026-10-15)

Items from the repository audits of 2026-10-15 that need owner or admin
rights or an owner decision. Nothing here has been done automatically.

## Branches

- [ ] Delete (verified: no unique work left vs `main`):
  `docs/meeting-state-architecture`, `fix/633-r1`, `codex/runtime-profile`
  (only the generated `docs/design/layout.json` differs, `main` is newer).
- [ ] Delete if the closing decision stands:
  - `codex/decision-evidence-audit` (#778 closed; work is in `main`, only layout files differ)
  - `codex/retrieval-model-evaluation` (#777 closed; same)
  - `codex/nli-full-pair-guard` (#782 closed on purpose: pairs never exceed the tokenizer limit)
  - `fix/audit-2026-10-11-reliability` (#774 closed: part 1 covered by #796; confirm the vector-rebuild task is done)
- [ ] `gh-pages`: stale since 2026-08-19; `pages.yml` deploys from `main`.
  Check Settings → Pages → Source = "GitHub Actions", then delete.
- [ ] `feat/embeddinggemma-2`: unmerged embedding-profile work (20 files,
  `model_seam.py`, `test_embed_profile.py`), no PR. Decide: open a PR or drop it.
- Never delete `release-please--branches--main` — release-please recreates and uses it.

## Settings

- [ ] Ruleset for `main`: required checks `lint` and `pytest (src/)`,
  squash merge only, block force-push and deletion.
- [ ] Settings → General: "Automatically delete head branches".
- [ ] Code security: secret scanning + push protection.
- [ ] Code security: Dependabot alerts and Dependabot security updates.
- [ ] Code security: private vulnerability reporting (then link it from `SECURITY.md`).
- [ ] Social preview: upload `docs/img/social-preview.png`.
- [ ] Homepage: currently `/releases/latest`; point it at the devlog site if that is the public face.

## Pull requests

- [ ] #769 release 0.96.0: merge (main has moved on since 0.95.0).
- [ ] #799 (jekyll 4.4.1): check whether #797 already covers it; close if so.
- [ ] #804 and #790: overlapping UX work from 2026-10-11; review in order, rebase the second.
- [ ] #766 devlog entries: open since 2026-10-08 — merge or close.

## Owner decisions

- [ ] Default model: `config/config.example*.yaml` uses `qwen3.6:35b-a3b`,
  the 64 GB preset (`README.md`, `ModelPresetPolicy.swift`) uses `qwen3.6:35b-mlx`,
  `docs/MODELS.md:9` says the template starts on the 16 GB set (`qwen3.5:4b`). Pick one.
- [ ] Quote accuracy: `README.md` (32 GB row) says 96%,
  `ModelPresetPolicy.swift:98` comment says 92% for 35b-mlx. Confirm against the bench.
- [ ] `app/Sources/CharoiteApp/Services/LocalChatService.swift:74,80,255` hard-codes
  `qwen3.6:35b-a3b` / `gemma4:*` regardless of the RAM preset.

## Documentation

- [ ] ZH translations lag far behind EN (lines EN/ZH): FEATURES 1444/146,
  ARCHITECTURE 2028/1243, DIARIZATION 1006/274, CONTRIBUTING 451/172,
  RELEASING 369/147, ROADMAP 219/73, SECURITY 229/116. Translate or mark as abridged.
- [ ] RU/ZH: sync the README/FEATURES sections changed in `docs/readme-features-sync`.
- [ ] `docs/INVARIANTS.md` has no `docs/ru/` or `docs/zh/` version.
