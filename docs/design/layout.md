# Раскладка кода Чароита (генерируется `scripts/layout_map.py`, руками не править)

Источник истины — `docs/design/layout.json`; гейт — `tests/test_import_boundaries.py`. Снимок allowlist и точек входа: 2026-09-19T18:30Z. Модулей 64, рёбер импорта 199.

## Слои и направление стрелок

- **core** (зависит от: —; модулей 11): `config_loader`, `deps`, `exit_codes`, `file_locks`, `frontmatter`, `live_gate`, `media_meta`, `privacy`, `redirects`, `safe_write`, `vocabulary`
- **llm** (зависит от: core; модулей 4): `llm`, `llm_health`, `model_lease`, `nli`
- **graph** (зависит от: core; модулей 7): `dossier`, `graph_links`, `graph_names`, `graph_nodes`, `graph_search`, `graphs`, `tier3`
- **cloud** (зависит от: core; модулей 1): `cloud`
- **audio** (зависит от: core; модулей 9): `audio`, `channel_labels`, `diarize`, `diarize_live`, `frame_drops`, `owner_voice`, `stt`, `stt_runtime`, `voice_pitch`
- **meeting** (зависит от: core, llm, graph, cloud, audio; модулей 23): `action_items`, `autostop`, `busy_signals`, `channel_trace`, `fact_check`, `graph_updater`, `hint_guard`, `install_profile`, `lexicon`, `live_sidecar`, `meeting_archive`, `meeting_processing`, `meeting_source`, `meeting_stamp`, `meeting_thread`, `name_fixes`, `question_filter`, `rebuild_transcript`, `retro_fill`, `review_bridge`, `speaker_names`, `thesis_rules`, `transcript`
- **app** (зависит от: core, llm, graph, cloud, audio, meeting; модулей 9): `brain`, `charoite_paths`, `daemon`, `dictate`, `dictate_note`, `main`, `mcp_server`, `transcribe_file`, `voice_memos_bridge`

## Поправки к таблице брифа (с обоснованием)

- `charoite_paths` → app: корни данных и кода; по брифу остаётся в app, 17 импортёров — долг №321 в allowlist
- `deps` → core: рецепт про интерпретатор и .venv; ничего из репо не импортирует
- `fact_check` → meeting: сверка якорей документа со стенограммой; ничего из репо не импортирует, читают daemon, main, rebuild_transcript
- `graph_updater` → meeting: до разреза (№322) целиком встречный: встречная и графовая половины в одном файле
- `install_profile` → meeting: бриф ставит в app, но по коду это предикат над конфигом, импортирующий graphs; читают graph_updater и rebuild_transcript — до фазы 3 (№321: flag() в config_loader) держим в meeting
- `media_meta` → core: разбор контейнеров mp4/caf/wav ради момента записи; ничего из репо не импортирует
- `nli` → llm: ONNX-инференс NLI-модели; читают tier3 и daemon
- `tier3` → graph: бриф просил проверить по коду: ревизия ядер графа (bge-m3 + NLI), импортирует llm, nli, frontmatter, redirects, live_gate — граф, не облако
- `vocabulary` → core: декларативные замены из config.yaml; читают stt (audio) и import_meeting — в meeting дал бы ребро audio → meeting

## Рёбра против стрелок (allowlist с карточками на снятие)

Всего 16.

- `audio` (audio) → `charoite_paths` (app) — №321
- `audio` (audio) → `meeting_stamp` (meeting) — №322
- `channel_labels` (audio) → `speaker_names` (meeting) — №322
- `diarize` (audio) → `charoite_paths` (app) — №321
- `diarize` (audio) → `llm` (llm) — №322 (LLM.complete для имён спикеров — вызов точки входа)
- `graph_search` (graph) → `llm` (llm) — №321 (llm/nli из graph — Protocol или параметр)
- `graph_updater` (meeting) → `charoite_paths` (app) — №321
- `graphs` (graph) → `charoite_paths` (app) — №321
- `llm` (llm) → `charoite_paths` (app) — №321
- `llm_health` (llm) → `charoite_paths` (app) — №321
- `meeting_archive` (meeting) → `charoite_paths` (app) — №321
- `rebuild_transcript` (meeting) → `charoite_paths` (app) — №321
- `retro_fill` (meeting) → `charoite_paths` (app) — №321
- `stt` (audio) → `charoite_paths` (app) — №321
- `tier3` (graph) → `llm` (llm) — №321 (llm/nli из graph — Protocol или параметр)
- `tier3` (graph) → `nli` (llm) — №321 (llm/nli из graph — Protocol или параметр)

## Точки входа (исполняемые файлы, которые упоминает код)

- `scripts/bench_extract.py` ← ручной запуск
- `scripts/bench_models.py` ← ручной запуск
- `scripts/build_app_icon.sh` ← ручной запуск
- `scripts/build_embedded_python.sh` ← .github/workflows/release-app.yml, app/make_app.sh
- `scripts/check_private_markers.py` ← .github/workflows/supply-chain.yml, .pre-commit-config.yaml
- `scripts/check_test_assertions.py` ← .github/workflows/ci.yml, .pre-commit-config.yaml
- `scripts/cloud_review.py` ← src/graph_updater.py
- `scripts/dedup_archive.py` ← ручной запуск
- `scripts/dedup_graph.py` ← scripts/nightly.sh
- `scripts/diar_bench.py` ← ручной запуск
- `scripts/doctor.py` ← src/deps.py
- `scripts/fix_action_items.py` ← ручной запуск
- `scripts/forget_meeting.py` ← app/Sources/CharoiteApp/Services/MeetingActionsService.swift
- `scripts/get_models.py` ← app/Sources/CharoiteApp/Services/ModelPullService.swift, scripts/diar_bench.py, scripts/doctor.py, src/diarize_live.py, src/stt.py
- `scripts/graph_doctor.py` ← scripts/nightly.sh
- `scripts/graph_search_index.py` ← scripts/nightly.sh
- `scripts/import_meeting.py` ← app/Sources/CharoiteApp/Services/ImportService.swift, scripts/doctor.py, src/daemon.py
- `scripts/layout_map.py` ← ручной запуск
- `scripts/lock_runtime_deps.py` ← scripts/build_embedded_python.sh
- `scripts/make_dmg.sh` ← .github/workflows/release-app.yml
- `scripts/memory_bench.py` ← scripts/doctor.py, scripts/nightly.sh
- `scripts/merge_graphs.py` ← ручной запуск
- `scripts/migrate_placeholders.py` ← ручной запуск
- `scripts/morning_brief.py` ← scripts/nightly.sh
- `scripts/mutate_check.py` ← ручной запуск
- `scripts/nightly.sh` ← app/Sources/CharoiteApp/Services/NightlyStatusService.swift, app/Sources/CharoiteApp/Views/Settings/SettingsView.swift
- `scripts/nightly_claude_cores.py` ← scripts/nightly.sh
- `scripts/nightly_dossier.py` ← scripts/nightly.sh
- `scripts/nightly_dossier_review.py` ← scripts/nightly.sh
- `scripts/notarize.sh` ← .github/workflows/release-app.yml
- `scripts/protocol.py` ← app/Sources/CharoiteApp/Services/MeetingActionsService.swift
- `scripts/rename_meeting.py` ← app/Sources/CharoiteApp/Services/MeetingCard.swift
- `scripts/sign_release_manifest.py` ← .github/workflows/release-app.yml
- `scripts/stt_bench.py` ← ручной запуск
- `scripts/tier3_cores.py` ← scripts/nightly.sh, src/graph_updater.py
- `scripts/wait_for_idle.py` ← scripts/nightly.sh
- `src/daemon.py` ← app/Sources/CharoiteApp/Models/AppSettings.swift, app/Sources/CharoiteApp/Services/SetupReadinessService.swift, app/Sources/CharoiteApp/Services/SuflerService.swift, app/Sources/CharoiteApp/Views/Settings/SettingsView.swift
- `src/diarize.py` ← ручной запуск
- `src/dictate.py` ← app/Sources/CharoiteApp/Services/DictationService.swift
- `src/dictate_note.py` ← app/Sources/CharoiteApp/Services/DictationService.swift, scripts/import_meeting.py
- `src/graph_search.py` ← scripts/memory_bench.py
- `src/graph_updater.py` ← scripts/import_meeting.py, src/mcp_server.py, src/transcribe_file.py
- `src/llm_health.py` ← scripts/doctor.py
- `src/main.py` ← scripts/doctor.py
- `src/mcp_server.py` ← ручной запуск
- `src/meeting_archive.py` ← ручной запуск
- `src/privacy.py` ← scripts/doctor.py
- `src/rebuild_transcript.py` ← app/Sources/CharoiteApp/Services/MeetingProcessingService.swift, scripts/doctor.py, src/daemon.py
- `src/retro_fill.py` ← scripts/import_meeting.py
- `src/transcribe_file.py` ← scripts/import_meeting.py
- `src/voice_memos_bridge.py` ← ручной запуск

## Модули: импортирует → / кем импортируется ←

- `action_items` [meeting] → — ← daemon, graph_updater, mcp_server, rebuild_transcript, review_bridge
- `audio` [audio] → channel_labels, charoite_paths, meeting_stamp, stt_runtime ← daemon, main
- `autostop` [meeting] → — ← daemon
- `brain` [app] → graph_search ← daemon
- `busy_signals` [meeting] → file_locks, live_gate, meeting_processing ← —
- `channel_labels` [audio] → owner_voice, speaker_names ← audio, daemon, name_fixes, rebuild_transcript
- `channel_trace` [meeting] → live_sidecar, safe_write, transcript ← daemon, graph_updater, meeting_archive, meeting_source, rebuild_transcript
- `charoite_paths` [app] → — ← audio, daemon, diarize, dictate, dictate_note, graph_updater, graphs, llm, llm_health, main, mcp_server, meeting_archive, rebuild_transcript, retro_fill, stt, transcribe_file, voice_memos_bridge
- `cloud` [cloud] → — ← daemon, graph_updater
- `config_loader` [core] → — ← diarize, dictate, dictate_note, meeting_archive, retro_fill, transcribe_file, voice_memos_bridge
- `daemon` [app] → action_items, audio, autostop, brain, channel_labels, channel_trace, charoite_paths, cloud, deps, diarize_live, fact_check, file_locks, frame_drops, graph_nodes, graphs, hint_guard, install_profile, live_sidecar, llm, meeting_processing, meeting_source, meeting_stamp, meeting_thread, nli, owner_voice, privacy, question_filter, safe_write, speaker_names, stt, stt_runtime, thesis_rules, transcript, voice_pitch ← —
- `deps` [core] → — ← daemon, main, rebuild_transcript
- `diarize` [audio] → charoite_paths, config_loader, llm, stt ← rebuild_transcript
- `diarize_live` [audio] → — ← daemon
- `dictate` [app] → charoite_paths, config_loader, stt ← —
- `dictate_note` [app] → charoite_paths, config_loader, graphs, llm, meeting_stamp, safe_write, stt ← —
- `dossier` [graph] → redirects ← graph_search
- `exit_codes` [core] → — ← graph_updater, rebuild_transcript
- `fact_check` [meeting] → — ← daemon, main, rebuild_transcript
- `file_locks` [core] → — ← busy_signals, daemon, live_gate
- `frame_drops` [audio] → — ← daemon
- `frontmatter` [core] → — ← graph_links, graph_nodes, graph_search, graph_updater, lexicon, tier3
- `graph_links` [graph] → frontmatter, graph_names, redirects ← graph_updater
- `graph_names` [graph] → — ← graph_links, graph_nodes, graph_updater
- `graph_nodes` [graph] → frontmatter, graph_names, redirects ← daemon, graph_search, graph_updater
- `graph_search` [graph] → dossier, frontmatter, graph_nodes, graphs, llm, redirects, safe_write ← brain, graph_updater
- `graph_updater` [meeting] → action_items, channel_trace, charoite_paths, cloud, exit_codes, frontmatter, graph_links, graph_names, graph_nodes, graph_search, graphs, install_profile, live_gate, live_sidecar, llm, llm_health, meeting_archive, meeting_processing, meeting_source, meeting_stamp, privacy, redirects, safe_write, speaker_names, tier3, transcript ← —
- `graphs` [graph] → charoite_paths ← daemon, dictate_note, graph_search, graph_updater, install_profile, meeting_archive, meeting_processing, rebuild_transcript, retro_fill
- `hint_guard` [meeting] → — ← daemon
- `install_profile` [meeting] → graphs ← daemon, graph_updater, rebuild_transcript
- `lexicon` [meeting] → frontmatter ← rebuild_transcript
- `live_gate` [core] → file_locks ← busy_signals, graph_updater, rebuild_transcript, tier3
- `live_sidecar` [meeting] → meeting_stamp, safe_write ← channel_trace, daemon, graph_updater, mcp_server, meeting_archive, meeting_source, name_fixes, rebuild_transcript, retro_fill
- `llm` [llm] → charoite_paths, llm_health, model_lease, privacy ← daemon, diarize, dictate_note, graph_search, graph_updater, llm_health, main, mcp_server, meeting_archive, rebuild_transcript, retro_fill, tier3
- `llm_health` [llm] → charoite_paths, llm, model_lease, privacy ← graph_updater, llm
- `main` [app] → audio, charoite_paths, deps, fact_check, llm, stt, transcript ← —
- `mcp_server` [app] → action_items, charoite_paths, live_sidecar, llm, meeting_source, meeting_stamp, transcript ← —
- `media_meta` [core] → — ← —
- `meeting_archive` [meeting] → channel_trace, charoite_paths, config_loader, graphs, live_sidecar, llm, meeting_source, meeting_stamp, safe_write ← graph_updater, retro_fill
- `meeting_processing` [meeting] → graphs, meeting_stamp ← busy_signals, daemon, graph_updater, rebuild_transcript, retro_fill
- `meeting_source` [meeting] → channel_trace, live_sidecar, transcript ← daemon, graph_updater, mcp_server, meeting_archive, rebuild_transcript, retro_fill
- `meeting_stamp` [meeting] → — ← audio, daemon, dictate_note, graph_updater, live_sidecar, mcp_server, meeting_archive, meeting_processing, rebuild_transcript, retro_fill, transcript
- `meeting_thread` [meeting] → — ← daemon
- `model_lease` [llm] → — ← llm, llm_health
- `name_fixes` [meeting] → channel_labels, live_sidecar, review_bridge, safe_write, transcript ← —
- `nli` [llm] → — ← daemon, tier3
- `owner_voice` [audio] → — ← channel_labels, daemon, rebuild_transcript
- `privacy` [core] → — ← daemon, graph_updater, llm, llm_health, stt
- `question_filter` [meeting] → — ← daemon
- `rebuild_transcript` [meeting] → action_items, channel_labels, channel_trace, charoite_paths, deps, diarize, exit_codes, fact_check, graphs, install_profile, lexicon, live_gate, live_sidecar, llm, meeting_processing, meeting_source, meeting_stamp, owner_voice, safe_write, speaker_names, stt, transcript ← retro_fill
- `redirects` [core] → — ← dossier, graph_links, graph_nodes, graph_search, graph_updater, tier3
- `retro_fill` [meeting] → charoite_paths, config_loader, graphs, live_sidecar, llm, meeting_archive, meeting_processing, meeting_source, meeting_stamp, rebuild_transcript ← —
- `review_bridge` [meeting] → action_items, safe_write ← name_fixes
- `safe_write` [core] → — ← channel_trace, daemon, dictate_note, graph_search, graph_updater, live_sidecar, meeting_archive, name_fixes, rebuild_transcript, review_bridge, tier3
- `speaker_names` [meeting] → voice_pitch ← channel_labels, daemon, graph_updater, rebuild_transcript
- `stt` [audio] → charoite_paths, privacy, vocabulary ← daemon, diarize, dictate, dictate_note, main, rebuild_transcript, transcribe_file
- `stt_runtime` [audio] → — ← audio, daemon
- `thesis_rules` [meeting] → — ← daemon
- `tier3` [graph] → frontmatter, live_gate, llm, nli, redirects, safe_write ← graph_updater
- `transcribe_file` [app] → charoite_paths, config_loader, stt, transcript ← —
- `transcript` [meeting] → meeting_stamp ← channel_trace, daemon, graph_updater, main, mcp_server, meeting_source, name_fixes, rebuild_transcript, transcribe_file
- `vocabulary` [core] → — ← stt
- `voice_memos_bridge` [app] → charoite_paths, config_loader ← —
- `voice_pitch` [audio] → — ← daemon, speaker_names
