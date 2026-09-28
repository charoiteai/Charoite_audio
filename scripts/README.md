# Scripts

*[**English**] · [Русский](../docs/ru/scripts/README.md) · [中文](../docs/zh/scripts/README.md)*

Operational helpers. All local, all optional.

Run dependency-bearing scripts with `.venv/bin/python`; `doctor.py` is the
intentional exception and works with system `python3` before installation.
The data root (recordings, transcripts, logs) is `CHAROITE_ROOT`, or the
repository itself when it is unset. `import_meeting.py` insists on it: without
`CHAROITE_ROOT` it refuses with exit code 5 and prints the recipe, so run it as
`CHAROITE_ROOT="$PWD" .venv/bin/python scripts/import_meeting.py …` from a clone.
The task-oriented workflow and recovery order are documented in
[User guide](../docs/USER_GUIDE.md) and
[Data and recovery](../docs/DATA_AND_RECOVERY.md).

## Everyday

- `doctor.py` — one command that shows what's missing and how to fix it: the install half (config and its keys, the graph folder, Ollama and the required models including bge-m3, STT models, diarization, dependencies) and the runtime half (a real generation probe, stuck meetings, the import queue, free disk space). It fixes nothing by itself. `--restart-llm` is the emergency restart of the model server over live leases — the only manual way out when the port owner hangs.
- `import_meeting.py` — import a recorded meeting (audio / text / Zoom or Teams subtitles) into the archive and the graph; `note_`/`diary_` audio goes to the notes pipeline. `--date`/`--time`/`--title` override what the recording says about itself. `--scan <folder>` imports a whole import folder: successes move to `done/` with a sidecar, failures stay marked until `--retry-failed`; `--prune` only deletes `done/` copies older than `audio.import_keep_days`. Folder scans wait until a WAV reaches the complete size declared by its RIFF header, so a syncing recording is never transcribed halfway. Re-importing the same recording (same file name and size) lands on the existing meeting instead of creating a second one. A phone recording's `<file>.json` stop manifest travels with the audio and becomes a recording-trace event on the Mac.
- `protocol.py` — a participant-safe protocol from Summary and Minutes; strips wiki syntax and never includes the raw transcript. The latest meeting by default, or a date / part of the folder name. Supports `--style plain`, `--copy`, `--out` and `--graph`.
- `rename_meeting.py` — rename a meeting coherently across transcripts, archive, graph note and app status. Dry-run by default; `--yes` applies.
- `forget_meeting.py` — remove one meeting from its transcript, recording, archive, graph references and cloud-review snapshots. Dry-run by default; `--yes` applies and backs up surviving edited nodes. `--keep-graph` touches only the transcript and the recording; `--import-folder` also removes the import copy in `done/`.

## Graph upkeep

- `graph_doctor.py` — the graph's health as memory, a deterministic lint without a model: broken `[[links]]` (active folders and the archive counted separately), links broken by a newline, diarization labels among People, orphans, same-name nodes in different folders, near-duplicate names, `_MOC.md` coverage, freshness. Changes nothing; writes a JSON report to `logs/graph_doctor.json` in the data root (the morning brief reads it). `--all-graphs` (the nightly step), `--examples N`, `--strict` (exit 1 on warnings).
- `dedup_archive.py` — consolidate historical duplicate archive folders. Dry-run by default; `--apply` parks extras under `Встречи-архив/_дубли/` rather than deleting them.
- `dedup_graph.py` — two rules, each with its own permission. `--apply-copies` / `sufler.dedup_copies` moves iCloud conflict copies (`Имя 2.md` byte-identical to `Имя.md`) out of the graph into `backups/<graph>-<hash>/dedup_copies/` with a manifest; `--apply` / `sufler.dedup_files` replaces byte-identical archive copies with hard links, so editing either path edits the same file. `--all-graphs` walks every graph the doctor checks (the nightly step does). Without flags it only reports.
- `merge_graphs.py` — merge a split-off graph back into the main one: new files move, Markdown name collisions get appended as a "moved from" section (donor frontmatter stripped), meeting lines migrate into the receiver's `_MOC.md`, the donor `_MOC.md` becomes a "merged into" note. Dry-run by default; `--apply` first validates the whole plan, refuses binary and non-Markdown collisions, creates a recovery backup and rolls partial failures back.
- `fix_action_items.py` — a one-off normalization of action-item formatting in minutes written before the daemon started normalizing them; only the format changes, and a status already set (done `[x]`, cancelled `[-]`, reopened by hand, any other mark in the box) stays as it is; a file whose rewrite would change a status is skipped and named, exit code 1. Dry-run by default; `--apply` needs `CHAROITE_ROOT`, writes under the shared graph lock and keeps the originals with a manifest in the graph backups.
- `migrate_placeholders.py` — clears accumulated diarization-label nodes (`Люди/Собеседник N`), which glued different people from different meetings into one: every link to such a node becomes plain text, the node moves into a backup with a manifest, `Люди/_ЛЮДИ.md` is rebuilt. Plan only by default; `--apply --backup DIR` needs a backup folder outside the graph and an explicit data root (`--root` or `CHAROITE_ROOT`), takes the shared graph lock and refuses during a live meeting (exit 3).

## Night cycle and cloud passes

- `nightly.sh` — the night cycle (the launchd job the app installs from Settings, 04:15): waits for the machine to be idle, graph doctor, file dedup, the hint-memory index, an early morning brief, tier3 core review, dossiers, the optional cloud passes, then the brief again over the tidied graph and the memory bench. Steps are independent — a failed one marks the night failed without cancelling the brief. `NIGHTLY_MAX_H` (default 4) caps the whole night.
- `wait_for_idle.py` — waits until no meeting is being recorded or processed and no mutation run is going, because the night cycle and meeting processing both drive the local model and do not fit on one machine together. Waits with a ceiling (`--timeout`, default an hour; `0` does not wait) and always exits 0 — the log says whether it got the machine.
- `tier3_cores.py` — core revision (duplicates and nesting in `Ядра`). Without flags it only reports; `--mark` writes reversible "possible duplicate" marks; `--apply` also merges confident duplicates (backups kept). `--auto` is the nightly mode: merges only with `sufler.tier3_auto_apply: true`, otherwise marks; `--since-last` judges only cores changed since the previous run.
- `nightly_dossier.py` — incrementally rebuild topic dossiers (`--full` rebuilds all, `--dry` shows the plan, `--limit` caps topics per run) or inspect retrieval with `--find`.
- `nightly_dossier_review.py` — the cloud pass over dossiers the local model wrote: Opus sees what a retelling misses (a decision overruled later, an expired deadline, two nodes disagreeing). Edits only with `sufler.cloud_edit_graph`, otherwise writes recommendations; `--dry` shows without writing.
- `nightly_claude_cores.py` — the cloud review of graph cores: changes nothing, writes a recommendations report into the graph. Silent unless the cloud layer is enabled (`sufler.cloud_enrich`) and no kill switch is set.
- `morning_brief.py` — assemble the morning brief (`_Сегодня.md`) from ready graph lines, the nightly review marks and the graph doctor report, without a model.
- `graph_search_index.py` — the vector index behind in-meeting memory (`src/charoite_graph/graph_search.py`): changed graph blocks → bge-m3 via Ollama, cache in `data/graph_search/`. Runs outside live recording — at night and briefly after a meeting; during a meeting the daemon only embeds the query. `--budget-s` caps the time, `--stats` shows the index state, `--force` indexes even while recording.
- `cloud_review.py` — runs the cloud debrief of a meeting with a timeout and explicit limits, instead of firing `claude` into the background and calling it done. A crash or a truncated answer no longer leaves a review file that merely looks real.

## Models and measurement

- `get_models.py` — models in one command: `--diar` (diarization embeddings, without it live per-voice labels stay off; `--model` picks `eres2net-base`, `eres2net-en` or `eres2netv2`), `--segmentation`, `--stt sensevoice` (Chinese recognition, 228 MB). Also `--list`, `--check` (no network), `--url`, `--dest`.
- `memory_bench.py` — benchmark the whole retrieval loop on reference questions from `config/memory_bench.yaml`, or on the demo graph (`--demo`, `--demo-en`, `--demo-zh`). `--stats` skips synthesis and prints coverage and gate verdicts per case.
- `diar_bench.py` — DER for diarization: the share of speech time labelled wrongly. `--make` builds a synthetic fixture locally, because no meeting recordings can live in this repository; `--crosstalk` adds overlapping speech, `--wav`/`--truth` measure your own recording, `--engine compare` puts the current engines against the experimental Nemotron 3 Diarization ([details](../docs/DIARIZATION.md#crosstalk-and-the-nemotron-experiment)).
- `stt_bench.py` — CER for recognition: the share of characters that came back wrong. `--compare` runs SenseVoice against Whisper on the same synthetic phrases. Same caveat as diarization: synthesized speech is cleaner than live, so this is a floor, not a benchmark.
- `bench_models.py` — compares Ollama models on our own loads rather than synthetic tok/s: a short hint, a mid-size extraction and a long real transcript, time to first token and to the end, the cold first run shown separately.
- `bench_extract.py` — compares models on meeting analysis quality rather than speed: the same `graph_updater.extract` on the same transcripts (the 3 latest, `--meetings N` or `--files`), checking what fails silently — quotes that are not in the transcript, HH:MM times that are not there, how much was extracted, whether the JSON parses. Raw answers go to `logs/bench_extract/` for a human to read.
- `gate_bench.py` — measures the decision gate (`src/decision_gate.py`): per confidence threshold, how many empty model calls it would remove and how many real questions it would lose. `harvest` collects candidates from transcripts for hand labelling, `eval` scores a labelled JSONL, `shadow` reads the gate's shadow lines from the daemon log (`sufler.decision_gate_shadow: true`).

Benchmarks that drive a model need an idle machine: during a live meeting they measure the queue, not the model.

## Build and release

- `build_embedded_python.sh` — assembles the portable python runtime that ships inside `Charoite.app` from the hashed `requirements-runtime.lock`; run it before `app/make_app.sh` when building your own bundle.
- `lock_runtime_deps.py` — rebuilds `requirements-runtime.lock` with versions and hashes from the ranges in `pyproject.toml`, so the signed bundle gets exactly what was reviewed, not whatever PyPI served at build time. Needs `uv`; the result is committed.
- `build_app_icon.sh` — macOS app icon from the Icon Composer document `app/Resources/AppIcon.icon`: `actool` (Xcode 26+) produces `Assets.car` for macOS 26 (without it Tahoe draws the old `.icns` inside a grey tile) and the legacy `AppIcon.icns` for macOS ≤ 15; both are committed, CI on macos-15 does not rebuild them.
- `make_dmg.sh` — the `Charoite.dmg` installer from a built `app/build/Charoite.app` (a window with the app and an Applications link), signed with the same Developer ID when one is present, plus `.sha256` files for the DMG and the zip, which the in-app updater verifies.
- `notarize.sh` — submits a zip or DMG to Apple notarization with an App Store Connect API key, waits for the verdict, prints Apple's log on rejection and staples the ticket. Used by the release workflow.
- `sign_release_manifest.py` — signs a release's `.sha256` manifest with the owner's ed25519 key (it never lives in CI) and attaches the signature to the release; the app requires it before replacing its bundle. `--file` signs a local file without `gh`. The step is in [Releasing](../docs/RELEASING.md).

## Development gates

- `check_private_markers.py` — the de-identification guard (a pre-commit hook): checks both the added lines and the whole tracked tree, prints places and never the marker itself. `--all` scans the tree only; `--public-only` is the CI mode with public patterns. See [Contributing](../CONTRIBUTING.md).
- `check_test_assertions.py` — a test must be able to fail: flags `test_*` bodies with nothing that can fail (no `assert`, `pytest.raises`/`fail`/`warns`, `self.assert*`, `raise …Error`) and assertions placed after a `return`. Runs in CI and as a pre-commit hook; paths default to `tests/`.
- `mutate_check.py` — puts defects back into the lines changed in `--range` (default `origin/main...HEAD`) inside a separate git worktree and demands that the tests go red. Refuses while the machine is busy with a meeting, processing or the night (`--force` overrides that, not another mutator's lock); `--budget-s` caps the run, `--report` is rewritten after every mutant. CI runs it on each PR's changed lines.
- `layout_map.py` — the code layout: layers, import edges, entry points and every executable path the code and prose name, checked against `docs/design/layout.json`. Without flags it writes the map `docs/design/layout.md`; `--check` is the gate (CI), `--regen` refreshes the allowlist and run contracts from the code, `--report` measures the seams.
- `preflight.sh` — the local summary before a review round: machine busy, ruff, the layout gate, privacy markers, the full pytest set, Swift when `app/` changed, the mutation check on the range. Prints `preflight: ok` or `FAIL: <steps>`; `PREFLIGHT_SKIP=mutation,swift` skips steps on a re-run.

Details of the gates — run contracts, exit codes, the mutation budget — are in
[Contributing](../CONTRIBUTING.md).
