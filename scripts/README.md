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

Run dependency-bearing scripts with `.venv/bin/python`; `doctor.py` is the
intentional exception and works with system `python3` before installation.
The task-oriented workflow and recovery order are documented in
[User guide](../docs/USER_GUIDE.md) and
[Data and recovery](../docs/DATA_AND_RECOVERY.md).
