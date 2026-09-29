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
| `data/sck/` | Raw capture streams the app writes during a session | Removed at a normal stop; leftovers of a crash — after `record_keep_days` | How live audio gets from the app to the daemon |
| `transcripts/` | Live and final transcripts, minutes, hints and debrief | Until explicitly removed | Pipeline input and manual retry source |
| `<graph_dir>/Встречи/` | Episode note with links and facts | Persistent | Canonical meeting memory in the graph |
| `<graph_dir>/Встречи-архив/` | Readable meeting folder with summary, minutes, transcript and other layers | Persistent | Finder, Obsidian and sync result |
| `<graph_dir>/Документация/` | Document copies referenced by graph nodes | Persistent | Graph sources |
| `logs/meeting-status/` | Processing state, transcript path and error | 14 days | App state and Recent meetings |
| Import folder `done/`, `Исходник.*` in the archive folder | Copy of an imported recording and its audio source next to the meeting | `audio.import_keep_days`, 2 days after the import by default | A second chance while you check the result |
| `backups/<graph>-<hash>/` | Service copies of the graph: the cloud-review snapshot and quarantine, parked conflict copies, copies made before bulk edits of action items | The snapshot keeps one run, the quarantine ten; parked copies stay until you remove them | Undo for automatic graph edits, outside iCloud |
| `data/graph_search/` | Vector cache of the hint memory: vectors and file paths of graph blocks, no text | Rebuildable (`scripts/graph_search_index.py`); vectors of vanished files drop out on the next indexing | Hint-memory speed, not a backup |
| `~/Library/Application Support/Charoite/semantic_index_v2.bin` | Derived search index, holds ~700-character previews of every block — including transcripts | Rebuildable; entries for files that disappeared are dropped on the next search | Search acceleration, not a backup |
| `~/Library/Application Support/CharoiteApp/chat_history.json` | Local chat: the last 200 messages of the conversation (answers may retell the graph; the excerpts themselves are not stored) | Until the chat's **Clear**; not touched by `forget_meeting.py` | Conversation continuity, nothing else reads it |

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
- Conflict copies `Имя 2.md … Имя 12.md` next to meeting documents (in
  archive folders and in `Документация/Стенограммы встреч`) came from the
  archiver rewriting unchanged documents in place under iCloud; since 0.84.0
  it writes only changes, through a temporary file. Copies that
  `scripts/dedup_graph.py --apply-copies` (or `sufler.dedup_copies: true` in
  the nightly pass) removes are the byte-identical ones only, and they are not
  deleted: they sit in `backups/<graph>-<hash>/dedup_copies/<run>/` under the
  data root, outside iCloud, with `manifest.tsv` (copy, original, bytes,
  sha256; paths relative to the graph). To restore one, copy it back to the
  graph path in the first column. Copies that differ from the original stay
  where they are and are only reported — a human decides.

The safe rule is simple: editing a final note is fine, but move or rename the
meeting's layers through the provided UI and commands rather than separately.

## Automatic deletion

Automatic cleanup is limited to data documented as temporary:

- PCM/WAV files in `recordings/` older than `audio.record_keep_days`,
  including `*.wav.part*` conversion temporaries;
- raw capture streams a crash left in `data/sck/`, on the same window (the
  live session's own folder is never touched);
- `logs/graph_*.log`, `logs/cloud_review_*.log`, `logs/retry_*.log`, `logs/recover_*.log` and the live Nemotron shadow's `logs/nemotron_live_*` diagnostic logs, the shared graph-decision log `logs/graph_unlinked.log` with its rotated `logs/graph_unlinked.old` (removed once nothing has been written to it for the retention window; it is not cleaned line by line), and the import-cleanup output `logs/import_prune-*.log` on the same retention window (the rebuild lock files `logs/rebuild-*.pid` are left alone);
- copies of imported recordings — the file in the import folder's `done/` and
  the audio `Исходник` in the meeting's archive folder — `audio.import_keep_days`
  after the import (2 by default, independent of `record_keep_days`); the app
  sweeps every six hours while it runs, the daemon at the start of every
  meeting, both the folder chosen in the app and `audio.import_dir`;
- processing status records older than 14 days.

Audio cleanup runs when the daemon starts. This means two things:

1. a file may physically remain beyond its nominal age while Charoite is not
   run;
2. after the next launch, expired audio may disappear at once.

One exception is deliberate: **a recording that is being rebuilt right now is
never deleted, even past its retention date.** On startup the daemon finds
interrupted meetings, launches their rebuild and holds their recordings out of
cleanup for the duration; a rebuild started any other way (a retry from the
app, a manual run) refreshes the modification time of the meeting's
recordings, so retention counts `record_keep_days` from that moment. The delay
is not silent — Charoite reports it in the status line («Ретеншн придержал N
записей: встречи ещё восстанавливаются»).

Files whose names the pipeline does not recognise are never removed: cleanup
deletes only what it created. Anything you drop into `recordings/` by hand is
yours to remove by hand.
Transcripts, summaries, minutes, tasks and graph nodes are not deleted by that
retention setting.

## Imported sources are different

`scripts/import_meeting.py` copies imported source audio into the meeting's
archive folder in the graph as `Исходник.<ext>`. That copy is not under
`recordings/` and `record_keep_days` does not apply to it. If the graph is
synced with iCloud, the source may be synced too.

A recording that arrived through the import folder (the app's **External
recording** tab, `--scan`; the Voice Memos bridge copies into the same folder)
has its own clock: the copy in `done/` and the audio `Исходник` in the archive
are deleted `audio.import_keep_days` after the import (see above); a text or
subtitle source in the archive stays — it holds no voice. A single file
imported by hand with `import_meeting.py <file>` has no copy in `done/`, so
nothing counts its days: its `Исходник` in the archive stays until you delete
it or forget the meeting.

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
| Error «модель не дала разбор — граф не обновлён» | Transcript, minutes, the archive folder and the vault copies are already assembled | The local model was down or returned no JSON: only the graph nodes are missing. Retry once the model is back (the pipeline retries unfinished meetings itself); nothing on disk needs repair |
| Second meeting in a row has no far side | The microphone channel is intact | Before 0.48.0 stopping the previous capture deleted the streams of the meeting that had already started; update. Such a meeting cannot be rebuilt — the system audio is gone |
| Processing no longer advances | Status may belong to a dead process | Run `doctor`, then retry |
| No status, transcript exists | Only the UI status is missing | Run `rebuild_transcript.py` manually |
| PCM remains after a crash | Raw audio and live transcript | Next daemon start recovers the meeting and clears the interrupted conversion; do not delete PCM |
| Only WAV/M4A/VTT/SRT remains | Meeting source | Import with `import_meeting.py` |
| Two archive folders for one meeting | Documents are usually intact in both | Dry-run `dedup_archive.py`, then use `--apply` |
| Search misses a known fact | Markdown may be intact; the index is derived | Inspect the file and trigger a fresh search; do not treat the index as a source |
| A command prints «корень данных не назван» and exits with code 5 | Everything — nothing was started | `rebuild_transcript.py`, `import_meeting.py` and other entry points do not guess where the data lives: pass `CHAROITE_ROOT`, as in the commands below |

Baseline diagnosis:

```bash
python3 scripts/doctor.py
```

> **Installed the app rather than the repository?** The interpreter and the
> code live inside the bundle, so prefix the commands below and point them at
> your data folder (the default below, or the folder chosen in Settings; adjust
> `APP` if you put the app elsewhere):
>
> ```bash
> APP=/Applications/Charoite.app/Contents/Resources
> export CHAROITE_ROOT=~/Library/Application\ Support/Charoite
> cd "$APP/charoite" && "$APP/python/bin/python3" src/rebuild_transcript.py …
> ```
>
> `.venv/bin/python` in the commands below assumes a cloned repository.

Manual rebuild from an existing transcript:

```bash
CHAROITE_ROOT="$PWD" .venv/bin/python src/rebuild_transcript.py transcripts/<file>.md
```

Import a surviving source:

```bash
CHAROITE_ROOT="$PWD" .venv/bin/python scripts/import_meeting.py <audio|text|subtitles>
```

## Safe maintenance commands

Commands that change data can show a plan first.

Rename every layer coherently:

```bash
.venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "New title"
.venv/bin/python scripts/rename_meeting.py 2026-08-03_1130 "New title" --yes
```

A stamp with seconds (`2026-08-03_113015`) picks the second meeting of the
same minute.

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
transcripts (including `transcripts/.prev/` and live sidecars), recordings,
archive and graph — including its pipeline status under
`logs/meeting-status/`, its graph, cloud-review, retry, recovery and live-shadow logs, every
`<stamp>_*.md` copy under Documentation and the meeting's files in every kind
of service copy under `backups/`. Facts already sent to the optional external
memory (`sufler.brain`) are forgotten there too. With `--import-folder <folder>`
(the app passes it) the imported copy in `done/` goes as well; without it the
plan says the copy will expire by `import_keep_days`. `--keep-graph` removes
only the transcript and the recording.

A minute key (`2026-07-15_1400`) takes the meeting's own per-second files —
the daemon and the import name recordings by the second — but never another
meeting that started in the same minute: whatever the script cannot prove is
this meeting stays on disk and is named in the plan with its own stamp to
forget separately. A meeting of which only the archive folder is left is found
by the folder's manifest; archive folders of that day which the plan does not
take are named too, with the reason. Surviving nodes are copied to
`.forget_backup/<stamp>/` at the graph root before their references are edited.
That directory is not a trash can for the deleted meeting — after
confirmation, its own files should be considered removed.

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
