# Scripts

*[**English**] · [Русский](../docs/ru/scripts/README.md) · [中文](../docs/zh/scripts/README.md)*

Operational helpers. All local, all optional.

- `doctor.py` — one command that shows what's missing (venv, models, config, Ollama) and how to fix it.
- `import_meeting.py` — import a recorded meeting (audio / text / Zoom subtitles) into the archive and the graph; `note_`/`diary_` audio goes to the notes pipeline. Folder scans wait until a WAV reaches the complete size declared by its RIFF header, so a syncing recording is never transcribed halfway.
- `protocol.py` — a participant-safe protocol from Summary and Minutes; strips wiki syntax and never includes the raw transcript. Supports `--style plain`, `--copy` and `--out`.
- `rename_meeting.py` — rename a meeting coherently across transcripts, archive, graph note and app status. Dry-run by default; `--yes` applies.
- `forget_meeting.py` — remove one meeting from its transcript, recording, archive and graph references. Dry-run by default; `--yes` applies and backs up surviving edited nodes.
- `dedup_archive.py` — consolidate historical duplicate archive folders. Dry-run by default; `--apply` parks extras under `Встречи-архив/_дубли/` rather than deleting them.
- `dedup_graph.py` — replace byte-identical archive copies with hard links when explicitly enabled. Editing either path then edits the same file, so read the report before applying.
- `merge_graphs.py` — merge a split-off graph back into the main one: new files move, Markdown name collisions get appended as a "moved from" section (donor frontmatter stripped), meeting lines migrate into the receiver's `_MOC.md`, the donor `_MOC.md` becomes a "merged into" note. Dry-run by default; `--apply` first validates the whole plan, refuses binary and non-Markdown collisions, creates a recovery backup and rolls partial failures back.
- `memory_bench.py` — benchmark the whole retrieval loop on the demo graph (`--demo`, `--demo-en`).
- `tier3_cores.py` — core revision (duplicate merge) with `--apply`; without it, dry-run.
- `nightly_dossier.py` — incrementally rebuild topic dossiers or inspect retrieval with `--find`.
- `morning_brief.py` — assemble the morning brief from the graph and the nightly review.
- `nightly.sh`, `nightly_claude_cores.py` — the night cycle: tier3 + an optional cloud review of graph cores (off unless the cloud layer is enabled).
- `nightly_dossier_review.py` — the cloud pass over dossiers the local model wrote: Opus sees what a retelling misses (a decision overruled later, an expired deadline, two nodes disagreeing). `--dry` shows without writing.
- `cloud_review.py` — runs the cloud debrief of a meeting with a timeout and explicit limits, instead of firing `claude` into the background and calling it done. A crash or a truncated answer no longer leaves a review file that merely looks real.
- `get_models.py` — models in one command: `--diar` (diarization embeddings, without it live per-voice labels stay off), `--segmentation`, `--stt sensevoice` (Chinese recognition, 228 MB). Also `--list`, `--check`, `--url`.
- `diar_bench.py` — DER for diarization: the share of speech time labelled wrongly. `--make` builds a synthetic fixture locally, because no meeting recordings can live in this repository.
- `stt_bench.py` — CER for recognition: the share of characters that came back wrong. `--compare` runs SenseVoice against Whisper on the same synthetic phrases. Same caveat as diarization: synthesized speech is cleaner than live, so this is a floor, not a benchmark.
- `fix_action_items.py` — a one-off normalization of action-item formatting in minutes written before the daemon started normalizing them; only the format changes. Dry-run by default.
- `check_private_markers.py` — the de-identification guard (a pre-commit hook): checks both the added lines and the whole tracked tree, prints places and never the marker itself. See [Contributing](../CONTRIBUTING.md).
- `build_embedded_python.sh` — assembles the portable python runtime that ships inside `Charoite.app`; run it before `app/make_app.sh` when building your own bundle.
- `build_app_icon.sh` — macOS app icon from the Icon Composer document `app/Resources/AppIcon.icon`: `actool` (Xcode 26+) produces `Assets.car` for macOS 26 (without it Tahoe draws the old `.icns` inside a grey tile) and the legacy `AppIcon.icns` for macOS ≤ 15; both are committed, CI on macos-15 does not rebuild them.

Run dependency-bearing scripts with `.venv/bin/python`; `doctor.py` is the
intentional exception and works with system `python3` before installation.
The task-oriented workflow and recovery order are documented in
[User guide](../docs/USER_GUIDE.md) and
[Data and recovery](../docs/DATA_AND_RECOVERY.md).
