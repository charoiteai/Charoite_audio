# Setup

***English** · [Русский](ru/SETUP.md) · [中文](zh/SETUP.md)*

## 1. Dependencies

**From the prebuilt app (recommended).** Charoite.app from the
[releases](https://github.com/charoiteai/Charoite_audio/releases) ships a
python runtime inside: no git clone, no venv, no pip. All you need is the
language model — Ollama:

```bash
brew install ollama
brew services start ollama
ollama pull qwen3.5:4b   # the set of the shipped config (8–16 GB)
```

On 32 GB the main model becomes `qwen3.8:27b-mlx`, on 64 GB+
`qwen3.6:35b-mlx` (`qwen3.5:4b` stays the light one) — the first-run wizard
picks the set for your memory and downloads it itself, see below.

**Install one of the two: brew or Ollama.app — never both.** The app starts its
own server and takes port 11434; the brew service then fails to start and sits
silently in `error`, and a brew upgrade never takes effect — the running server
stays old. The symptom looks harmless: `ollama --version` prints a client/server
version mismatch warning. If the app is already installed and you want brew:
quit it, disable its autostart (`launchctl disable gui/$(id -u)/com.ollama.ollama`)
and remove it — the service will come up on its own.

⚠️ **Proxies.** Ollama reads `HTTP_PROXY`/`HTTPS_PROXY` from the environment,
not from macOS system settings. A service started by `brew services` does not
inherit them and goes out directly — measured on Aug 13: 6.8 MB/s versus
39 KB/s through a local proxy, a 170-fold difference. If you run `ollama serve`
by hand from a shell with a proxy configured, model downloads will take hours.

The cloud layer is the opposite case: `claude -p` launched from the app has
no shell environment, so Charoite injects the proxy itself from the `env`
section of `~/.claude/settings.json` (one place for the post-meeting review,
the nightly reviews and live answers). If a post-meeting review fails with
«403 Request not allowed», the request went to api.anthropic.com directly:
check `HTTPS_PROXY` there.

**The runtime installs with a button.** The first-run readiness check tells
three states apart: running, installed but not started, not installed at all.
In the first two cases the app starts it itself (`brew services start` for a
brew install, launching the app for Ollama.app); in the third it installs via
Homebrew when available, otherwise opens the download page. When both are
present it starts the brew service rather than a second instance — otherwise
they fight over the port again.

Everything else the app asks for and does itself: your name and graph folder in
the first-run wizard, the voice-separation model with a button, permissions
through system dialogs.

**From source** (development, custom build, non-Apple-Silicon):

```bash
git clone https://github.com/charoiteai/Charoite_audio && cd Charoite_audio
python3 -m venv .venv && .venv/bin/pip install .
cp config/config.example.en.yaml config/config.yaml
```

That copies the English preset; for Russian or Chinese meetings copy
`config/config.example.yaml` or `config/config.example.zh.yaml` instead
(see [config/README.md](../config/README.md)), then set `llm.model` and
`llm.small_model` to the row of the RAM table in the README that fits your Mac.

The app uses the embedded runtime when present and the `.venv` next to the
repository otherwise. To build a bundle with the runtime yourself:
`scripts/build_embedded_python.sh && app/make_app.sh`. The embedded runtime
deliberately leaves out `parakeet-mlx` and `mlx-whisper` (they pull in half a
gigabyte of torch): it recognises Russian with GigaAM and Chinese with
SenseVoice. For the Parakeet (English) or Whisper presets use a source install
or build the runtime with `scripts/build_embedded_python.sh --extras`.

**Where the data lives.** A bundle carries its own code (signed, read-only),
so the prebuilt app keeps your data — `config/`, `transcripts/`,
`recordings/`, `models/`, `logs/` — in `~/Library/Application Support/Charoite`.
To work from a clone, set Settings → Connection → Data folder to it; a clone at
`~/Charoite_audio` is not adopted by itself — the Today screen offers it once.
Running the clone's code as well is a separate, visible switch under the same
field: "Run the daemon code from this folder (development)" — on for a folder
you pick there, off when the clone is taken through the Today offer, which is
about data only.

Which models exactly — the app suggests itself: the first-run wizard reads
your machine's memory and shows four ready sets ("Full" from 64 GB,
"Precise" from 32, "Balanced" from 16, "Light" from 8) with the recommended
one marked, writes the chosen one into the config and downloads it with a
single button. Model details are in [MODELS.md](MODELS.md).

## 2. Config: two required fields

**The easy way is in the app.** The first-run wizard asks for your name and
graph folder and writes them into `config/config.yaml` in the data folder
itself; the folder is picked from a panel. If the file does not exist yet, the
wizard creates it from the bundled example and lays out the `config/` directory
on its own. When writing fails (no permission on the data folder, a broken
install with no example), the wizard says so with the reason instead of showing
"Saved": a silent refusal here would mean the person configured into the void
and hit a permanently red readiness. Editing the file by hand, below, is for
installs without the interface.

In `config/config.yaml`:

- `sufler.user_name` — your name: labels your microphone in the transcript
  and is never assigned to another voice. In a call your lines are signed
  with it, because your microphone is a separate track from the system
  audio your interlocutors arrive on. Three cases where the name is *not*
  applied, and Charoite says so out loud: an in-person meeting (no sound in
  the speakers — one microphone hears the whole room, so there is nobody to
  tell apart), several distinct voices in your microphone (a colleague next
  to you), and a name indistinguishable from the neutral label
  («Собеседник», «Собеседник 2»), which is rejected outright.
- `sufler.graph_dir` — knowledge-graph folder (empty **or pointing at a
  folder whose parent does not exist** = graph off, transcription still
  works — a typo in the path leaves you with transcripts, not with failed
  meetings). Point it inside your Obsidian vault, e.g.
  `~/Documents/Obsidian/Work` — Charoite creates the structure itself. A
  relative path (`demo/graph`) is resolved by the app and by every script
  from the Charoite data folder (the one holding `config/`), never from the
  directory a script happens to be launched from; `CHAROITE_GRAPH_DIR` (or the
  older `SUFLER_GRAPH_DIR`) in the environment overrides the setting for trial
  runs on another graph and also narrows graph discovery to that graph's vault
  (the iCloud folder is not read).

Also worth filling: `sufler.user_context` (1-2 sentences about your work) —
context for instant answers.

**Language.** The interface and the meeting documents follow
`sufler.language` (`ru`, `en` or `zh`), not the system locale. The example
bundled with the app is the Russian one, so for English or Chinese set it in
the config and restart the app. Speech recognition is a separate pair of keys,
`stt.backend` and `stt.language` — section 1 says which backends the prebuilt
runtime can run.

## 3. System audio (calls) — one permission and a restart

The app captures meeting audio with macOS itself (ScreenCaptureKit). On the
first recording the system asks once for "Screen & System Audio Recording" —
press Allow, **then restart Charoite**.

The restart is not our whim: macOS applies the granted permission only to a
fresh launch of the process. Until the app is restarted the checkbox in System
Settings is already on while capture still fails — that meeting gets recorded
without the far side. The first-run readiness panel shows a separate line when
a restart is pending.

After the restart that's it: no drivers, no Audio MIDI Setup, no switching the
output device. Sound keeps going to your speakers as usual, and on macOS 15+
the microphone arrives in the same stream.

Separate channels give free "you / the other side" diarization and echo
filtering.

**Fallback — BlackHole** (permission denied, or a terminal run without the
app: the ScreenCaptureKit stream is raised by the app, the daemon only reads it):

1. Install [BlackHole 2ch](https://existential.audio/blackhole/).
2. Audio MIDI Setup → "+" → Multi-Output Device → tick speakers AND BlackHole.
3. System output → that Multi-Output (you hear sound, Charoite gets it too).

Charoite picks the source itself: ScreenCaptureKit first, then BlackHole. The
meeting status shows which channel is in use.

If there is no channel for the other side at all (Screen Recording permission
revoked, BlackHole not set up), or the device is there but fails to open at
start (busy, revoked), recording still starts — microphone only —
and Charoite says so right at the start: a line in the meeting status plus a
system notification with sound, including the reason. The status line stays
red until the end of the meeting — ordinary status updates do not push it
away. There is no more silent
"the meeting was recorded without the other side". A channel for the other
side that dies mid-meeting (two consecutive failed restarts, or a restart
that hung) raises the same alarm — once per outage, with advice; if the
channel comes back, the line is cleared and says so. The sound notification
fires at most three times per meeting, the status line on every outage.
The exception is
`device: mic` in `config.yaml`: the microphone is chosen deliberately there,
and no warning about the other side is raised.

The same holds for your own microphone. If it dies (headphones unplugged, a
hub gone, the stream failing to restart) or fails to open at start while the
other side is alive, the alarm is the same: "your microphone is not in the
recording, only the other side is being recorded from now on", a
notification, a line in capture.log. The advice differs: do not restart the
recording, check the microphone — the watchdog restarts the channel itself
and the line clears. Both channels lost (on macOS 15+ they share one stream)
reads "recording is empty". The red line is one for all channels: it is
rebuilt on every loss and cleared only when everything records again; if one
channel comes back while another is still dead, the line names what is still
missing.

Every loss and return also stays with the meeting itself: a line in the
meeting thread and in the transcript's notes, and a caveat in the minutes that
the recording is incomplete — so a gap is not later mistaken for silence.

## 4. macOS permissions

- **Microphone** — requested on first run.
- **Screen & System Audio Recording** — requested on the first meeting
  recording; without it only the microphone is heard (or BlackHole, if you
  set it up).
- **Notifications** — requested on the first recording: the alarm about a
  lost channel and the autostop warning reach you as banners; without them
  only the line inside the app remains.
- **Accessibility** (optional) — only for dictation: auto-paste into the
  field you were typing in and, on macOS 26, the live draft panel while you
  speak; without it the text simply stays in the clipboard.
- **Calendars** (optional) — only with Settings → Calendar → "Brief and a
  nudge to record": reads event titles and times, locally.
- **Full Disk Access** (optional) — only for the Voice Memos bridge
  (`audio.voice_memos_bridge`): the Voice Memos container is protected by the
  system. The grant is broad (the whole home Library), so the decision is yours.

## 5. Voice diarization (optional)

One command puts both voice models in place: the ERes2Net embedding model at
`models/diar/embedding.onnx` and the segmentation model at
`models/diar/segmentation.onnx` (in the app — the "Tell speakers apart"
button of the first-run wizard):

```bash
.venv/bin/python scripts/get_models.py --diar    # embedding choices: --list
```

Details and tuning — [DIARIZATION.md](DIARIZATION.md). Without them labels are
per-channel (you/them); with both — per voice ("Speaker 1/2/…"). Embeddings
without segmentation leave live labels in a simplified mode and skip the
after-meeting re-labelling.

## 6. Run

```bash
CHAROITE_ROOT="$PWD" .venv/bin/python src/main.py     # CLI: live transcript + hints
# or the daemon for UI integration (NDJSON over stdout/stdin). It does not guess
# where your data lives — whoever starts it names the root:
CHAROITE_ROOT="$PWD" .venv/bin/python src/daemon.py
```

First run downloads the STT model (~1 min). Say something — transcript lines
appear in the console.

`CHAROITE_ROOT` is the data folder. The daemon, `src/main.py`, the transcript
rebuild, the importer and the other pipeline entry points never infer it from
where the code lies: without it they stop before doing anything, with a
one-line recipe and exit code 5 (`EXIT_ROOT_UNNAMED` in `src/exit_codes.py`) —
distinct from argparse's 2, so launchd and scripts can tell "not started" from
"crashed". The app always passes the root itself.

The first successful recording should end in a meeting card, not merely in a
transcript file. Follow the end-to-end check in the
[practical user guide](USER_GUIDE.md). A complete map of temporary audio,
transcripts, graph documents and retention lives in
[Data and recovery](DATA_AND_RECOVERY.md).

## 7. Where things live

Relative to the data folder (the prebuilt app: `~/Library/Application Support/Charoite`;
from source: the clone named by `CHAROITE_ROOT`):

- `transcripts/` — transcripts and the meeting's working files
- `recordings/` — full recordings (auto-deleted after `record_keep_days`)
- `<graph_dir>/Встречи-архив/` — a "date — title" folder per meeting:
  summary, minutes, transcript, questions and answers, debrief

This is the short map. Wherever retention, the source of truth or the recovery
order after a failure matter, use the
[full data map](DATA_AND_RECOVERY.md).

## Troubleshooting

- **Empty transcript** — check inputs: `.venv/bin/python -c "import sounddevice as sd; print(sd.query_devices())"`.
- **Slow answers** — `ollama ps`: the model must stay in RAM; keep
  `num_ctx: 8192` in the config.
- **No system audio** — check the permission: System Settings → Privacy →
  Screen & System Audio Recording, Charoite must be listed and enabled. After
  an app update the permission sometimes has to be re-granted: untick and tick
  it again. If you use BlackHole as the fallback, the macOS output must be the
  Multi-Output device rather than the speakers directly.

## Semantic search (recommended)

The app's archive search adds a semantic layer when the `bge-m3`
embedding model is available in Ollama:

```bash
ollama pull bge-m3   # ~1.2 GB; without it search is lexical-only
```

The index builds in the background on first search and updates
incrementally as the graph changes (stored in
`~/Library/Application Support/Charoite/semantic_index_v2.bin`).

## Diagnosis

`python3 scripts/doctor.py` checks Python, dependencies, config keys, the graph folder, Ollama and its models (incl. `bge-m3`), the SenseVoice model when that backend is chosen, and diarization — with an exact fix for every problem.

The second half of the report is about running, not installing: whether the model answers a **generation** probe (a stalled Ollama returns its model list instantly while inference sits still — that difference is the only way to tell them apart), whether any meetings got stuck on the way to the graph, how many files wait in the import folder, and how much disk is left. Any "Charoite is silent" starts here.

The doctor reads the data folder named by `CHAROITE_ROOT`, otherwise the clone
it lives in. For the prebuilt app's data, run it from a clone as
`CHAROITE_ROOT="$HOME/Library/Application Support/Charoite" python3 scripts/doctor.py`.
It changes nothing on its own; the one exception is an explicit
`--restart-llm` — an emergency restart of the model server when a stuck
generation holds it and the pipeline's own watchdog does not fire.

The doctor is the one script that runs under any Python: it is written without
dependencies so that it can answer *before* they are installed. Everything else
runs via `.venv/bin/python` — and if you start it with the system Python, the
answer is a one-line recipe instead of a traceback (`src/deps.py`).

## Claude Code tools (optional)

`src/mcp_server.py` is an MCP server that gives Claude Code the live (or
latest) meeting as tools: status, live transcript, notes, hints, minutes by the
local model and a graph update. Register it with the data folder named:

```bash
claude mcp add sufler -e CHAROITE_ROOT="$PWD" -- "$PWD/.venv/bin/python" "$PWD/src/mcp_server.py"
```

Registered without `CHAROITE_ROOT`, the server still starts, but every tool
answers with a tool error carrying this recipe (and a JSON block with `env` for
other MCP clients) — nothing is read, no model is called and no graph update
is launched until the root is named.

## Versions: the app, the code and the release

A repository install holds three separate things, and they drift apart
quietly: the app (`.app` in `~/Applications`), the code in your working
folder — what the daemon and the nightly pass actually run — and the latest
release on GitHub. An app at 0.46.0 when 0.47.0 is already out looks
perfectly normal; so does a folder ten commits behind. You find out when you
spend half a day fixing a bug that no longer exists upstream.

The app compares all three and says so on the Today tab when they diverge.
Matching versions are the norm and get no line: a reminder about normality
stops being read within a week. The code version comes from the git tag in
your folder; the release number from a single GET to GitHub's public API when
you come back to the app, at most once every four hours (a daily pause used to
turn release day into waiting day) — no token, not a byte about you, and
silent on any network error. Don't want it: Settings → "Ask GitHub about a new
version", or `sufler.check_updates: false` in the config; the
`CHAROITE_NO_CLOUD` switch turns this off too.

## Night cycle (optional)

`scripts/nightly.sh` keeps the graph tidy while you sleep: a graph health
check, Tier-3 core revision (duplicates, merges — with backups), topic dossiers,
the morning brief `_Сегодня.md` (ready-made context for the day), and the
memory bench (quality regression signal). The pass waits for meeting
processing to finish and runs on a single model.
On Aug 12 the two collided: transcription, core revision and dossier building
at once — 14 GB free out of 64 with 17 GB already compressed. The local server
started swapping models in and out (41 loads in one pass), requests began to
hang for 2-6 minutes, and then it died outright: 258 topics went unanalysed.
The wait is capped at an hour (`NIGHTLY_WAIT`, seconds): missing a night
entirely is worse than working in a crowded machine. The night also has to end
by morning: `NIGHTLY_MAX_H` (4 hours by default) caps the whole pass — a long
step stops between topics, and what is left is picked up the next night.

The step order exists so that the brief is ready by morning no matter what: it
is written right away, before the heavy steps, and once more at the end on top
of tidied cores. On Aug 13 that cost a whole morning — the graph had grown to
three hundred cores, the full revision was in its fifth hour, and the brief was
still waiting last in line. On weekdays the revision now runs incrementally
(`--since-last`: only cores changed since the previous pass); the full sweep
happens on Sundays or by hand with `NIGHTLY_TIER3_FULL=1`.

For an install from a clone the app schedules it for you: Settings → Nightly
cycle → Turn on writes the launchd agent below (04:15, log in
`logs/nightly.log` next to the data). By hand:

```xml
<!-- ~/Library/LaunchAgents/ai.charoite.nightly.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.charoite.nightly</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>/PATH/TO/Charoite_audio/scripts/nightly.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>/PATH/TO/Charoite_audio/logs/nightly.log</string>
  <key>StandardErrorPath</key><string>/PATH/TO/Charoite_audio/logs/nightly.log</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/ai.charoite.nightly.plist
```

Whether the pass actually ran shows up in the app on the Today tab, at the
bottom of the recent meetings column. Nightly work is invisible by
definition: you are asleep, and in the morning a tidied graph looks exactly
like an untouched one. So the script writes its outcome to
`logs/nightly.json` next to your data (a log is for reading, not for the app
to judge; and the launchd log used to live in `/tmp` and vanish on reboot,
which made "never ran" indistinguishable from "the file is gone"), and the app
reads it. A successful pass is one calm line with the time; a pass
in progress, failed steps, an interrupted run, a night the Mac slept through
and a skipped night are highlighted — and a problem night also turns the menu
bar icon yellow.

Only a night where nothing went wrong counts as a success. A silent model is
caught separately: if the local server dies mid-pass, dossiers are built with
nothing to build from — topics stay unanalysed while the step still exits
zero. Such a night is marked `досье(модель-молчала)`, otherwise the graph
goes stale unnoticed.

The app also checks which path the agent points at: if the repository has
moved, the `plist` keeps launching the script from the old location — the
graph gets edited nightly by an older version of the code. The Today line then
says the nightly pass runs from another folder and names the script.
