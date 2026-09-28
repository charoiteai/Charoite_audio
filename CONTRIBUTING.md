# Contributing to Charoite

***English** · [Русский](docs/ru/CONTRIBUTING.md) · [中文](docs/zh/CONTRIBUTING.md)*

Thanks for your interest! Charoite is a fully local meeting assistant —
contributions that keep it local-first are very welcome.

How the project is maintained day to day — the AI-maintainer pipeline,
review gates, and who answers for what — is documented in
[MAINTENANCE.md](MAINTENANCE.md).

## Ground rules

- **Local-first is non-negotiable.** No cloud calls, no telemetry, no
  accounts. The only network targets are localhost (Ollama or
  `mlx_lm.server`, the optional STT stream server and memory companion) — the
  opt-in cloud layer
  (the Claude CLI and the cloud chat engine) is the exception, stays off by
  default, and every exit that can carry meeting data asks `src/privacy.py`.
  The two requests that are not the cloud layer — the version check and
  model downloads — are named in [PRIVACY.md](PRIVACY.md); a new one needs
  its line there.
- **Russian first, three interface languages.** The app speaks Russian,
  English and Chinese: the language is `sufler.language`, and each UI string
  is written where it is used, as a triple — `L.t(ru, en, zh)` takes all
  three. Meeting documents (hints, minutes, graph values) follow the same
  key — Russian, English or Chinese; STT is strongest in Russian (GigaAM),
  English goes through Parakeet or Whisper, Chinese through SenseVoice or
  Whisper. Identifiers are English;
  comments and commit messages in this repository are mostly Russian, and
  English is just as welcome.
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
  minutes. The mutation lands in a separate copy of the repository — a local
  clone checked out at the head of the range — so test subprocesses see the
  same broken code the imports do. The range is resolved to commit hashes once,
  before the plan, and the copy never writes to the source repository: a
  `git worktree` registry lives in the shared `.git`, and parallel runs broke
  each other's `add` and `remove`. Each mutant is judged by the tests
  that reach its module: `import X`, a subprocess running `X.py`, or a load
  by path (`spec_from_file_location("X", …)`, the `_load("X")` helper). A
  module no test reaches that way is judged by the whole suite — slow in CI,
  and locally the baseline may not fit the time limit, so the run refuses.
  An equivalent mutant (a threshold
  that both code and test read from one constant) need not be fixed — but is
  worth a look: on 20.08 one such survivor revealed that behaviour exactly at
  the threshold was tested by nobody.
  A mutant whose text does not parse back into the mutated tree is reported
  as NOT APPLIED with its reason, never as killed. Changed lines with nothing
  to mutate end with `unmutable` (code 8) and print the plan counters — files,
  lines, module constants, AST nodes, unreadable files; an empty range stays
  `nothing` (code 6).
  `--budget-s N` caps the whole run, counted from start: before each baseline
  suite the run needs 4 × `--timeout` left, before each mutant the measured
  duration of its suite. Short of it, the run stops with the tool's own line
  `прервано: бюджет` ("interrupted: budget"; `--force` does not lift it) and
  ends as `partial`, or as `unjudged` (code 9) when not a single mutant of a
  non-empty plan was judged — in CI that one is red. `--report` is rewritten
  after every mutant, so a job killed at its ceiling still leaves what was
  checked.
  In CI the changed lines are split across four shards: `--shard K/N` keeps
  every N-th mutant (`i % N == K-1`, before `--max`), `--max all` lifts the
  60-mutant ceiling, and each shard writes a machine line `<report>.json`
  beside the report — `K`, `N`, `M` (its mutants), `P` (the whole plan) and
  the outcome word. A shard left with no mutants of a non-empty plan prints
  `шард K из N: 0 из P — нечего` and still writes both files; at `P = 0` every
  shard writes the diagnosis of the whole range. A separate verdict job merges
  them with `--merge-shards DIR`; its table lives in the `merge_shards`
  docstring. In short: red when the files do not cover the plan exactly (one
  per shard, keys 1..N, ΣM = P, every file readable); at `P = 0` all-`nothing`
  is the note "nothing to mutate" and `nothing`/`unmutable` the blind-spot
  warning, both green; at `P > 0` green only when every shard is `ok` — or
  `nothing` with its own `M = 0`; `partial`, `unjudged` and `fail` are red. So
  `partial` in CI is a failure now, not a warning. A file that does not parse
  counts as unread, like one missing from the revision.
  Locally `--jobs N` (1–4, only with `--max all`) runs the same split on one
  machine: the parent starts N ordinary mutators with `--shard k/N` on the
  range resolved to hashes once and judges them with `merge_shards` — the
  table above, codes 0/1. A finite `--max` is refused: a cap applied after
  the split judges a different set than a sequential run with the same cap.
  Parallel runs are allowed: the mutation lock is shared, and four shares on
  one `.git` found the same survivors 3.3× faster (193 s → 59 s). Shares'
  logs and reports stay in the `mutate-jobs-*` directory the parent prints;
  SIGINT or SIGTERM to the parent reaches every share, which cleans up its
  copy before exiting.
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
- **Tests run sealed.** Autouse fixtures in `tests/conftest.py` give every
  test a temporary data root, so no run touches the owner's live data, and
  close the network: a request fails the test with `pytest.fail` (which an
  `except Exception` around the call cannot swallow). "Ollama is down" is
  the default answer of that guard, not a stubbed method; a test that needs
  models declares `@pytest.mark.ollama_отвечает("model", …)`, and a test that
  truly needs a socket asks for `сеть_разрешена`. A crash in a background
  thread fails the run (`filterwarnings` in `pyproject.toml`), and each test
  has a 120-second ceiling.
- **No pattern blacklists.** Classification decisions go through the
  local model, not through hardcoded word lists — patterns rot, models
  understand context.

## Workflow

1. Fork, branch from `main`: `feat/…`, `fix/…`, `docs/…`.
2. Conventional commits (`feat(app): …`, `fix(daemon): …`).
3. Before review, `scripts/preflight.sh` (see the last section): ruff, the
   layout guard, privacy markers, the full pytest set, Swift when `app/` is
   touched, the mutation check on the changed lines. For app changes that
   means `swift build` clean and `swift test --filter '^CharoiteAppTests\.'`
   green (live probes run only by hand); run `scripts/memory_bench.py` if
   you touch search or prompts.
4. **Update docs in the same PR** — CI blocks a PR that changes code
   (`src/`, `scripts/`, `app/`, `app-ios/`, `app-android/`) and leaves the
   documentation untouched: `docs/`, `README.md` (root or of those folders),
   `PRIVACY.md`, `SECURITY.md`, `ROADMAP.md` or `CONTRIBUTING.md`.
   `CHANGELOG.md` does not count — release-please owns it. Label
   `skip-docs` for purely technical changes; dependency bumps
   (`libs.versions.toml`, the Gradle wrapper, `Package.resolved`) are exempt
   by themselves.
5. The PR title is a conventional commit — with squash merge it becomes the
   commit on `main` ([RELEASING](docs/RELEASING.md)). PR description (the
   template asks for it): the intent, the invariants that must not break,
   the area of impact, and how it was checked — for screen or sound, what
   you looked at and listened to on a real device.

### What CI checks

| When | What |
|---|---|
| every push and PR | `lint`: ruff, byte-compile, "a test must be able to fail", shellcheck, semgrep, mypy (advisory), example config keys · `pytest (src/)`: the full python suite in four processes (`-n 4 --dist loadgroup`), then the layout gate |
| pushes to `main` and every PR | CodeQL (`analyze`, also weekly) · supply chain: zizmor on the workflows and the public-format de-identification check |
| every PR only | mutation of the changed lines in four shards (`mutation (changed lines)`) and their verdict (`mutation verdict`) · docs guard · conventional PR title · dependency review (high severity fails) |
| when `app/`, `app-ios/` or `app-android/` change | Swift app build and deterministic tests with SwiftLint, iOS build · Android unit tests, lint and debug build |
| nightly | the same python and Swift tests on macOS plus **iOS tests in the simulator** |

Only `lint` and `pytest (src/)` block a merge; why the rest is advisory is in
[RELEASING](docs/RELEASING.md), "Branch protection". A red advisory check is
still read before merging.

Lint rules live in one place, `[tool.ruff.lint]` of the root `pyproject.toml`;
CI, pre-commit and `scripts/preflight.sh` call `ruff check <paths>` without flags, and
a test keeps it that way. Besides errors (`E9`, `F`), a broad `except Exception` or
`except BaseException` that swallows the error (no `raise` in the handler) is gated:
narrow the class, or mark it `# noqa: BLE001 — <reason>`; a bare `except:` is not
allowed. Preflight runs the ruff version pinned in CI (from the venv when it matches,
otherwise via pipx or uvx); if that engine cannot start, the step is reported as
skipped, not as failed.

iOS tests live in the nightly run on purpose: the simulator takes a while
to boot, and keeping that in the fast PR check would teach everyone to wait.
At night there is time.

The scenarios tap Russian labels while the runner lives in an English locale,
so UI tests launch the app with `-ui.language ru` — the same key a person uses
to pick the language in settings. Not by changing the simulator's locale: this
way the test checks the app rather than the runner image, and stays honest on a
machine with any language. Unit tests compare against `L.t` instead of Russian
literals: the test is about behaviour, not about the interface language.

### The layout guard

`docs/design/layout.json` is the single source of truth for how `src/` is laid
out: which layer a module belongs to, which import edges point the wrong way,
which files are entry points, and which modules may derive the data root
themselves. `docs/design/layout.md` is a generated map of the same facts — it is
never edited by hand.

The bottom of the stack is split in two. **base** holds pure helpers that know
nothing about the machine they run on (`frontmatter`, `redirects`, `safe_write`,
`model_seam`, `exit_codes`, `media_meta`, `vocabulary`, `file_locks`,
`task_line`); **runtime** holds the application's environment — data and code
roots, config, the live gate, privacy, the interpreter recipe (`charoite_paths`,
`config_loader`, `live_gate`, `privacy`, `deps`). The runtime layer is not
named in the guard: it is whichever layer holds the roots canon
(`src/charoite_paths.py`). The **graph** layer may import only base, so the
graph search can be installed without the app; `graphs`, the door that
assembles the cache directory and the night window from the environment, lives
in **meeting**. llm, cloud and audio see base and runtime; meeting and app see
everything below them.

**The environment gate.** A module of a layer that `allowed` does not give the
runtime layer (today: base and graph) has no edge into runtime — `allowed_edges`
cannot excuse one — and none of the environment shapes of `ROOT_SHAPES` with
scope `layer`: any environment access (`os.environ`, `getenv`, `expandvars`,
`tempfile` without `dir=`, which reads `TMPDIR`), `Path.home()` / `expanduser`,
`__file__` (and `__spec__`, `inspect.getfile`), any touch of `sys.path` at
import (`site.addsitedir` included), a dynamic import. A name is resolved
through the module's imports (`import sys as s`, `from importlib import
import_module as im`), and any mention counts, not only a call
(`loader = importlib.import_module`); `tempfile` is judged at the call. Matching
by name segment is conservative on purpose: a namesake such as `self.home` is a
reason to rename. `root_exemptions` cannot excuse these shapes — the artifact is
rejected at load — and runtime reached through a neighbour (graph → an
`allowed_edges` debt → runtime) is as red as a direct edge. The tables of this
grammar are pinned by an approved copy in `tests/test_import_boundaries.py`.
It is a grammar, not every possible way, like the root rule's `_env_reads`:
binding by assignment (`S = sys`), `getattr`, `exec` and implicit readers other
than `tempfile` (`getpass.getuser`, `shutil.which`) are not recognised. What the
grammar cannot see, the package probe below sees by behaviour. The fix is
derived from the same `allowed`: the path comes in as a parameter, and the
caller from a layer that sees runtime assembles it — the way
`graphs.open_search` does — never "go to the roots canon". The graph package is
the import closure of one declared entry, `package_entry` in `layout.json`
(`graph_search`), not "all of base plus graph"; `tests/test_entry_points_contract.py`
copies that closure into a temporary directory and runs a search over the demo
graph in a separate process with `HOME`, `CHAROITE_ROOT`, `SUFLER_GRAPH_DIR`,
`CHAROITE_GRAPH_DIR` and `TMPDIR` pointing into a trap and an audit hook that
fails on reading the trap or writing outside `data_dir`. A deterministic fake
embedder makes the package write its vector cache and read it back, so the
write path is exercised, not only the lexical search. After the run the probe
compares `sys.modules` and `sys.path` with the state before the import, so a
dependency leak or a change to the import path is caught in any spelling. The probe's self-check
builds one leaky package per element of the probe's own tables — each poisoned
variable, each file-system mutation event, each variable the isolation drops —
so a new element without its case turns the test red; the tables themselves
are pinned by an approved copy from the task, so shrinking one is red too.

If a PR turns that check red, the message names the fix. The usual cases:

| Message | What it means |
|---|---|
| new edge against the arrows | the import crosses a layer boundary — untangle it, or add it to `allowed_edges` **with a ticket** |
| `allowed_edges` holds X → Y, but that edge is gone | the debt was paid, remove the entry |
| field X is not declared in `_SCHEMA` | the artifact's fields are declared in code, each with a class — `measured`, `seed` or `decision`; add the declaration (and its snapshot `APPROVED_FIELDS` in `tests/test_import_boundaries.py`) instead of writing the key into the JSON |
| file derives the root itself | take it from `src/charoite_paths.py` instead of re-parsing `CHAROITE_ROOT` or walking up from `__file__` |
| file remembers the canon's answer at import | ask on call (`def _root(): return resolve_root(__file__)`), don't freeze it in a module constant or a class field — the value would be taken before the entry point names the root |
| edge into the environment layer / module of layer X touches the environment | layer X has no environment: pass the path or setting in as a parameter and assemble it in a door on a layer that sees runtime (like `graphs.open_search`); `allowed_edges` cannot excuse it and `root_exemptions` refuses these shapes at load |
| package X pulls module Y | the closure of `package_entry` reached a layer with the environment — cut the import, the package must install without the app |
| map is stale | run `.venv/bin/python scripts/layout_map.py` |
| `✗ scripts/layout_map.py: …` instead of `✗ docs/design/layout.json: …` | the defect is in a table of the guard's own code (`ROOT_SHAPES`, the scope table `SHAPE_SCOPES`, `PROBLEM_KINDS`), not in the artifact — fix the code |

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

**The folder-name guard.** The storage schema is one value:
`charoite_graph.graph_schema.GraphSchema` declares the folder names, section
heads and raw-file markers with invariants, and `src/charoite_schema.py` holds
the single `CHAROITE = GraphSchema(…)` value as literals. The guard in
`scripts/layout_map.py` reads the field list from the class annotations and the
values from that call, looks for copies of those names among the string literals
of the graph package (the same closure the package probe copies) and holds each
copy as `folder_literals` debt with a ticket; `folder_literal_exemptions` forgives
one copy with a written reason. A hit without a ticket, or a declared hit the
measurement no longer finds, is red. Since PR B of №422 the debt is zero: the
package asks the schema's role predicates and keeps no name constants, and
`tests/test_graph_schema_roles.py` rotates the schema (every name of `CHAROITE`
becomes an ASCII token) and requires the same observable behaviour of search,
dossier and node index — a literal that survived shows up as a mismatch there
before the guard even runs. One copy and one registry per hit: a hit that is
both debt and forgiven is refused at load. The schema module is an ordinary
member of the entry closure: the search takes the schema as a parameter.

The `KINDS` table in the guard is pinned by a copy inside the test on purpose —
the comment there explains why. Changing the policy means changing two files,
and that is the point.

## Where to start

- [ROADMAP.md](ROADMAP.md) — what we plan next
- Issues labeled `good first issue`
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the daemon,
  diarization and the graph pipeline fit together

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
outside CI and skips in CI (`CI` set). Outside CI it also refuses a commit
whose `git config user.email` is not the maintainer's address — the public
repository is committed to under one identity. Both checks run on any machine
that installed the hook, so a contributor who cannot have the list is stopped
by this hook locally; pre-commit's own `SKIP=private-markers` lets such a
commit through, and the CI check below still runs on the pull request.

**The second line in CI goes by format, not by name.** The hook guards only
the machine where it is installed: a commit through the web UI, a fresh clone
without `pre-commit install` or someone's fork passes it by. So CI runs
`check_private_markers.py --public-only`: it looks for internal hosts, emails
on non-public domains, personal paths and surnames with initials — formats
that give nothing away by themselves but catch the most common leak, a pasted
piece of config, log or path from a work machine.

A colleague's name in a comment is caught **only by the local hook**. Install
it — `pre-commit install`, once per clone.

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
`exit_codes.EXIT_ROOT_UNNAMED` and prints the recipe — the code is deliberately
not 2, which argparse uses for a bad flag. `none`: there is no safe probe (a
stdio server, a daemon without argparse, a shell script); the file is not run,
and the record carries the tracker card in its own `ticket` field (`№…` at the
start, a note may follow the number) — debt with an owner, not a coverage
figure. `docs/design/layout.md` lists that debt by card, together with the
upward edges. `tests/test_entry_points_contract.py` runs each entry point as a process
and checks the contract — in CI and under the mutation check alike. A new
executable gets its contract from `scripts/layout_map.py --regen` when the code
proves one (`parse_args` → `help`, the root constructor → `refuse`); `none` is
never written by the machine — a human writes it, with the card. Run `--regen`
first when you add an entry point: the layout guard is red until the contract
exists.

`scripts/preflight.sh [base]` (range `base...HEAD`, `origin/main` by default)
is the local summary before a review round and before accepting a
contributor's (or a sandboxed executor's) work: the machine is busy (a live
meeting, a debrief, the nightly cycle or a running mutator stops it with exit
code 3; `PREFLIGHT_FORCE=1` only with the owner's consent), ruff at the version
pinned in CI, the layout guard, privacy markers (`--all`), the full pytest set,
SwiftLint plus `swift build`/`swift test` when `app/` is touched, and the
mutation check on the changed lines. The last line is the machine verdict:
`ok` when every step ran and passed, `FAIL: <steps>` (with the names of
failed tests above it), or `неполный — пропущены: …` ("incomplete —
skipped") when a step could not run — a skip is said out loud, never counted
as a pass.
`PREFLIGHT_SKIP=mutation,swift` skips steps on a re-run. The full pytest set
runs in `PREFLIGHT_JOBS` processes (4 by default) when pytest-xdist is
installed, the same mode as CI; without it the set runs sequentially and the
step says so with the install command — a slower run, not a skipped one. It
works inside a git worktree: the owner's data root comes from the main
checkout, so the busy guard still sees a live meeting.

Why this exists: five review findings in a row were claims about process
behaviour ("exits with code 2", "the app shows the recipe") that nobody had
run. What closes that class is a test that runs the process — not a comment
that describes it, and not a shell step that nothing runs.
