# Practical user guide

***English** · [Русский](ru/USER_GUIDE.md) · [中文](zh/USER_GUIDE.md)*

This is not a feature catalogue — [Features](FEATURES.md) already fills that
role. This guide follows the daily path: prepare Charoite, record a meeting,
understand post-processing, take the result and recover from a failure.

## Before the first real meeting

1. Complete [Setup](SETUP.md). With the prebuilt app the first-run wizard
   asks for your name and graph folder — no need to edit `config/config.yaml`
   by hand. From source, fill in `sufler.user_name` and `sufler.graph_dir`.
2. Check readiness there as well: python runtime, config, the model address,
   Ollama, models, microphone and the graph folder. Working from source, set **Settings →
   Connection → Data folder** to the repository; the switch "Run the daemon
   code from this folder (development)" under it decides whether the app runs
   the code from there or from its own signed bundle.
3. Calls need no setup: on the first recording macOS asks for "Screen & System
   Audio Recording" — press Allow, then restart Charoite. Without it only your
   microphone is captured. BlackHole is only a fallback for a denied
   permission or a terminal run without the app.
4. Make a short test recording and wait for its result card. That exercises
   audio capture, transcription, Ollama and the graph in one pass.

If readiness does not explain the problem, run this from the repository root:

```bash
python3 scripts/doctor.py
```

`doctor` checks both installation and runtime health: a real model-generation
probe, stuck meetings, the import queue and free disk space. It never changes
anything on its own. It reads the data folder named by `CHAROITE_ROOT`; for
the prebuilt app's data see [Setup → Diagnosis](SETUP.md).

## Before every meeting

- Look for **Ready to record** (or **Meeting ready** after the last one
  finished) in the menu bar, not a problem line such as “Ollama is not
  responding”; a problem also turns the menu bar icon yellow, and only data
  being lost turns it red.
- Charoite picks the audio source itself and shows it after the start:
  "System audio (ScreenCaptureKit)" is the normal path, "BlackHole" the
  fallback. There is no need to switch the system output.
- Tell participants that the meeting is being recorded. Charoite joins no bot
  and cannot do this for you.
- With calendar integration enabled, the app offers to start recording but
  never starts it by itself.

## During the meeting

Press **Listen to meeting** (`⌘⇧Space`) in the Meeting section of the main
window or **Start recording** in the menu bar. A red dot and an increasing
timer mean that the session is running.

Check the first transcript lines as well as the timer:

- your configured name should label the microphone channel;
- the other side should appear on another channel or as “Speaker N”;
- missing system audio must be fixed now — it cannot be reconstructed later
  from the microphone alone;
- a “disk recording disabled” warning means the live transcript continues,
  but there is no safety audio for an accurate rebuild;
- if less than about 880 MB is free when recording starts, a warning stays
  for the whole meeting («МАЛО МЕСТА НА ДИСКЕ: …» — "low disk space") while
  recording continues. Free some space: what gets lost if the disk fills
  later is the tail of the meeting. The threshold is
  `audio.record_free_min_bytes` (bytes). A failed space check is a line in
  the daemon log and does not stop recording;
- critical memory pressure («ПАМЯТЬ НА ИСХОДЕ: …» — "memory is nearly gone")
  stays on screen until the machine is comfortable again. A mild warning is
  only in the daemon log: with a local model resident it is ordinary;
- a “hints are falling behind: up to N s of live audio lost” line means
  recognition cannot keep up with the conversation and the live transcript
  missed part of the speech. While disk recording is on, the meeting itself is
  safe: the final transcript is rebuilt from the audio and will be complete —
  only the in-meeting hints suffer. If the same line says recording is not
  running, that audio is gone for good and needs attention right away: free up
  disk space, close heavy apps. Falling behind usually means a busy machine —
  another model resident in memory, a build, a video export.

While the problem continues, the warning remains visible in both the meeting
window and the menu bar; ordinary hint or minutes messages cannot clear it.
“STT has not responded” means recognition is blocked inside the named stage;
after 100 seconds the existing watchdog attempts to recover the recording. A
red “audio is not being saved” warning outranks every other status; the only
thing shown in its place is the daemon's own error with the actual cause
(«ЗАПИСЬ НА ДИСК ВЫКЛЮЧЕНА: …» — "disk recording disabled: …").
Recording of the failed channel does not resume until the meeting ends — no
raw audio of that channel is available for recovery from that point onward;
the remaining channels and finalization keep working, and the freed disk
space serves them.

The main window may be closed; state remains in the menu bar. Quitting the app
during a recording asks for confirmation because it stops the meeting.

Forgetting to stop is not a problem: the recording ends by itself — after 5
minutes if nobody ever spoke, after 15 once the conversation goes quiet, and
after 6 hours in any case. There is a fourth case: if nothing was ever heard
from the far side — system audio stayed silent the whole recording and only you
were talking — quiet time is cut to 10 minutes instead of 15. Note that an
in-person meeting looks the same to us: everyone sits in one room and lands in
the microphone, so the far side is "silent" by definition. If your meetings are
in-person, set `alone_minutes: 15` and the rule stops changing anything;
`0` turns it off.
A fifth case is a farewell: on hearing "bye everyone" or "goodbye" in the
conversation, the recording waits only a minute of silence instead of
fifteen (the warning appears at once, any remark cancels it), and two
farewells in a row — an exchange of goodbyes — stop the recording
immediately. A "bye" inside a sentence does not count;
`farewell_seconds: 0` disables the rule. A warning appears a minute before (in the status
line, and as a banner if the window is not visible) — say something and the
recording continues. The stop goes the usual way, as if you pressed Stop: the
meeting is saved and processed, nothing is lost, and the status keeps the
reason. What autostop does not catch: a TV or music left on in the room — that
speech counts as talking for us, and only the six-hour ceiling stops such a
recording. Thresholds live in `config/config.yaml` under `sufler.autostop`.

## After Stop

Processing is independent of the window. The app reads actual pipeline state;
it does not invent a percentage. A transcript, the minutes, the meeting hints,
the archive canon and an imported meeting are stored with this Mac's paths
replaced: the home directory becomes `~`, and a data root that sits outside
the home becomes `‹данные Чароита›`. A directory name continues only with a
letter, a digit, `_`, `-`, or a dot inside the name. The hidden one-generation
copy kept to restore a hand edit stays as it was. Logs are not rewritten.

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
- four reading depths in place — Summary · Minutes · Analysis · Transcript;
  the card remembers the one you read last;
- gist, decisions and action items from `Саммари.md`, with a jump to the
  meeting's tasks;
- **Open in editor** for the depth that is open; **More** holds Obsidian,
  "Copy the protocol for participants", copying the summary, tasks or
  everything, and **Rebuild result**: a transcript you fixed by hand is kept
  and the minutes are rebuilt from it; an untouched one is recognised again
  while the recording is still kept (the previous version goes to
  `transcripts/.prev`).

The pencil beside the title renames the meeting coherently: transcript files,
archive folder, graph note and stored status path move together. Do not rename
those pieces separately in Finder; that leaves stale links behind.
**Forget meeting…** first lists every trace it will delete — transcript,
recording, archive folder, graph note, mentions in Cores — and deletes only
after "Delete permanently".

For an email to participants, use the cleaned protocol rather than a transcript:

```bash
.venv/bin/python scripts/protocol.py --style plain --copy
```

The latest meeting is used by default; pass a date or part of a title to select
one. The protocol contains the gist, decisions, actions, open questions and
risks. The raw transcript is never included under any option — the same goes
for the card's "Copy the protocol for participants".

## Recent meetings is not the archive

The **Meetings** section (the menu bar's **Recent meetings** opens it) lists up
to 20 status records from the last 14 days (Today · This week · Earlier). Its job is to
answer “what happened to yesterday's recording”, not to replace the archive.
The search field above the list goes further: it searches the summaries,
minutes and debriefs of the whole archive; a hit opens the meeting card when
the meeting is in the list, otherwise the document itself.

The complete history remains under `<graph_dir>/Встречи-архив/` and
`Встречи/`. Expiry from the list does not delete meeting documents. See
[Data and recovery](DATA_AND_RECOVERY.md) for every storage location and
retention rule.

## When processing did not finish

Move from the safest action to the more manual one:

1. Open the transcript offered by the error. If it contains the conversation,
   there is enough source data for a retry.
2. Press **Retry processing** on the meeting (or **Retry** in the menu bar). A
   second run is blocked while this one is active.
3. If it does not start, run `python3 scripts/doctor.py`. A common failure is
   Ollama returning its model list while generation itself is stuck.
4. Restart Ollama when instructed, then retry from the app.
5. If the status is gone but the transcript exists, run:

```bash
CHAROITE_ROOT="$PWD" .venv/bin/python src/rebuild_transcript.py transcripts/<file>.md
```

Do not delete the transcript or recent `recordings/` files before the meeting
is ready. Retry starts from the transcript, while the recording allows a more
accurate rebuild.

## Old recordings and phone recordings

Audio, an existing transcript or Zoom/Teams subtitles can enter the same
pipeline:

```bash
CHAROITE_ROOT="$PWD" .venv/bin/python scripts/import_meeting.py recording.m4a --date 2026-07-15
CHAROITE_ROOT="$PWD" .venv/bin/python scripts/import_meeting.py zoom.vtt --title "Planning"
```

The meeting date comes from the recording itself (container metadata or the
phone's name stamp), otherwise from the file's time; `--date` and `--time`
override it. The same file imported twice is recognised as a repeat and not
processed again.

In the app, drop the file on the **External recording** tab, or set a watched
folder in **Settings → Recording import** (m4a/wav/mp3, txt/md, vtt/srt): files
dropped there become meetings on their own. The tab shows the queue, failed
files with a **Retry** button, and when each copy will be deleted. A growing
WAV is postponed until copying finishes. The iPhone companion delivers into
the same folder; recordings from the stock Voice Memos app can be taken in too
(opt-in `audio.voice_memos_bridge`, needs Full Disk Access — see
[Setup](SETUP.md)).

The original you dropped on the tab stays where it was. Processed copies in
`done/` and the audio source in the meeting archive are deleted
`audio.import_keep_days` after import (2 by default) — independently of
`record_keep_days`; failed files are never deleted. Include that in backup and
deletion decisions.

## Between meetings

- **Memory** answers over past meetings: a chat with the local model that,
  with **Graph memory** on, draws on meetings, graph nodes and dossiers; chips
  under the answer show which sources were mixed in. "⚠ weak graph matches"
  means the archive may not contain the answer. A quick question also fits in
  the menu bar field.
- **Tasks** collects open Markdown checkboxes — yours first, by meeting or by
  due date; ticking one writes back to the graph file.
- **Dictation**, **Voice note** and **Diary** are available from the menu bar
  with `⌥⌘D`, `⌥⌘N` and `⌥⌘J`.

The compact daily loop is: check green readiness → start recording → verify
both sides appear → stop → wait for the card → share decisions and actions →
correct the title when needed.
