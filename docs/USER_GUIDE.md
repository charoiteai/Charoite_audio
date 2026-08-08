# Practical user guide

***English** · [Русский](ru/USER_GUIDE.md) · [中文](zh/USER_GUIDE.md)*

This is not a feature catalogue — [Features](FEATURES.md) already fills that
role. This guide follows the daily path: prepare Charoite, record a meeting,
understand post-processing, take the result and recover from a failure.

## Before the first real meeting

1. Complete [Setup](SETUP.md). With the prebuilt app the first-run wizard
   asks for your name and graph folder — no need to edit `config/config.yaml`
   by hand. From source, fill in `sufler.user_name` and `sufler.graph_dir`.
2. Check readiness there as well: python runtime, config, Ollama, models,
   microphone and the graph folder. Working from source, point **Settings**
   at the repository — the app then runs the code from there instead of its
   own bundle.
3. Calls need no setup: on the first recording macOS asks for "Screen & System
   Audio Recording" — press Allow. Without it only your microphone is
   captured. BlackHole is only a fallback for macOS before 13.
4. Make a short test recording and wait for its result card. That exercises
   audio capture, transcription, Ollama and the graph in one pass.

If readiness does not explain the problem, run this from the repository root:

```bash
python3 scripts/doctor.py
```

`doctor` checks both installation and runtime health: a real model-generation
probe, stuck meetings, the import queue and free disk space. It never changes
anything on its own.

## Before every meeting

- Look for **Ready to record** in the menu bar, not “Ollama is not responding”.
- Charoite picks the audio source itself and shows it after the start:
  "System audio (ScreenCaptureKit)" is the normal path, "BlackHole" the
  fallback. There is no need to switch the system output. (Fallback only.) For a call over BlackHole, make the Multi-Output Device containing BlackHole the current
  macOS output. Charoite reports active sources after start.
- Tell participants that the meeting is being recorded. Charoite joins no bot
  and cannot do this for you.
- With calendar integration enabled, the app offers to start recording but
  never starts it by itself.

## During the meeting

Press **Listen to meeting** in the main window or **Start recording** in the
menu bar. A red dot and an increasing timer mean that the session is running.

Check the first transcript lines as well as the timer:

- your configured name should label the microphone channel;
- the other side should appear on another channel or as “Speaker N”;
- missing system audio must be fixed now — it cannot be reconstructed later
  from the microphone alone;
- a “disk recording disabled” warning means the live transcript continues,
  but there is no safety audio for an accurate rebuild.

The main window may be closed; state remains in the menu bar. Quitting the app
during a recording asks for confirmation because it stops the meeting.

## After Stop

Processing is independent of the window. The app reads actual pipeline state;
it does not invent a percentage.

| State | What it means | What to do |
|---|---|---|
| Saving the recording | Channels are finalized; orphaned PCM may become WAV | Do not move files out of `recordings/` |
| Rebuilding the transcript | The full recording is transcribed and diarized again | The window may close; keep the Mac awake |
| Updating graph, part N of M | Minutes, nodes, links, archive and summary are being written | The part number should advance |
| Meeting ready | The graph note and result card are published | Open the card or Obsidian |
| Failed — source kept | The pipeline did not reach a ready graph | Open the transcript and retry |

Processing time depends on meeting length and the selected model. After a few
successful runs Charoite estimates it from your own median. A status that has
not changed for 30 minutes is shown as a failure instead of an endless spinner.

## The meeting card

The card exposes the part of a result people usually need:

- title, date and duration estimated from transcript timestamps;
- participants found in the transcript;
- gist, decisions and action items from `Саммари.md`;
- links to the full note, transcript and Obsidian;
- copy actions for the summary, tasks or the whole card.

The pencil beside the title renames the meeting coherently: transcript files,
archive folder, graph note and stored status path move together. Do not rename
those pieces separately in Finder; that leaves stale links behind.

For an email to participants, use the cleaned protocol rather than a transcript:

```bash
.venv/bin/python scripts/protocol.py --style plain --copy
```

The latest meeting is used by default; pass a date or part of a title to select
one. The protocol contains the gist, decisions, actions, open questions and
risks. The raw transcript is never included under any option.

## Recent meetings is not the archive

The window displays up to 20 status records from the last 14 days. Its job is
to answer “what happened to yesterday's recording”, not to replace the archive.

The complete history remains under `<graph_dir>/Встречи-архив/` and
`Встречи/`. Expiry from the window does not delete meeting documents. See
[Data and recovery](DATA_AND_RECOVERY.md) for every storage location and
retention rule.

## When processing did not finish

Move from the safest action to the more manual one:

1. Open the transcript offered by the error. If it contains the conversation,
   there is enough source data for a retry.
2. Press **Retry**. A second run is blocked while this one is active.
3. If it does not start, run `python3 scripts/doctor.py`. A common failure is
   Ollama returning its model list while generation itself is stuck.
4. Restart Ollama when instructed, then retry from the app.
5. If the status is gone but the transcript exists, run:

```bash
.venv/bin/python src/rebuild_transcript.py transcripts/<file>.md
```

Do not delete the transcript or recent `recordings/` files before the meeting
is ready. Retry starts from the transcript, while the recording allows a more
accurate rebuild.

## Old recordings and phone recordings

Audio, an existing transcript or Zoom/Teams subtitles can enter the same
pipeline:

```bash
.venv/bin/python scripts/import_meeting.py recording.m4a --date 2026-07-15
.venv/bin/python scripts/import_meeting.py zoom.vtt --title "Planning"
```

Alternatively configure the watched import folder in the app. A growing WAV
is postponed until copying finishes. Imported source audio is kept in the graph
and is not covered by `record_keep_days`; include that distinction in backup
and deletion decisions.

## Between meetings

- **Archive** answers over past meetings and exposes its sources. A weak-match
  warning means the archive may not contain the answer.
- **Meeting tasks** collects open Markdown checkboxes; ticking one writes back
  to the graph file.
- **Chat with memory** adds retrieved graph facts to the local model.
- **Dictation**, **Voice note** and **Diary** are available from the menu bar
  with `⌥⌘D`, `⌥⌘N` and `⌥⌘J`.

The compact daily loop is: check green readiness → start recording → verify
both sides appear → stop → wait for the card → share decisions and actions →
correct the title when needed.
