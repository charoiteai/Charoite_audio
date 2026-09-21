# Раскладка кода Чароита (генерируется `scripts/layout_map.py`, руками не править)

Источник истины — `docs/design/layout.json`; гейт — `tests/test_import_boundaries.py`. Снимок allowlist: 2026-09-20T17:26Z. Модулей 65.

## Слои и направление стрелок

- **core** (зависит от: —; модулей 13): `charoite_paths`, `config_loader`, `deps`, `exit_codes`, `file_locks`, `frontmatter`, `live_gate`, `media_meta`, `model_seam`, `privacy`, `redirects`, `safe_write`, `vocabulary`
- **llm** (зависит от: core; модулей 4): `llm`, `llm_health`, `model_lease`, `nli`
- **graph** (зависит от: core; модулей 7): `dossier`, `graph_links`, `graph_names`, `graph_nodes`, `graph_search`, `graphs`, `tier3`
- **cloud** (зависит от: core; модулей 1): `cloud`
- **audio** (зависит от: core; модулей 9): `audio`, `channel_labels`, `diarize`, `diarize_live`, `frame_drops`, `owner_voice`, `stt`, `stt_runtime`, `voice_pitch`
- **meeting** (зависит от: core, llm, graph, cloud, audio; модулей 23): `action_items`, `autostop`, `busy_signals`, `channel_trace`, `fact_check`, `graph_updater`, `hint_guard`, `install_profile`, `lexicon`, `live_sidecar`, `meeting_archive`, `meeting_processing`, `meeting_source`, `meeting_stamp`, `meeting_thread`, `name_fixes`, `question_filter`, `rebuild_transcript`, `retro_fill`, `review_bridge`, `speaker_names`, `thesis_rules`, `transcript`
- **app** (зависит от: core, llm, graph, cloud, audio, meeting; модулей 8): `brain`, `daemon`, `dictate`, `dictate_note`, `main`, `mcp_server`, `transcribe_file`, `voice_memos_bridge`

## Поправки к таблице брифа (с обоснованием)

- `charoite_paths` → core: корни данных и кода; машинный замер: из репозитория не импортирует ничего, а импортируют его 19 модулей всех слоёв. Слой app достался от брифа и делал нарушением каждый импорт в него — 10 записей allowlist из 16. Перенос вниз снимает все 10 и не создаёт ни одного нового (№321, фаза 3)
- `deps` → core: рецепт про интерпретатор и .venv; ничего из репо не импортирует
- `fact_check` → meeting: сверка якорей документа со стенограммой; ничего из репо не импортирует, читают daemon, main, rebuild_transcript
- `graph_updater` → meeting: до разреза (№322) целиком встречный: встречная и графовая половины в одном файле
- `install_profile` → meeting: бриф ставит в app, но по коду это предикат над конфигом, импортирующий graphs; читают graph_updater и rebuild_transcript — до фазы 3 (№321: flag() в config_loader) держим в meeting
- `media_meta` → core: разбор контейнеров mp4/caf/wav ради момента записи; ничего из репо не импортирует
- `model_seam` → core: шов способности: тип векторизатора нужен обоим берегам — и слою моделей, который его строит, и графу, который его получает. В llm он дал бы графу импорт ради аннотации, то есть ровно то ребро, которое шов снимает (гейт считает импорты обходом всего дерева, включая TYPE_CHECKING). Зависимостей нет: модуль читает и doctor.py, обязанный работать до установки пакетов (№321, кусок 2а)
- `nli` → llm: ONNX-инференс NLI-модели; читают tier3 и daemon
- `tier3` → graph: бриф просил проверить по коду: ревизия ядер графа (bge-m3 + NLI), импортирует llm, nli, frontmatter, redirects, live_gate — граф, не облако
- `vocabulary` → core: декларативные замены из config.yaml; читают stt (audio) и import_meeting — в meeting дал бы ребро audio → meeting

## Корень выводит один модуль — объявленные исключения

Канон: `src/charoite_paths.py`. Область правила: `src/`.

- `src/deps.py`, форма «file»: рецепт про интерпретатор: по своему докстрингу не может импортировать ничего, иначе упадёт первым — в том числе канон путей. Цепочка ведёт к .venv, то есть к корню КОДА, а не данных (обе головы круга №321)

## Рёбра против стрелок (allowlist с карточками на снятие)

Всего 3.

- `audio` (audio) → `meeting_stamp` (meeting) — №322
- `channel_labels` (audio) → `speaker_names` (meeting) — №322
- `diarize` (audio) → `llm` (llm) — №322 (LLM.complete для имён спикеров — вызов точки входа)

## Точки входа — исполняемые файлы (кто зовёт из кода)

- `app/make_app.sh` ← .github/workflows/release-app.yml, scripts/doctor.py, scripts/make_dmg.sh
- `scripts/bench_extract.py` ← ручной запуск: бенчмарк извлечения, ручной прогон
- `scripts/bench_models.py` ← ручной запуск: бенчмарк моделей, ручной прогон
- `scripts/build_app_icon.sh` ← ручной запуск: сборка иконки приложения руками
- `scripts/build_embedded_python.sh` ← .github/workflows/release-app.yml, app/make_app.sh
- `scripts/check_private_markers.py` ← .github/workflows/supply-chain.yml, .pre-commit-config.yaml
- `scripts/check_test_assertions.py` ← .github/workflows/ci.yml, .pre-commit-config.yaml
- `scripts/cloud_review.py` ← src/graph_updater.py
- `scripts/dedup_archive.py` ← ручной запуск: разовая уборка дублей архива руками
- `scripts/dedup_graph.py` ← scripts/nightly.sh
- `scripts/diar_bench.py` ← ручной запуск: бенчмарк диаризации, ручной прогон
- `scripts/doctor.py` ← src/deps.py
- `scripts/fix_action_items.py` ← ручной запуск: разовая починка поручений руками
- `scripts/forget_meeting.py` ← app/Sources/CharoiteApp/Services/MeetingActionsService.swift
- `scripts/get_models.py` ← app/Sources/CharoiteApp/Services/ModelPullService.swift, scripts/diar_bench.py, scripts/doctor.py, src/diarize_live.py, src/stt.py
- `scripts/graph_doctor.py` ← scripts/nightly.sh
- `scripts/graph_search_index.py` ← scripts/nightly.sh
- `scripts/import_meeting.py` ← app/Sources/CharoiteApp/Services/ImportService.swift, scripts/doctor.py, src/daemon.py
- `scripts/layout_map.py` ← ручной запуск: сторож раскладки: ручной прогон и pytest — точкой входа продукта не является
- `scripts/lock_runtime_deps.py` ← scripts/build_embedded_python.sh
- `scripts/make_dmg.sh` ← .github/workflows/release-app.yml
- `scripts/memory_bench.py` ← scripts/doctor.py, scripts/nightly.sh
- `scripts/merge_graphs.py` ← ручной запуск: слияние графов руками
- `scripts/migrate_placeholders.py` ← ручной запуск: разовая миграция заглушек руками
- `scripts/morning_brief.py` ← scripts/nightly.sh
- `scripts/mutate_check.py` ← ручной запуск: мутационная проверка тестов, ручной прогон без места в CI (№67)
- `scripts/nightly.sh` ← app/Sources/CharoiteApp/Services/NightlyStatusService.swift, app/Sources/CharoiteApp/Views/Settings/SettingsView.swift
- `scripts/nightly_claude_cores.py` ← scripts/nightly.sh
- `scripts/nightly_dossier.py` ← scripts/nightly.sh
- `scripts/nightly_dossier_review.py` ← scripts/nightly.sh
- `scripts/notarize.sh` ← .github/workflows/release-app.yml
- `scripts/protocol.py` ← app/Sources/CharoiteApp/Services/MeetingActionsService.swift
- `scripts/rename_meeting.py` ← app/Sources/CharoiteApp/Services/MeetingCard.swift
- `scripts/sign_release_manifest.py` ← .github/workflows/release-app.yml
- `scripts/stt_bench.py` ← ручной запуск: бенчмарк STT, ручной прогон
- `scripts/tier3_cores.py` ← scripts/nightly.sh, src/graph_updater.py
- `scripts/wait_for_idle.py` ← scripts/nightly.sh
- `src/daemon.py` ← app/Sources/CharoiteApp/Models/AppSettings.swift, app/Sources/CharoiteApp/Services/SetupReadinessService.swift, app/Sources/CharoiteApp/Services/SuflerService.swift, app/Sources/CharoiteApp/Views/Settings/SettingsView.swift
- `src/diarize.py` ← ручной запуск: диаризация одной записи из терминала ради замеров; конвейер зовёт модуль импортом, не процессом
- `src/dictate.py` ← app/Sources/CharoiteApp/Services/DictationService.swift
- `src/dictate_note.py` ← app/Sources/CharoiteApp/Services/DictationService.swift, scripts/import_meeting.py
- `src/graph_updater.py` ← scripts/import_meeting.py, src/mcp_server.py, src/rebuild_transcript.py, src/transcribe_file.py
- `src/main.py` ← scripts/doctor.py
- `src/mcp_server.py` ← ручной запуск: запускает конфиг настольного MCP-клиента вне репозитория
- `src/meeting_archive.py` ← ручной запуск: разовая миграция архива `--all` руками
- `src/rebuild_transcript.py` ← app/Sources/CharoiteApp/Services/MeetingProcessingService.swift, scripts/doctor.py, src/daemon.py
- `src/retro_fill.py` ← scripts/import_meeting.py
- `src/transcribe_file.py` ← scripts/import_meeting.py
- `src/voice_memos_bridge.py` ← ручной запуск: мост Диктофона — отдельный процесс, поднимается руками до №210

## Пути, названные кодом, но не исполняемые (подсказки и сообщения)

- `src/charoite_paths.py` ← scripts/layout_map.py
- `src/graph_search.py` ← scripts/memory_bench.py
- `src/llm_health.py` ← scripts/doctor.py
- `src/privacy.py` ← scripts/doctor.py

## Пути, названные в документации и конфигах

- `app/make_app.sh` ← README.md, app/README.md, docs/RELEASING.md, docs/SETUP.md, docs/ru/README.md, docs/ru/RELEASING.md, docs/ru/SETUP.md, docs/ru/app/README.md, docs/ru/scripts/README.md, docs/zh/README.md, docs/zh/RELEASING.md, docs/zh/SETUP.md, docs/zh/app/README.md, docs/zh/scripts/README.md, scripts/README.md
- `scripts/bench_extract.py` ← README.md, docs/MODELS.md, docs/ru/MODELS.md, docs/ru/README.md, docs/zh/MODELS.md, docs/zh/README.md
- `scripts/bench_models.py` ← docs/MODELS.md, docs/ru/MODELS.md, docs/zh/MODELS.md
- `scripts/build_app_icon.sh` ← docs/ru/scripts/README.md, docs/zh/scripts/README.md, scripts/README.md
- `scripts/build_embedded_python.sh` ← README.md, docs/SETUP.md, docs/ru/README.md, docs/ru/SETUP.md, docs/ru/scripts/README.md, docs/zh/README.md, docs/zh/SETUP.md, docs/zh/scripts/README.md, scripts/README.md
- `scripts/check_private_markers.py` ← CONTRIBUTING.md, MAINTENANCE.md, docs/ru/CONTRIBUTING.md, docs/ru/MAINTENANCE.md, docs/zh/CONTRIBUTING.md, docs/zh/MAINTENANCE.md
- `scripts/check_test_assertions.py` ← CONTRIBUTING.md, docs/ru/CONTRIBUTING.md
- `scripts/dedup_archive.py` ← docs/DATA_AND_RECOVERY.md, docs/FEATURES.md, docs/ru/DATA_AND_RECOVERY.md, docs/ru/FEATURES.md, docs/zh/DATA_AND_RECOVERY.md, docs/zh/FEATURES.md
- `scripts/dedup_graph.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md
- `scripts/diar_bench.py` ← docs/DIARIZATION.md, docs/ru/DIARIZATION.md, docs/zh/DIARIZATION.md
- `scripts/doctor.py` ← README.md, docs/ARCHITECTURE.md, docs/DATA_AND_RECOVERY.md, docs/SETUP.md, docs/USER_GUIDE.md, docs/ru/ARCHITECTURE.md, docs/ru/DATA_AND_RECOVERY.md, docs/ru/README.md, docs/ru/SETUP.md, docs/ru/USER_GUIDE.md, docs/zh/ARCHITECTURE.md, docs/zh/DATA_AND_RECOVERY.md, docs/zh/README.md, docs/zh/SETUP.md, docs/zh/USER_GUIDE.md, pyproject.toml
- `scripts/fix_action_items.py` ← docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `scripts/forget_meeting.py` ← PRIVACY.md, docs/DATA_AND_RECOVERY.md, docs/FEATURES.md, docs/ru/DATA_AND_RECOVERY.md, docs/ru/FEATURES.md, docs/ru/PRIVACY.md, docs/zh/DATA_AND_RECOVERY.md, docs/zh/FEATURES.md, docs/zh/PRIVACY.md
- `scripts/get_models.py` ← PRIVACY.md, README.md, ROADMAP.md, SECURITY.md, config/config.example.zh.yaml, docs/DIARIZATION.md, docs/FEATURES.md, docs/MODELS.md, docs/ru/DIARIZATION.md, docs/ru/FEATURES.md, docs/ru/MODELS.md, docs/ru/PRIVACY.md, docs/ru/README.md, docs/ru/ROADMAP.md, docs/ru/SECURITY.md, docs/zh/DIARIZATION.md, docs/zh/FEATURES.md, docs/zh/MODELS.md, docs/zh/PRIVACY.md, docs/zh/README.md, docs/zh/ROADMAP.md, docs/zh/SECURITY.md
- `scripts/graph_doctor.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md
- `scripts/import_meeting.py` ← PRIVACY.md, README.md, docs/DATA_AND_RECOVERY.md, docs/FEATURES.md, docs/USER_GUIDE.md, docs/ru/DATA_AND_RECOVERY.md, docs/ru/FEATURES.md, docs/ru/PRIVACY.md, docs/ru/README.md, docs/ru/USER_GUIDE.md, docs/zh/DATA_AND_RECOVERY.md, docs/zh/FEATURES.md, docs/zh/PRIVACY.md, docs/zh/README.md, docs/zh/USER_GUIDE.md
- `scripts/layout_map.py` ← CONTRIBUTING.md, docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md, packages/README.md
- `scripts/lock_runtime_deps.py` ← SECURITY.md, docs/ru/SECURITY.md, docs/zh/SECURITY.md, requirements-runtime.in
- `scripts/make_dmg.sh` ← docs/RELEASING.md
- `scripts/memory_bench.py` ← CONTRIBUTING.md, README.md, config/memory_bench.example.yaml, demo/README.md, docs/ARCHITECTURE.md, docs/FEATURES.md, docs/ru/ARCHITECTURE.md, docs/ru/CONTRIBUTING.md, docs/ru/FEATURES.md, docs/ru/README.md, docs/ru/demo/README.md, docs/zh/ARCHITECTURE.md, docs/zh/CONTRIBUTING.md, docs/zh/FEATURES.md, docs/zh/README.md, docs/zh/demo/README.md
- `scripts/merge_graphs.py` ← docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `scripts/migrate_placeholders.py` ← docs/ru/ARCHITECTURE.md
- `scripts/morning_brief.py` ← docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `scripts/mutate_check.py` ← CONTRIBUTING.md, docs/ru/CONTRIBUTING.md
- `scripts/nightly.sh` ← config/memory_bench.example.yaml, docs/ARCHITECTURE.md, docs/FEATURES.md, docs/SETUP.md, docs/ru/ARCHITECTURE.md, docs/ru/FEATURES.md, docs/ru/SETUP.md, docs/ru/scripts/README.md, docs/zh/FEATURES.md, docs/zh/SETUP.md, docs/zh/scripts/README.md, scripts/README.md
- `scripts/nightly_dossier.py` ← docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `scripts/nightly_dossier_review.py` ← docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `scripts/notarize.sh` ← docs/RELEASING.md
- `scripts/protocol.py` ← docs/FEATURES.md, docs/USER_GUIDE.md, docs/ru/FEATURES.md, docs/ru/USER_GUIDE.md, docs/zh/FEATURES.md, docs/zh/USER_GUIDE.md
- `scripts/rename_meeting.py` ← docs/DATA_AND_RECOVERY.md, docs/FEATURES.md, docs/ru/DATA_AND_RECOVERY.md, docs/ru/FEATURES.md, docs/zh/DATA_AND_RECOVERY.md, docs/zh/FEATURES.md
- `scripts/sign_release_manifest.py` ← docs/RELEASING.md, docs/ru/RELEASING.md, docs/zh/RELEASING.md
- `scripts/stt_bench.py` ← docs/FEATURES.md, docs/MODELS.md, docs/ru/FEATURES.md, docs/ru/MODELS.md, docs/zh/FEATURES.md, docs/zh/MODELS.md
- `scripts/tier3_cores.py` ← config/config.example.en.yaml, config/config.example.yaml, docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `src/brain.py` ← docs/FEATURES.md, docs/design/OVERHAUL_2026-08.md, docs/ru/FEATURES.md
- `src/channel_labels.py` ← docs/ARCHITECTURE.md, docs/design/OVERHAUL_2026-08.md, docs/ru/ARCHITECTURE.md
- `src/charoite_paths.py` ← CONTRIBUTING.md, docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md
- `src/cloud.py` ← docs/ARCHITECTURE.md, docs/MODELS.md, docs/ru/ARCHITECTURE.md, docs/ru/MODELS.md, docs/zh/MODELS.md
- `src/config_loader.py` ← docs/design/OVERHAUL_2026-08.md
- `src/daemon.py` ← SECURITY.md, docs/ARCHITECTURE.md, docs/SETUP.md, docs/ru/ARCHITECTURE.md, docs/ru/SECURITY.md, docs/ru/SETUP.md, docs/zh/ARCHITECTURE.md, docs/zh/SECURITY.md, docs/zh/SETUP.md
- `src/deps.py` ← docs/SETUP.md, docs/ru/SETUP.md, docs/zh/SETUP.md
- `src/dossier.py` ← docs/FEATURES.md, docs/design/UI_REVISION_2026-08.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `src/file_locks.py` ← docs/ARCHITECTURE.md, docs/design/OVERHAUL_2026-08.md, docs/ru/ARCHITECTURE.md
- `src/frontmatter.py` ← docs/ru/ARCHITECTURE.md
- `src/graph_links.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md
- `src/graph_nodes.py` ← docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `src/graph_search.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md
- `src/graph_updater.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md
- `src/live_gate.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md
- `src/llm.py` ← config/config.example.yaml, docs/ARCHITECTURE.md, docs/FEATURES.md, docs/ru/ARCHITECTURE.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `src/main.py` ← README.md, docs/SETUP.md, docs/ru/README.md, docs/ru/SETUP.md, docs/zh/README.md, docs/zh/SETUP.md
- `src/mcp_server.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md
- `src/meeting_archive.py` ← docs/ARCHITECTURE.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md
- `src/nli.py` ← docs/ARCHITECTURE.md, docs/FEATURES.md, docs/ru/ARCHITECTURE.md, docs/ru/FEATURES.md, docs/zh/ARCHITECTURE.md, docs/zh/FEATURES.md, pyproject.toml
- `src/privacy.py` ← MANIFESTO.md, PRIVACY.md, SECURITY.md, docs/ARCHITECTURE.md, docs/FEATURES.md, docs/ru/ARCHITECTURE.md, docs/ru/FEATURES.md, docs/ru/MANIFESTO.md, docs/ru/PRIVACY.md, docs/ru/SECURITY.md, docs/zh/FEATURES.md, docs/zh/MANIFESTO.md, docs/zh/PRIVACY.md, docs/zh/SECURITY.md
- `src/rebuild_transcript.py` ← docs/ARCHITECTURE.md, docs/DATA_AND_RECOVERY.md, docs/USER_GUIDE.md, docs/ru/ARCHITECTURE.md, docs/ru/DATA_AND_RECOVERY.md, docs/ru/USER_GUIDE.md, docs/zh/ARCHITECTURE.md, docs/zh/DATA_AND_RECOVERY.md, docs/zh/USER_GUIDE.md
- `src/redirects.py` ← docs/ru/ARCHITECTURE.md
- `src/speaker_names.py` ← docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `src/stt_runtime.py` ← CONTRIBUTING.md, docs/ru/CONTRIBUTING.md
- `src/tier3.py` ← config/config.example.en.yaml, config/config.example.yaml, docs/FEATURES.md, docs/ru/FEATURES.md, docs/zh/FEATURES.md
- `src/transcript.py` ← docs/ARCHITECTURE.md, docs/design/OVERHAUL_2026-08.md, docs/ru/ARCHITECTURE.md, docs/zh/ARCHITECTURE.md

## Голые имена без цели в репозитории (чужие или порождаемые скрипты — справка)

- `replace.sh` ← app/Sources/CharoiteApp/Services/UpdateService.swift

## Область замера (таблица KINDS сторожа; кандидаты в точки входа — всегда код)

- `docs/design/layout.md` — out (git): карта — производная замера, не источник
- `tests/` — out (git): тесты строят синтетические деревья: пути в них — не факты о репозитории
- `app/Tests/` — out (git): Swift-тесты приложения: те же выдуманные пути
- `app-ios/` — out (git): телефон python и shell не запускает — пути там только в тексте
- `app-android/` — out (git): телефон python и shell не запускает — пути там только в тексте
- `docs/reviews/` — history (git): датированные ревью описывают код своего дня — после переезда не правятся
- `devlog/_posts/` — history (git): датированные посты — снимок своего дня
- `CHANGELOG.md` — history (git): релизные заметки — снимок своего дня, записи о вышедших версиях не правятся
- `app/build/` — out (walk): сборка
- `app/.build/` — out (walk): сборка
- `.build/` — out (walk): сборка
- `build/` — out (walk): сборка
- `.venv/` — out (walk): окружение
- `node_modules/` — out (walk): чужой код
- `.git/` — out (walk): служебный каталог git
- `app/` — code (git): приложение зовёт python и shell
- `scripts/` — code (git): скрипты зовут друг друга и модули; проза по суффиксу (README)
- `src/` — code (git): модули зовут скрипты и подсказывают пути человеку
- `packages/` — code (git): дистрибутивы: модуль пакета — тот же продукт, что модуль src/
- `.github/` — code (git): workflow CI — источник запуска
- `.pre-commit-config.yaml` — code (git): хуки — источник запуска
