# Privacy

Charoite is built local-first. Concretely:

- **No telemetry.** Zero analytics, crash reporters or "anonymous usage stats". Grep the code.
- **No network calls** except to services you run yourself on localhost (Ollama at `127.0.0.1:11434`, optional local STT stream server) — unless you explicitly enable the cloud layer.
- **Cloud layer is opt-in and off by default** (`cloud_enrich: false`, `cloud_live: false`). When enabled, it runs the `claude` CLI under your own subscription; transcripts you choose to enrich are sent to Anthropic under your account and their terms. Turn it off and Charoite is fully offline.
- **Recordings are temporary.** Full-meeting audio is kept only to rebuild an accurate transcript after the meeting and is deleted after `record_keep_days` (default 2).
- **No voice biometrics stored.** Live diarization keeps speaker embeddings in RAM for the duration of the meeting only. Nothing voice-derived is written to disk.
- **Your data is plain files.** Transcripts, summaries and the knowledge graph are Markdown in folders you choose. Delete them, sync them, encrypt them — they are yours.
