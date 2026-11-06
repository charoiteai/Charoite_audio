# №954: журнал ограниченной реализации

- 2026-11-06 08:33 +03:00: база 2f706a3, отдельный worktree/ветка; приватные файлы не копировались. Ресурсные ограничения сохранены.
- До кода: прочитаны 947/954, досье передачи, инструкции продукта и диагностический REPORT. Создан PLAN.
- INPUT: gpt-6.1-terra high C0/I2/M1; gpt-6-luna high C0/I2/M0. Все находки приняты, уточнения — в PLAN.
- Baseline: quiet_guard && heavy_run, transport/incremental/queue: 185 PASS / 6.14 с.
- Реализация: context-local collector; явные admission/contract/stream boundaries; один request outcome, отдельно final topic refusal.
- Адресные причинные проверки: 20 PASS / 0.81 с. Промежуточный dossier/nightly набор: 1302 PASS / 85.55 с.
- После уменьшения инструментального diff и учёта исключения индекса: targeted 260 PASS / 57.97 с.
- Найден и исправлен scan-time unreadable accounting: 77 PASS / 5.17 с, из них 21 новая causal-проверка; requests не выдумываются.
- Code commit a8926a41: обязательные hooks PASS, 12 файлов. Дополнение 958 сохраняется отдельным коммитом.
- №958: layout --check выявил 2 historical controls без KINDS + устаревшую карту (3 нарушения); точная классификация двух файлов/history и регенерация карты → PASS.
- 08:46 +03:00: новых model calls 0. OUTPUT/CI/синтетическая финальная матрица ещё не завершены.

- OUTPUT49d9: gpt-6.1-terra high C0/I1/M1; gpt-6-luna high C0/I0/M0. Обе находки Terra приняты и причинно подтверждены 2FAIL/2PASS.
- stream_error для типизированного HTTP200 и сохранение исходного исключения при сбое stdout: 209 PASS / 5.85с, ruff PASS.
- Команда адресного повтора: quiet_guard.sh && heavy_run.sh <project-python> -m pytest -q tests/test_dossier_telemetry_954.py tests/test_dossier_ollama_contract.py tests/test_dossier_transport_contract.py tests/test_dossier_encoding.py.
- Ранее: те же guard wrappers + pytest tests/test_dossier_transport_contract.py tests/test_dossier_incremental.py tests/test_dossier_queue.py (baseline185); после реализации добавлен tests/test_nightly_exit.py (260).

- Публикационная ошибка исполнителя: предварительный raw-list grep отказал, но зависимый push1bee ошибочно продолжился из-за отдельных shell-команд. Последующие push/PR остановлены root до разбора; обхода штатных Git hooks не было.
- Разбор выявил отличие raw substring/binary matching от канонического Unicode boundary scanner. Безопасная приватная квитанция сохранена отдельно, значения маркеров/совпавшие строки не публикуются. Нативный commit/pre-push scanner прошёл; дальнейшая доставка ждёт оценки root.
- Root принял причинный разбор false positives и разрешил продолжение при штатных scanner/hooks. Raw grep не объявлен зелёным; отдельный долг канонической команды — №959. Синтетический Unicode-boundary контроль воспроизводим отдельным скриптом без real marker-list.

- PR910 создан штатным GitHub connector от аккаунта продукта после отказа collaborator-права у CLI; auth CLI не переключался. Head6b5369e4, source/test7hashes точно совпали с предыдущей проверкой.
- Первый PR-CI42171863496: mutation verdict FAIL — база 1FAIL/664PASS, 49 в плане/48critical/судилось0. Пропущенное ожидание старой фразы в forget-regression приведено к нейтральному build-refusal, отрицательная проверка encoding также обновлена. Продуктовый код не менялся.
- Адресная проверка двух изменённых regression-наборов и telemetry: quiet_guard && heavy_run, 92 PASS / 7.60с. Новая CI обязательна; прежние 0/48critical зелёными не считаются.

- PR-CI42172791582: Linux: 11081 PASS / 2 FAIL (READERS/KINDS утверждённые копии); mutation: 49/49 judged, 15 critical + 1 noncritical survived. Все findings в текущей №954, не отложены.
- Расширены сквозные сценарии и настоящий direct CLI import для mutation tests_for; обе утверждённые копии обновлены точными записями. Порядок JSON-ключей не включён в контракт; sort_keys устранён.
- Подтверждён новый lifecycle FAIL: empty first reply → preparation error на retry оставлял прежнюю причину. Теперь start-attempt очищает атрибуцию текущего отказа, сохранённая история requests прежняя; implicit bool-switch заменён явной optional Reason и двумя отказными функциями.
- Адресно: 173 PASS / 69.36 с; отдельный clean-source replay штатных AST-операторов: baseline: 35 PASS, 11 retained survivors killed / 0 survived. 5 удалённых переключателей отсутствуют, не whitelist. Receipt survivor-replay.json.
- 09:29 +03:00: далее точный source snapshot, обе final-risk модели, обычные hooks/push и вся CI той же SHA; реальных моделей/owner-data 0.

- Resume: обе final-risk модели на6b317 C0/I1; исходные raw-вердикты сохранены в docs/reviews/2026-11-06-nightly-954.
- Причинный before:4FAIL/7PASS. Index RuntimeError после последней темы не учитывался; emitter RuntimeError/второй KeyboardInterrupt/SystemExit маскировали исходное исключение.
- Common write_index boundary ставит один графовый failure flag, cleanup суммирует флаги один раз. При body-error cleanup не подменяет исходный отказ; при нормальном body индексный отказ виден.
- Эмиссия защищает исходный exception object от любого BaseException, без исходной ошибки сама ошибка вывода распространяется.
- Дополнительная защита: чужой уже обработанный caller-except не является ошибкой run_all. Этот контроль сначала дал1FAIL/1PASS при sys.exc_info(), после локального unwind-флага прошёл.
- Адресно198PASS/12.82с; отдельный clean replay6risk mutants killed/0survived. Ruff PASS; обязательная новая CI ещё впереди.

- CI42178354744/e66: Linux: 11104 PASS / macOS: 11116 PASS, merge snapshot200dcdd2. Все 55 critical рассуждены, 1 survivor; verdict FAIL.
- Удалена лишняя автозапись final topic.reason из request observer (or→and survivor): финальный отказ уже назначает rejecting gate. Request failure/history сохраняются. Исключения/waiver/критичность не менялись.
- Адресно: 184 PASS / 68.20 с, telemetry 46+failure/import contracts; повтор risk replay: 6 killed / 0 survived. Новая CI обязательна.
