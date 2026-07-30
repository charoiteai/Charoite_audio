# Privacy

*[**English**] · [Русский](PRIVACY.ru.md) · [中文](PRIVACY.zh.md)*

Charoite is built local-first. Concretely:

- **No telemetry.** Zero analytics, crash reporters or "anonymous usage stats". Grep the code.
- **No network calls** except to services you run yourself on localhost (Ollama at `127.0.0.1:11434`, optional local STT stream server) — unless you explicitly enable the cloud layer.
- **Cloud layer is opt-in and off by default.** There are exactly four switches, all of them `false` in the shipped config, and this list is the whole truth — a test fails if a fifth one appears and is not documented here:
  - `cloud_enrich` — after you stop the meeting, the **full transcript** goes to Claude for a debrief.
  - `cloud_live` — mid-meeting questions go out as **chunks of the transcript**, one request per question.
  - `cloud_hints` — cloud refinement of live hints: the transcript is sent on **every hint**, i.e. a steady stream for as long as the meeting runs, not a single package. Requires `cloud_live` as well.
  - `cloud_edit_graph` — the only switch that grants **writing, not sending**: the nightly dossier review may rewrite graph files itself instead of just filing a report. Requires `cloud_enrich` as well. Transcripts, minutes and `## Author edits` are never touched, and every edit is backed up first.

  When enabled, they run the `claude` CLI under your own subscription; what you choose to send goes to Anthropic under your account and their terms. Turn them off and Charoite is fully offline.
- **One place decides, and the check sits where the request leaves.** Every "may this leave the machine?" question is answered by `src/privacy.py`. The check lives at the network exit itself, not only at the caller: a manual request (the ☁️ button, ⌘⇧⏎) used to reach the API past a switch that was only consulted on the automatic path. Silence in the config means *no* — a missing key, `"false"`, `0` or an empty value are never read as permission. `tests/test_privacy_defaults.py` and `tests/test_cloud_call_sites.py` hold both properties, so this bullet is a test rather than a promise.
- **Kill switch.** `CHAROITE_NO_CLOUD=1` (or the historical `SUFLER_NO_CLOUD=1`) in the environment forces the cloud layer off whatever the config says — "run this one strictly offline" should be an env var, not a YAML edit before someone else's meeting.
- **Recordings are temporary.** Full-meeting audio is kept only to rebuild an accurate transcript after the meeting and is deleted after `record_keep_days` (default 2). Cleanup runs when the daemon starts, not only when a new meeting begins — so recordings expire on schedule even if you do not record for a week or turn `record` off.
- **A meeting can be forgotten entirely.** `record_keep_days` covers the audio; `scripts/forget_meeting.py <date|stamp>` covers the rest: the transcript and its derivatives, the meeting folder in the archive, the meeting node, the transcript copy under Documentation, chronicle lines in Cores (together with the fact that came from that meeting) and links in Dossiers and people's nodes. By default it only prints the plan — deletion is irreversible and needs `--yes`; edits to surviving nodes are backed up into `.forget_backup/`. What it cannot do: reach copies already synced to iCloud or Time Machine, or clean up files held by other participants.
- **Audio imported into the graph is yours to manage.** `scripts/import_meeting.py` copies the source file into the meeting folder so the transcript can be rebuilt later. That copy lives in your graph (often iCloud) and is *not* covered by `record_keep_days` — delete the meeting folder if you want it gone.
- **Models are downloaded once.** On first run the selected STT model is fetched from Hugging Face; the diarization model is installed on demand by `scripts/get_models.py --diar`, which prints the URL before connecting. Everything after that is local: neither the daemon nor the app ever fetches models. If you need a machine that never reaches the network, pre-populate `models/` and run offline.
- **No voice biometrics stored.** Live diarization keeps speaker embeddings in RAM for the duration of the meeting only. Nothing voice-derived is written to disk.
- **Your data is plain files.** Transcripts, summaries and the knowledge graph are Markdown in folders you choose. Delete them, sync them, encrypt them — they are yours.
