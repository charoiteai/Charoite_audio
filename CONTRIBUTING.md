# Contributing to Charoite

***English** · [Русский](docs/ru/CONTRIBUTING.md) · [中文](docs/zh/CONTRIBUTING.md)*

Thanks for your interest! Charoite is a fully local meeting assistant —
contributions that keep it local-first are very welcome.

How the project is maintained day to day — the AI-maintainer pipeline,
review gates, and who answers for what — is documented in
[MAINTENANCE.md](MAINTENANCE.md).

## Ground rules

- **Local-first is non-negotiable.** No cloud calls, no telemetry, no
  accounts. The only network targets are localhost (Ollama, the optional
  brain companion) — the opt-in Claude layer is the single exception and
  stays off by default.
- **Russian-first UI, English-friendly code.** UI strings are Russian
  today (English STT works; English prompts are on the roadmap). Code,
  comments and commit messages are in English.
- **A test must be able to fail.** Not "covers the lines" — fails when the
  behaviour breaks. Check it by hand: put the defect back and make sure the
  test turns red. Coverage does not catch this: a test with no assertion at
  all covers the code fully and stays green, while the green check reads as
  proof the place is guarded. The crude cases are caught by
  `scripts/check_test_assertions.py` (CI and pre-commit): a test with no
  `assert`/`pytest.raises`/`raise ...Error`, and assertions placed after a
  `return`, where execution never arrives. The subtle ones — a tautology, a
  mock that replaced the very logic under test — no static check will find;
  only a restored defect will.
- **When in doubt about a test, break the code.**
  `scripts/mutate_check.py --range main...HEAD` puts defects back into the
  changed lines and demands that the tests go red. A surviving mutant is a
  behaviour change nobody noticed: either the test for that place exists but
  holds nothing, or there is no test at all. Only lines from the diff are
  mutated — a whole-file pass means thousands of mutants and hours instead of
  minutes. The mutation lands in a separate git worktree, so test subprocesses
  see the same broken code the imports do. An equivalent mutant (a threshold
  that both code and test read from one constant) need not be fixed — but is
  worth a look: on 20.08 one such survivor revealed that behaviour exactly at
  the threshold was tested by nobody.
- **Decisions live in pure functions, loops only apply them.** The live
  contour (`stt_loop`, the heartbeat loop) is a closure inside
  `daemon.main()` — no unit test reaches it, and a mutation run on 21.08 put
  a number on that: 53 of 53 mutants in `daemon.py` survived while the
  policies already extracted to `src/stt_runtime.py` killed 14 of 15. So
  every threshold, hysteresis and branch choice of the live contour is a
  named function in `stt_runtime` with an invariant in
  `tests/test_stt_runtime.py` (`progress_throttled`, `lag_transition`,
  `diarization_plan`, `live_input_young_enough`, …); the loop calls it and
  does nothing else. Boundary tests take a non-zero `last`: with zero,
  `now - last` and `now + last` are indistinguishable — the mutator showed it.
- **No pattern blacklists.** Classification decisions go through the
  local model, not through hardcoded word lists — patterns rot, models
  understand context.

## Workflow

1. Fork, branch from `main`: `feat/…`, `fix/…`, `docs/…`.
2. Conventional commits (`feat(app): …`, `fix(daemon): …`).
3. `swift build` clean and `swift test --filter '^CharoiteAppTests\.'` green (live probes run only by hand) for app changes; `python -m py_compile` for the
   daemon; run `scripts/memory_bench.py` if you touch search or prompts.
4. **Update docs in the same PR** — CI blocks code changes that leave
   `docs/`, `README*` and `CHANGELOG.md` untouched (label `skip-docs`
   for purely technical changes).
5. PR description: what changed, why, before/after where visible.

### What CI checks

| When | What |
|---|---|
| every PR | lint, python tests, app Swift tests, iOS build, CodeQL, docs guard |
| nightly | the same python and Swift tests on macOS plus **iOS tests in the simulator** |

### The layout guard

`docs/design/layout.json` is the single source of truth for how `src/` is laid
out: which layer a module belongs to, which import edges point the wrong way,
which files are entry points, and which modules may derive the data root
themselves. `docs/design/layout.md` is a generated map of the same facts — it is
never edited by hand.

If a PR turns that check red, the message names the fix. The usual cases:

| Message | What it means |
|---|---|
| new edge against the arrows | the import crosses a layer boundary — untangle it, or add it to `allowed_edges` **with a ticket** |
| `allowed_edges` holds X → Y, but that edge is gone | the debt was paid, remove the entry |
| file derives the root itself | take it from `src/charoite_paths.py` instead of re-parsing `CHAROITE_ROOT` or walking up from `__file__` |
| file remembers the canon's answer at import | ask on call (`def _root(): return resolve_root(__file__)`), don't freeze it in a module constant or a class field — the value would be taken before the entry point names the root |
| map is stale | run `.venv/bin/python scripts/layout_map.py` |

```bash
.venv/bin/python scripts/layout_map.py           # regenerate the map
.venv/bin/python scripts/layout_map.py --check   # what CI runs, as an exit code
.venv/bin/python scripts/layout_map.py --regen   # allowlist from the measurement
.venv/bin/python scripts/layout_map.py --report  # the seams, for planning work
```

Every entry that grants an exception — an edge against the arrows, a manual
entry point, a module allowed to derive the root — requires a ticket or a
written reason, and the guard compares the artifact with the code **in both
directions**: an entry that no longer matches reality is just as red as a
violation that is not declared. The list can only shrink by itself; it grows
only through a diff a human wrote and a reviewer read.

The `KINDS` table in the guard is pinned by a copy inside the test on purpose —
the comment there explains why. Changing the policy means changing two files,
and that is the point.

iOS tests live in the nightly run on purpose: the simulator takes a while
to boot, and keeping that in the fast PR check would teach everyone to wait.
At night there is time.

The scenarios tap Russian labels while the runner lives in an English locale,
so UI tests launch the app with `-ui.language ru` — the same key a person uses
to pick the language in settings. Not by changing the simulator's locale: this
way the test checks the app rather than the runner image, and stays honest on a
machine with any language. Unit tests compare against `L.t` instead of Russian
literals: the test is about behaviour, not about the interface language.

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

## Run contracts and preflight: checked by running, not by reading

Every executable file — `scripts/layout_map.py` knows which ones: python with a
real `__main__` guard, shell scripts — carries a **run contract** in
`docs/design/layout.json` (`run_contracts`). `help`: `--help` with an isolated
data root exits 0. `refuse`: started without a named data root it refuses with
`exit_codes.EXIT_ROOT_UNNAMED` and prints the recipe, before touching the disk.
`none`: there is no safe probe (a stdio server, a daemon without argparse, a
shell script); the file is not run and the reason stays in the map as visible
debt. `tests/test_entry_points_contract.py` runs each entry point as a process
and checks the contract — in CI and under the mutation check alike. A new
executable gets its contract from `scripts/layout_map.py --regen` (derived from
the code); turning it into `none`, or back, is a human decision. Run `--regen`
first when you add an entry point: the layout guard is red until the contract
exists.

`scripts/preflight.sh [base]` is the local summary before a review round and
before accepting a contributor's (or a sandboxed executor's) work: the machine
is busy (a live meeting stops it; `PREFLIGHT_FORCE=1` only with the owner's
consent), ruff, the layout guard, privacy markers, the full pytest set,
`swift build`/`swift test` when `app/` is touched, and the mutation check on
the changed lines. It prints a machine verdict — `preflight: ok` or
`FAIL: <steps>` — with the names of failed tests. `PREFLIGHT_SKIP=mutation,swift`
skips steps on a re-run. It works inside a git worktree: the owner's data root
comes from the main checkout, so the busy guard still sees a live meeting.

Why this exists: five review findings in a row were claims about process
behaviour ("exits with code 2", "the app shows the recipe") that nobody had
run. What closes that class is a test that runs the process — not a comment
that describes it, and not a shell step that nothing runs.
