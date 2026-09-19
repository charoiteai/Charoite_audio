# Раскладка кода Чароита (генерируется `scripts/layout_map.py`, руками не править)

Источник истины — `docs/design/layout.json`; гейт — `tests/test_import_boundaries.py`. Снимок: 2026-09-19T18:06Z, HEAD 85bf212. Модулей 64, строк 29271, рёбер импорта 199.

## Слои и направление стрелок

- **core** (зависит от: —; модулей 11): `config_loader`, `deps`, `exit_codes`, `file_locks`, `frontmatter`, `live_gate`, `media_meta`, `privacy`, `redirects`, `safe_write`, `vocabulary`
- **llm** (зависит от: core; модулей 4): `llm`, `llm_health`, `model_lease`, `nli`
- **graph** (зависит от: core, llm; модулей 7): `dossier`, `graph_links`, `graph_names`, `graph_nodes`, `graph_search`, `graphs`, `tier3`
- **cloud** (зависит от: core; модулей 1): `cloud`
- **audio** (зависит от: core; модулей 9): `audio`, `channel_labels`, `diarize`, `diarize_live`, `frame_drops`, `owner_voice`, `stt`, `stt_runtime`, `voice_pitch`
- **meeting** (зависит от: core, llm, graph, cloud, audio; модулей 23): `action_items`, `autostop`, `busy_signals`, `channel_trace`, `fact_check`, `graph_updater`, `hint_guard`, `install_profile`, `lexicon`, `live_sidecar`, `meeting_archive`, `meeting_processing`, `meeting_source`, `meeting_stamp`, `meeting_thread`, `name_fixes`, `question_filter`, `rebuild_transcript`, `retro_fill`, `review_bridge`, `speaker_names`, `thesis_rules`, `transcript`
- **app** (зависит от: core, llm, graph, cloud, audio, meeting; модулей 9): `brain`, `charoite_paths`, `daemon`, `dictate`, `dictate_note`, `main`, `mcp_server`, `transcribe_file`, `voice_memos_bridge`

## Рёбра против стрелок (allowlist с карточками на снятие)

Всего 13.

- `audio` (audio) → `charoite_paths` (app) — №321
- `audio` (audio) → `meeting_stamp` (meeting) — №322
- `channel_labels` (audio) → `speaker_names` (meeting) — №322
- `diarize` (audio) → `charoite_paths` (app) — №321
- `diarize` (audio) → `llm` (llm) — №322 (LLM.complete для имён спикеров — вызов точки входа или Protocol)
- `graph_updater` (meeting) → `charoite_paths` (app) — №321
- `graphs` (graph) → `charoite_paths` (app) — №321
- `llm` (llm) → `charoite_paths` (app) — №321
- `llm_health` (llm) → `charoite_paths` (app) — №321
- `meeting_archive` (meeting) → `charoite_paths` (app) — №321
- `rebuild_transcript` (meeting) → `charoite_paths` (app) — №321
- `retro_fill` (meeting) → `charoite_paths` (app) — №321
- `stt` (audio) → `charoite_paths` (app) — №321

## Точки входа (кто зовёт код по пути)

- `scripts/bench_extract.py` ← app/Sources/CharoiteApp/Services/ModelPresetPolicy.swift
- `scripts/cloud_review.py` ← src/graph_updater.py
- `scripts/dedup_graph.py` ← scripts/nightly.sh
- `scripts/forget_meeting.py` ← app/Sources/CharoiteApp/Services/MeetingActionsService.swift
- `scripts/get_models.py` ← app/Sources/CharoiteApp/Services/ModelPullService.swift, scripts/build_embedded_python.sh
- `scripts/graph_doctor.py` ← scripts/nightly.sh
- `scripts/graph_search_index.py` ← scripts/nightly.sh
- `scripts/import_meeting.py` ← app/Sources/CharoiteApp/Services/ImportService.swift, app/Sources/CharoiteApp/Views/Workspace/ExternalRecordingPolicy.swift, src/daemon.py
- `scripts/lock_runtime_deps.py` ← scripts/build_embedded_python.sh
- `scripts/memory_bench.py` ← scripts/nightly.sh, src/deps.py
- `scripts/morning_brief.py` ← scripts/nightly.sh
- `scripts/nightly_claude_cores.py` ← scripts/nightly.sh
- `scripts/nightly_dossier.py` ← scripts/nightly.sh
- `scripts/nightly_dossier_review.py` ← scripts/nightly.sh
- `scripts/protocol.py` ← app/Sources/CharoiteApp/Services/MeetingActionsService.swift
- `scripts/rename_meeting.py` ← app/Sources/CharoiteApp/Services/MeetingCard.swift
- `scripts/sign_release_manifest.py` ← app/Sources/CharoiteApp/Services/UpdateAuthenticity.swift
- `scripts/tier3_cores.py` ← scripts/nightly.sh
- `scripts/wait_for_idle.py` ← scripts/nightly.sh
- `src/daemon.py` ← app/Sources/CharoiteApp/App/CharoiteApp.swift, app/Sources/CharoiteApp/Models/AppSettings.swift, app/Sources/CharoiteApp/Services/SetupReadinessService.swift, app/Sources/CharoiteApp/Services/SuflerService.swift, app/Sources/CharoiteApp/Views/Settings/SettingsView.swift, app/make_app.sh, scripts/migrate_placeholders.py
- `src/dictate.py` ← app/Sources/CharoiteApp/Services/DictationService.swift
- `src/dictate_note.py` ← app/Sources/CharoiteApp/Services/DictationService.swift, scripts/import_meeting.py
- `src/dossier.py` ← scripts/nightly.sh
- `src/graph_search.py` ← scripts/nightly.sh
- `src/graph_updater.py` ← scripts/import_meeting.py, src/mcp_server.py
- `src/graphs.py` ← app/Sources/CharoiteApp/Models/AppSettings.swift
- `src/llm.py` ← app/Sources/CharoiteApp/Services/MeetingMinutes.swift
- `src/privacy.py` ← app/Sources/CharoiteApp/Models/AppSettings.swift
- `src/rebuild_transcript.py` ← app/Sources/CharoiteApp/Services/MeetingProcessingService.swift, src/daemon.py
- `src/retro_fill.py` ← scripts/import_meeting.py
- `src/speaker_names.py` ← app/Sources/CharoiteApp/Views/Tasks/TasksScreenPolicy.swift
- `src/transcribe_file.py` ← scripts/import_meeting.py

## Модули: импортирует → / кем импортируется ←

- `action_items` [meeting, 530 строк] → — ← daemon, graph_updater, mcp_server, rebuild_transcript, review_bridge
- `audio` [audio, 1960 строк] → channel_labels, charoite_paths, meeting_stamp, stt_runtime ← daemon, main
- `autostop` [meeting, 421 строк] → — ← daemon
- `brain` [app, 159 строк] → graph_search ← daemon
- `busy_signals` [meeting, 115 строк] → file_locks, live_gate, meeting_processing ← —
- `channel_labels` [audio, 88 строк] → owner_voice, speaker_names ← audio, daemon, name_fixes, rebuild_transcript
- `channel_trace` [meeting, 458 строк] → live_sidecar, safe_write, transcript ← daemon, graph_updater, meeting_archive, meeting_source, rebuild_transcript
- `charoite_paths` [app, 201 строк] → — ← audio, daemon, diarize, dictate, dictate_note, graph_updater, graphs, llm, llm_health, main, mcp_server, meeting_archive, rebuild_transcript, retro_fill, stt, transcribe_file, voice_memos_bridge
- `cloud` [cloud, 259 строк] → — ← daemon, graph_updater
- `config_loader` [core, 29 строк] → — ← diarize, dictate, dictate_note, meeting_archive, retro_fill, transcribe_file, voice_memos_bridge
- `daemon` [app, 3337 строк] → action_items, audio, autostop, brain, channel_labels, channel_trace, charoite_paths, cloud, deps, diarize_live, fact_check, file_locks, frame_drops, graph_nodes, graphs, hint_guard, install_profile, live_sidecar, llm, meeting_processing, meeting_source, meeting_stamp, meeting_thread, nli, owner_voice, privacy, question_filter, safe_write, speaker_names, stt, stt_runtime, thesis_rules, transcript, voice_pitch ← —
- `deps` [core, 53 строк] → — ← daemon, main, rebuild_transcript
- `diarize` [audio, 412 строк] → charoite_paths, config_loader, llm, stt ← rebuild_transcript
- `diarize_live` [audio, 563 строк] → — ← daemon
- `dictate` [app, 60 строк] → charoite_paths, config_loader, stt ← —
- `dictate_note` [app, 311 строк] → charoite_paths, config_loader, graphs, llm, meeting_stamp, safe_write, stt ← —
- `dossier` [graph, 548 строк] → redirects ← graph_search
- `exit_codes` [core, 19 строк] → — ← graph_updater, rebuild_transcript
- `fact_check` [meeting, 93 строк] → — ← daemon, main, rebuild_transcript
- `file_locks` [core, 117 строк] → — ← busy_signals, daemon, live_gate
- `frame_drops` [audio, 42 строк] → — ← daemon
- `frontmatter` [core, 211 строк] → — ← graph_links, graph_nodes, graph_search, graph_updater, lexicon, tier3
- `graph_links` [graph, 193 строк] → frontmatter, graph_names, redirects ← graph_updater
- `graph_names` [graph, 92 строк] → — ← graph_links, graph_nodes, graph_updater
- `graph_nodes` [graph, 376 строк] → frontmatter, graph_names, redirects ← daemon, graph_search, graph_updater
- `graph_search` [graph, 1554 строк] → dossier, frontmatter, graph_nodes, graphs, llm, redirects, safe_write ← brain, graph_updater
- `graph_updater` [meeting, 3324 строк] → action_items, channel_trace, charoite_paths, cloud, exit_codes, frontmatter, graph_links, graph_names, graph_nodes, graph_search, graphs, install_profile, live_gate, live_sidecar, llm, llm_health, meeting_archive, meeting_processing, meeting_source, meeting_stamp, privacy, redirects, safe_write, speaker_names, tier3, transcript ← —
- `graphs` [graph, 158 строк] → charoite_paths ← daemon, dictate_note, graph_search, graph_updater, install_profile, meeting_archive, meeting_processing, rebuild_transcript, retro_fill
- `hint_guard` [meeting, 19 строк] → — ← daemon
- `install_profile` [meeting, 87 строк] → graphs ← daemon, graph_updater, rebuild_transcript
- `lexicon` [meeting, 369 строк] → frontmatter ← rebuild_transcript
- `live_gate` [core, 102 строк] → file_locks ← busy_signals, graph_updater, rebuild_transcript, tier3
- `live_sidecar` [meeting, 588 строк] → meeting_stamp, safe_write ← channel_trace, daemon, graph_updater, mcp_server, meeting_archive, meeting_source, name_fixes, rebuild_transcript, retro_fill
- `llm` [llm, 1362 строк] → charoite_paths, llm_health, model_lease, privacy ← daemon, diarize, dictate_note, graph_search, graph_updater, llm_health, main, mcp_server, meeting_archive, rebuild_transcript, retro_fill, tier3
- `llm_health` [llm, 509 строк] → charoite_paths, llm, model_lease, privacy ← graph_updater, llm
- `main` [app, 126 строк] → audio, charoite_paths, deps, fact_check, llm, stt, transcript ← —
- `mcp_server` [app, 238 строк] → action_items, charoite_paths, live_sidecar, llm, meeting_source, meeting_stamp, transcript ← —
- `media_meta` [core, 373 строк] → — ← —
- `meeting_archive` [meeting, 1065 строк] → channel_trace, charoite_paths, config_loader, graphs, live_sidecar, llm, meeting_source, meeting_stamp, safe_write ← graph_updater, retro_fill
- `meeting_processing` [meeting, 625 строк] → graphs, meeting_stamp ← busy_signals, daemon, graph_updater, rebuild_transcript, retro_fill
- `meeting_source` [meeting, 161 строк] → channel_trace, live_sidecar, transcript ← daemon, graph_updater, mcp_server, meeting_archive, rebuild_transcript, retro_fill
- `meeting_stamp` [meeting, 513 строк] → — ← audio, daemon, dictate_note, graph_updater, live_sidecar, mcp_server, meeting_archive, meeting_processing, rebuild_transcript, retro_fill, transcript
- `meeting_thread` [meeting, 541 строк] → — ← daemon
- `model_lease` [llm, 330 строк] → — ← llm, llm_health
- `name_fixes` [meeting, 311 строк] → channel_labels, live_sidecar, review_bridge, safe_write, transcript ← —
- `nli` [llm, 116 строк] → — ← daemon, tier3
- `owner_voice` [audio, 404 строк] → — ← channel_labels, daemon, rebuild_transcript
- `privacy` [core, 329 строк] → — ← daemon, graph_updater, llm, llm_health, stt
- `question_filter` [meeting, 161 строк] → — ← daemon
- `rebuild_transcript` [meeting, 1469 строк] → action_items, channel_labels, channel_trace, charoite_paths, deps, diarize, exit_codes, fact_check, graphs, install_profile, lexicon, live_gate, live_sidecar, llm, meeting_processing, meeting_source, meeting_stamp, owner_voice, safe_write, speaker_names, stt, transcript ← retro_fill
- `redirects` [core, 59 строк] → — ← dossier, graph_links, graph_nodes, graph_search, graph_updater, tier3
- `retro_fill` [meeting, 295 строк] → charoite_paths, config_loader, graphs, live_sidecar, llm, meeting_archive, meeting_processing, meeting_source, meeting_stamp, rebuild_transcript ← —
- `review_bridge` [meeting, 684 строк] → action_items, safe_write ← name_fixes
- `safe_write` [core, 240 строк] → — ← channel_trace, daemon, dictate_note, graph_search, graph_updater, live_sidecar, meeting_archive, name_fixes, rebuild_transcript, review_bridge, tier3
- `speaker_names` [meeting, 302 строк] → voice_pitch ← channel_labels, daemon, graph_updater, rebuild_transcript
- `stt` [audio, 141 строк] → charoite_paths, privacy, vocabulary ← daemon, diarize, dictate, dictate_note, main, rebuild_transcript, transcribe_file
- `stt_runtime` [audio, 286 строк] → — ← audio, daemon
- `thesis_rules` [meeting, 53 строк] → — ← daemon
- `tier3` [graph, 540 строк] → frontmatter, live_gate, llm, nli, redirects, safe_write ← graph_updater
- `transcribe_file` [app, 155 строк] → charoite_paths, config_loader, stt, transcript ← —
- `transcript` [meeting, 478 строк] → meeting_stamp ← channel_trace, daemon, graph_updater, main, mcp_server, meeting_source, name_fixes, rebuild_transcript, transcribe_file
- `vocabulary` [core, 32 строк] → — ← stt
- `voice_memos_bridge` [app, 394 строк] → charoite_paths, config_loader ← —
- `voice_pitch` [audio, 131 строк] → — ← daemon, speaker_names
