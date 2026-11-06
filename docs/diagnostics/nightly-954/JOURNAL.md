# №954: журнал ограниченной реализации

- 2026-11-06 08:33 +03:00: база 2f706a3, отдельный worktree/ветка; приватные файлы не копировались. Quota 68% / 10080.
- До кода: прочитаны 947/954, досье передачи, инструкции продукта и диагностический REPORT. Создан PLAN.
- INPUT: gpt-6.1-terra high C0/I2/M1; gpt-6-luna high C0/I2/M0. Все находки приняты, уточнения — в PLAN.
- Baseline: quiet_guard && heavy_run, transport/incremental/queue: 185 PASS / 6.14 с.
- Реализация: context-local collector; явные admission/contract/stream boundaries; один request outcome, отдельно final topic refusal.
- Адресные причинные проверки: 20 PASS / 0.81 с. Промежуточный dossier/nightly набор: 1302 PASS / 85.55 с.
- После уменьшения инструментального diff и учёта исключения индекса: targeted 260 PASS / 57.97 с.
- Найден и исправлен scan-time unreadable accounting: 77 PASS / 5.17 с, из них 21 новая causal-проверка; requests не выдумываются.
- Code commit a8926a41: обязательные hooks PASS, 12 файлов. Дополнение 958 сохраняется отдельным коммитом.
- №958: layout --check выявил 2 historical controls без KINDS + устаревшую карту (3 нарушения); точная классификация двух файлов/history и регенерация карты → PASS.
- 08:46 +03:00: live quota 68% / 10080, новых model calls 0. OUTPUT/CI/синтетическая финальная матрица ещё не завершены.

- OUTPUT49d9: gpt-6.1-terra high C0/I1/M1; gpt-6-luna high C0/I0/M0. Обе находки Terra приняты и причинно подтверждены 2FAIL/2PASS.
- stream_error для типизированного HTTP200 и сохранение исходного исключения при сбое stdout: 209 PASS / 5.85с, ruff PASS.
- Команда адресного повтора: quiet_guard.sh && heavy_run.sh <project-python> -m pytest -q tests/test_dossier_telemetry_954.py tests/test_dossier_ollama_contract.py tests/test_dossier_transport_contract.py tests/test_dossier_encoding.py.
- Ранее: те же guard wrappers + pytest tests/test_dossier_transport_contract.py tests/test_dossier_incremental.py tests/test_dossier_queue.py (baseline185); после реализации добавлен tests/test_nightly_exit.py (260).

- Публикационная ошибка исполнителя: предварительный raw-list grep отказал, но зависимый push1bee ошибочно продолжился из-за отдельных shell-команд. Последующие push/PR остановлены root до разбора; обхода штатных Git hooks не было.
- Разбор выявил отличие raw substring/binary matching от канонического Unicode boundary scanner. Безопасная приватная квитанция сохранена отдельно, значения маркеров/совпавшие строки не публикуются. Нативный commit/pre-push scanner прошёл; дальнейшая доставка ждёт оценки root.
- Root принял причинный разбор false positives и разрешил продолжение при штатных scanner/hooks. Raw grep не объявлен зелёным; отдельный долг канонической команды — №959. Синтетический Unicode-boundary контроль воспроизводим отдельным скриптом без real marker-list.
