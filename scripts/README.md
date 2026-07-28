# Scripts

*[**English**] · [Русский](README.ru.md) · [中文](README.zh.md)*

Operational helpers. All local, all optional.

- `doctor.py` — one command that shows what's missing (venv, models, config, Ollama) and how to fix it.
- `import_meeting.py` — import a recorded meeting (audio / text / Zoom subtitles) into the archive and the graph; `note_`/`diary_` audio goes to the notes pipeline.
- `memory_bench.py` — benchmark the whole retrieval loop on the demo graph (`--demo`, `--demo-en`).
- `tier3_cores.py` — core revision (duplicate merge) with `--apply`; without it, dry-run.
- `morning_brief.py` — assemble the morning brief from the graph and the nightly review.
- `nightly.sh`, `nightly_claude_cores.py` — the night cycle: tier3 + an optional cloud review of graph cores (off unless the cloud layer is enabled).
