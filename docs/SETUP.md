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
ollama pull qwen3.6:35b-mlx && ollama pull qwen3.5:4b && ollama pull gemma4:e4b
```

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
cp config/config.example.yaml config/config.yaml
```

The app uses the embedded runtime when present and the `.venv` next to the
repository otherwise. To build a bundle with the runtime yourself:
`scripts/build_embedded_python.sh && app/make_app.sh`.

Which models exactly — the app suggests itself: the first-run wizard reads
your machine's memory and shows three ready sets ("Full", "Balanced",
"Light") with the recommended one marked, writes the chosen one into the
config and downloads it with a single button. Model details are in
[MODELS.md](MODELS.md).

## 2. Config: two required fields

**The easy way is in the app.** The first-run wizard asks for your name and
graph folder and writes them into `config/config.yaml` itself; the folder is
picked from a panel. If the file does not exist yet, the wizard creates it
from the bundled example and lays out the `config/` directory on its own.
When writing fails (no permission on the data folder, a broken install with
no example), the wizard says so with the reason instead of showing "Saved":
a silent refusal here would mean the person configured into the void and hit
a permanently red readiness. Editing the file by hand, below, is for installs
without the interface.

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
  `~/Documents/Obsidian/Work` — Charoite creates the structure itself.

Also worth filling: `sufler.user_context` (1-2 sentences about your work) —
context for instant answers.

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

**Fallback — BlackHole** (macOS before 13, or permission denied):

1. Install [BlackHole 2ch](https://existential.audio/blackhole/).
2. Audio MIDI Setup → "+" → Multi-Output Device → tick speakers AND BlackHole.
3. System output → that Multi-Output (you hear sound, Charoite gets it too).

Charoite picks the source itself: ScreenCaptureKit first, then BlackHole. The
meeting status shows which channel is in use.

## 4. macOS permissions

- **Microphone** — requested on first run.
- **Screen & System Audio Recording** — requested on the first meeting
  recording; without it only the microphone is heard (or BlackHole, if you
  set it up).
- **Universal Access** (optional) — only for dictation auto-paste; without
  it the text simply stays in the clipboard.

## 5. Voice diarization (optional)

Put an ERes2Net embedding model at `models/diar/embedding.onnx` — see
[DIARIZATION.md](DIARIZATION.md). Without it labels are per-channel
(you/them), with it — per voice ("Speaker 1/2/…").

## 6. Run

```bash
.venv/bin/python src/main.py     # CLI: live transcript + hints
.venv/bin/python src/daemon.py   # daemon for UI integration (NDJSON)
```

First run downloads the STT model (~1 min).

The first successful recording should end in a meeting card, not merely in a
transcript file. Follow the end-to-end check in the
[practical user guide](USER_GUIDE.md). A complete map of temporary audio,
transcripts, graph documents and retention lives in
[Data and recovery](DATA_AND_RECOVERY.md).

## 7. Where things live

- `transcripts/` — transcripts and the meeting's working files
- `recordings/` — full recordings (auto-deleted after `record_keep_days`)
- `<graph_dir>/Встречи-архив/` — a "date — title" folder per meeting:
  summary, minutes, transcript, questions and answers, debrief

This is the short map. Wherever retention, the source of truth or the recovery
order after a failure matter, use the
[full data map](DATA_AND_RECOVERY.md).

## Troubleshooting

- **Empty transcript** — check inputs: `python -c "import sounddevice as sd; print(sd.query_devices())"`.
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

`python3 scripts/doctor.py` checks Python, dependencies, config keys, the graph folder, Ollama and its models (incl. `bge-m3`), and diarization — with an exact fix for every problem.

The second half of the report is about running, not installing: whether the model answers a **generation** probe (a stalled Ollama returns its model list instantly while inference sits still — that difference is the only way to tell them apart), whether any meetings got stuck on the way to the graph, how many files wait in the import folder, and how much disk is left. Any "Charoite is silent" starts here.

The doctor is the one script that runs under any Python: it is written without
dependencies so that it can answer *before* they are installed. Everything else
runs via `.venv/bin/python` — and if you start it with the system Python, the
answer is a one-line recipe instead of a traceback (`src/deps.py`).

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
your folder; the release number from a single GET to GitHub's public API
once a day — no token, not a byte about you, and silent on any network
error. Don't want it: `sufler.check_updates: false` in the config; the
`CHAROITE_NO_CLOUD` switch turns this off too.

## Night cycle (optional)

`scripts/nightly.sh` keeps the graph tidy while you sleep: Tier-3 core
revision (duplicates, merges — with backups), the morning brief
`_Сегодня.md` (ready-made context for the day), and the memory bench
(quality regression signal). The pass waits for meeting processing to finish and runs on a single model.
On Aug 12 the two collided: transcription, core revision and dossier building
at once — 14 GB free out of 64 with 17 GB already compressed. The local server
started swapping models in and out (41 loads in one pass), requests began to
hang for 2-6 minutes, and then it died outright: 258 topics went unanalysed.
The wait is capped at an hour (`NIGHTLY_WAIT`, seconds): missing a night
entirely is worse than working in a crowded machine.

The step order exists so that the brief is ready by morning no matter what: it
is written right away, before the heavy steps, and once more at the end on top
of tidied cores. On Aug 13 that cost a whole morning — the graph had grown to
three hundred cores, the full revision was in its fifth hour, and the brief was
still waiting last in line. On weekdays the revision now runs incrementally
(`--since-last`: only cores changed since the previous pass); the full sweep
happens on Sundays or by hand with `NIGHTLY_TIER3_FULL=1`.

Schedule it with launchd:

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
`logs/nightly.json` next to your data (the launchd log lives in `/tmp` and
disappears on reboot, which makes "never ran" indistinguishable from "the
file is gone"), and the app reads it. A successful pass is one calm line
with the time; a pass in progress, failed steps, an interrupted run and a
skipped night are highlighted.

Only a night where nothing went wrong counts as a success. A silent model is
caught separately: if the local server dies mid-pass, dossiers are built with
nothing to build from — topics stay unanalysed while the step still exits
zero. Such a night is marked `досье(модель-молчала)`, otherwise the graph
goes stale unnoticed.

Check separately which path the agent points at: if the repository has
moved, the `plist` keeps launching the script from the old location — the
graph gets edited nightly by an older version of the code, and without the
status file there is no way to notice.
