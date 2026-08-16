# Data, retention and recovery

***English** · [Русский](ru/DATA_AND_RECOVERY.md) · [中文](zh/DATA_AND_RECOVERY.md)*

Charoite is local-first in more than model placement. Its working state is a
set of visible files that can be copied and recovered without a proprietary
database. One meeting still has several layers with different lifetimes. This
map helps distinguish a temporary source from the archive and avoid deleting
the only input for a retry.

## One meeting's data map

| Location | Contents | Lifetime | Purpose |
|---|---|---|---|
| `recordings/` | Separate microphone and system-audio PCM/WAV channels | `record_keep_days`, 2 days by default | Safety source for an accurate rebuild |
| `transcripts/` | Live and final transcripts, minutes, hints and debrief | Until explicitly removed | Pipeline input and manual retry source |
| `<graph_dir>/Встречи/` | Episode note with links and facts | Persistent | Canonical meeting memory in the graph |
| `<graph_dir>/Встречи-архив/` | Readable meeting folder with summary, minutes, transcript and other layers | Persistent | Finder, Obsidian and sync result |
| `<graph_dir>/Документация/` | Document copies referenced by graph nodes | Persistent | Graph sources |
| `logs/meeting-status/` | Processing state, transcript path and error | 14 days | App state and Recent meetings |
| `~/Library/Application Support/Charoite/semantic_index_v2.bin` | Derived search index | Rebuildable | Search acceleration, not a backup |

`record_keep_days` deletes audio, not meeting documents. Disappearing from the
Meetings section also deletes nothing; only the status record expired.

## Sources of truth

- Long-term memory lives in Markdown under `graph_dir`. The vector index and
  UI history are derived and may be rebuilt.
- A retry requires the file under `transcripts/`. As long as it exists, the
  pipeline can run again without a live session.
- The most accurate transcript rebuild also needs a recent `recordings/`
  source. Once retention removes it, the text remains but disputed audio can no
  longer be recognized again.
- `Встречи-архив` is the reading layer. With `sufler.dedup_files: true`, some
  files may be hard links to originals in `Документация/`; editing through
  either path then edits the same contents.

The safe rule is simple: editing a final note is fine, but move or rename the
meeting's layers through the provided UI and commands rather than separately.

## Automatic deletion

Automatic cleanup is limited to data documented as temporary:

- PCM/WAV files in `recordings/` older than `audio.record_keep_days`,
  including `*.wav.part*` conversion temporaries;
- `logs/graph_*.log` diagnostic logs on the same retention window;
- processing status records older than 14 days.

Audio cleanup runs when the daemon starts. A file may therefore remain beyond
its nominal age while Charoite is not run, then disappear on the next launch.

One exception is deliberate: **a recording that is being rebuilt right now is
never deleted, even past its retention date.** On startup the daemon finds
interrupted meetings, launches their rebuild and holds their recordings out of
cleanup for the duration; a retry started from the app protects its own files.
The delay is not silent — Charoite reports it in the status line.

Files whose names the pipeline does not recognise are never removed: cleanup
deletes only what it created. Anything you drop into `recordings/` by hand is
yours to remove by hand.
Transcripts, summaries, minutes, tasks and graph nodes are not deleted by that
retention setting.

## Imported sources are different

`scripts/import_meeting.py` keeps imported source audio with the meeting
materials in the graph. That copy is no longer under `recordings/` and is not
covered by `record_keep_days`. If the graph is synced with iCloud, the source
may be synced too.

Under a “keep audio for two days” policy, imported sources need a separate
cleanup after the result is verified, or the whole meeting must be forgotten.

## Minimal backup set

To recover from a disk loss, keep:

1. all of `graph_dir` — persistent memory and final documents;
2. `transcripts/` — the ability to re-run processing;
3. `config/config.yaml` — selected paths, models and rules;
4. unexpired `recordings/` when re-transcription matters.

The repository and models can be downloaded again. `logs/`, the search index
and an app build normally need no backup. Config can contain workplace paths
and privacy choices, so protect it as carefully as the graph.

Before a large manual graph edit, make a normal file copy or a commit in your
private Git repository. Charoite does not replace Time Machine and does not
version arbitrary hand edits.

## Recovery by symptom

| Symptom | What is probably intact | Next step |
|---|---|---|
| Error after Stop | Usually transcript and recording | Open the transcript, then Retry |
| Second meeting in a row has no far side | The microphone channel is intact | Before 0.48.0 stopping the previous capture deleted the streams of the meeting that had already started; update. Such a meeting cannot be rebuilt — the system audio is gone |
| Processing no longer advances | Status may belong to a dead process | Run `doctor`, then retry |
| No status, transcript exists | Only the UI status is missing | Run `rebuild_transcript.py` manually |
| PCM remains after a crash | Raw audio and live transcript | Next daemon start recovers it; do not delete PCM |
| Only WAV/M4A/VTT/SRT remains | Meeting source | Import with `import_meeting.py` |
| Two archive folders for one meeting | Documents are usually intact in both | Dry-run `dedup_archive.py`, then use `--apply` |
| Search misses a known fact | Markdown may be intact; the index is derived | Inspect the file and trigger a fresh search |

Baseline diagnosis:

```bash
python3 scripts/doctor.py
```

> **Installed the app rather than the repository?** The interpreter and the
> code live inside the bundle, so prefix the commands below and point them at
> your data folder:
>
> ```bash
> APP=~/Applications/Charoite.app/Contents/Resources
> export CHAROITE_ROOT=~/Library/Application\ Support/Charoite
> cd "$APP/charoite" && "$APP/python/bin/python3" src/rebuild_transcript.py …
> ```
>
> `.venv/bin/python` in the commands below assumes a cloned repository.

Manual rebuild from an existing transcript:

```bash
.venv/bin/python src/rebuild_transcript.py transcripts/<file>.md
```

Import a surviving source:

```bash
.venv/bin/python scripts/import_meeting.py <audio|text|subtitles>
```

## Safe maintenance commands

Commands that change data can show a plan first.

Rename every layer coherently:

```bash
.venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "New title"
.venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "New title" --yes
```

Consolidate old duplicate archive folders:

```bash
.venv/bin/python scripts/dedup_archive.py
.venv/bin/python scripts/dedup_archive.py --apply
```

On apply, extra folders are moved to `Встречи-архив/_дубли/` for inspection,
not deleted.

Forget a meeting completely:

```bash
.venv/bin/python scripts/forget_meeting.py 2026-07-15
.venv/bin/python scripts/forget_meeting.py 2026-07-15_1400 --yes
```

The first run only lists affected files. `--yes` removes the meeting from
transcripts, recordings, archive and graph; surviving nodes are copied to
`.forget_backup/` before their references are edited. That directory is not a
trash can for the deleted meeting — after confirmation, its own files should
be considered removed.

## Avoid these during recovery

- Do not run two manual `rebuild_transcript.py` processes for one meeting.
- Do not delete PCM/WAV merely because it looks like an implementation file.
- Do not copy a growing WAV into the import folder twice; let the first copy
  finish.
- Do not edit `logs/meeting-status/` JSON to make a meeting “ready”: status
  reports a result but does not create it.
- Do not delete the search index expecting it to restore missing Markdown; it
  is derived and does not contain a complete graph copy.

For daily work, remember three layers: `recordings/` enables re-transcription,
`transcripts/` enables a pipeline retry, and `graph_dir` preserves long-term
memory.
