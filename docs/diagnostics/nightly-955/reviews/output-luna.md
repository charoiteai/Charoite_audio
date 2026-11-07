# Независимое OUTPUT ревью №955 — Luna

**Вердикт:** staged source candidate принят с Important и Minor замечаниями. Это ревью исходников ограниченного публичного пакета, а не проверка установленного runtime. Все 13 записей `MANIFEST.json` совпали с SHA-256 содержимым. Команды проекта, тесты и модели не запускались. Заявленная в пакете незакрытая граница удержания FD остается блокером установки; это не новая находка ревью.

## Important

- **Сценарий:** регрессия, при которой started-marker всё ещё запрещает повтор, хотя исходный текст трансформировался ожидаемым образом. `test_only_exact_selected_lines_change_and_slot_gate_is_enabled` проверяет байты результата в синтетическом fixture, а `test_retry_adapter_contract.py` запускает только `with_process_lock.py` и `fake_nightly.py`; fake-команда вообще не читает started-marker. Поэтому тестовый набор не исполняет один и тот же synthetic wrapper до/после и не доказывает заявленный сценарий «до — отказ по marker, после — retry».
  **Путь:** `tests/test_stage_retry_wrapper.py:33-41`; `tests/test_retry_adapter_contract.py:25-31`; `docs/diagnostics/nightly-955/README.md:45-51`.
  **Минимальное исправление:** добавить synthetic shell fixture с теми же двумя marker-блоками и fake command/status/artifact; исполнить исходный вариант и байтово трансформированный вариант, проверив отказ до преобразования и повтор после него. Сохранить текущую точную проверку формы/хеша; не включать private wrapper.

## Minor

- **Сценарий:** bash syntax check или чтение helper/nightly завершается ошибкой после создания output dir и записи `wrapper.sh`. `stage()` возвращает общий `staging_refused`, но оставляет частичный каталог; повтор с тем же `--output` затем откажет на `mkdir(..., exist_ok=False)`, и оператор не получает путь из stdout, чтобы понять, какой частичный кандидат остался.
  **Путь:** `scripts/stage_retry_wrapper.py:68-75`, `scripts/stage_retry_wrapper.py:78-90`, `scripts/stage_retry_wrapper.py:103-107`.
  **Минимальное исправление:** писать во временный каталог с режимом 0700 и очищать только созданный этим вызовом каталог при любой ошибке; либо оставить частичный каталог, но явно возвратить фиксированный код/сообщение с безопасным способом восстановления без раскрытия приватных путей.

## Проверенные границы

- Lock adapter использует `O_NOFOLLOW`, `fstat` для regular file/владельца/единственной ссылки, проверяет каталог, берет `flock` через существующий primitive, делает FD inheritable и передает argv через `execvpe` без shell. Busy возвращает 75; отказ и exec error ненулевые. Заявленная область удержания lock корректно ограничена процессами, продолжающими держать FD (`scripts/with_process_lock.py:2-8,23-55`).
- Staging привязан к ожидаемому исходному SHA и форме строк, пишет candidate/manifest только в новый каталог вне распознаваемых Git worktree, не изменяет источник и не ставит файл на установку (`scripts/stage_retry_wrapper.py:18-45,48-95`). Вывод не раскрывает пути или текст wrapper; manifest живет внутри каталога с правами 0700, содержимое файлов — 0600.
- Synthetic тесты содержат contention, exec failure, отказ symlink/hardlink/FIFO, закрытие FD и retained/close_fds descendant counterexample. Они корректно не заявляют, что close_fds-потомок защищен (`tests/test_process_lock.py:81-152`). Проверка поведения полного приватного wrapper, фактических UX/child границ и установки остается вне подтвержденного результата.
- В `docs/design/layout.json` обе критичные поверхности регистрации присутствуют: `locked_exec`, `outside_git` и `stage` в `mutation_critical`; тесты ссылаются на synthetic сценарии. Мутации и прогон не выполнялись, поэтому это только проверка деклараций, не подтверждение гейтов.
