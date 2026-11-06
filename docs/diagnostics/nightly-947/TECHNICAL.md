# Техническое приложение к №947

## Точные снимки и импорты

До: `f0a3ce57568efb5e2246fd25e627fa76114e152f`; после: `1ae08c23c74ee1b0a3799e137c27c1c1b24b7962`.
Из каждого SHA штатным `git archive --format=tar <SHA> src scripts` получены только 143/144 tracked-файла; `.git`, файлы владельца и checkout-shadow в архивы не входят. Сжатие — `gzip.compress(tar_bytes, mtime=0)`.
Полные manifests находятся в [source-archives](source-archives/before-manifest.json). Gzip-архивы доставлены отдельно в review-пакете и не добавляются в Git: штатный opaque-binary гейт и owner allowlist не менялись; сами архивы воспроизводимы из указанных Git SHA. Каждый entry содержит Git blob OID, SHA256, размер и mode. Контроль проверяет ожидаемую SHA, Git archive PAX identity, хеш архива и байты всех entries, извлекает их в новый собственный каталог и сверяет полный состав до/после опыта.

Импорты выполняются из архива через проверяющий SourceFileLoader; `.pyc` не используется для target/third-party `.py`. Отказ при target-модуле вне архива, неожиданном import-root, изменённом blob или shadow-файле. В квитанциях перечислены 28/29 исполненных модулей продукта; после изменения отдельно обязателен `src/dossier_incremental.py`. Зафиксированы 81 файл внешних зависимостей, 162 файла stdlib, 44 builtin/frozen имени и SHA256 бинарника интерпретатора. Их привязки совпали между процессами; builtin/frozen код связан с интерпретатором, а не отдельным исходником.

Отдельный readback сопоставил весь состав архивов и все blob OID с `git ls-tree` обеих ожидаемых SHA, затем проверил исполненный target closure по manifests. Итого 24 проверки в [causal-binding.json](causal-binding.json). Это точные архивные снимки; прежнее утверждение о чистом checkout само по себе доказательством не считается.

## Измеренный запрет, а не константы HTTP/model

Процессы запущены через `sandbox-exec -f network-deny.sb <python> -I -S causal-control.py ...`. `-I -S` исключает CWD, `.pth` и `sitecustomize`. До любых target/third-party импортов установлены audit/socket/process tripwires. Неожиданное срабатывание — BaseException, неподверженный обычному `except Exception`; неожиданное AssertionError также не поглощается.

Профиль [network-deny.sb](network-deny.sb) запрещает `network*` и `process-fork`. Независимый libc/socket/connect probe обходит Python hooks и наблюдает OS-отказ EPERM; если отказа нет, процесс завершается до target-импортов. На каждом снимке измерены 7 Python-отказов: создание socket, create_connection, connect_ex, sendto, DNS, subprocess и shell. Счётчик неожиданных target-срабатываний вычислен из накопленных событий, а не записан литералом. Поля `actual_http_requests: 0` / `real_generation_requests: 0` удалены; измеряются boundary events и входы в адаптер-двойник.

Явная одинаковая offline-фикстура `socket.has_ipv6=False` исключает urllib3 import-time IPv6 capability probe; она не разрешает сеть и не имитирует модель. Предыдущая попытка без этой фикстуры действительно упала на раннем socket trap и сохранена в [archive-import-denied.json](intermediate/archive-import-denied.json). Первая новая попытка также сохранена: [ошибка нормализации temp-root](intermediate/archive-first-failed.json).

## Адресная матрица и границы вывода

Те же три literal-входа, конфиги и adapter-double исполнены в двух новых процессах. Короткий 8192 допускается обоими; длинный 8192 раньше доходил до двойника, теперь получает context refusal до constructor/stream; длинный 32768 допускается обоими. Старый длинный prompt 6957 bytes обрезан, новый 17913 bytes полон. Это совместный эффект full-source и admission, не изолированный guard-effect, не качество ответа, не реальный сервер и не причина инцидента.

В каждом процессе отрицательные проверки отвергли неверную expectedSHA, добавленный shadow.py и изменённый исходный blob. Положительная привязка проверяет ожидаемые outcomes, одинаковые input/config/control/interpreter/dependency hashes, полноту нового источника и dossier_incremental closure. Квитанции: [до](causal-before.json), [после](causal-after.json).

Исторические 213 pytest PASS / 5.83s не повторялись. Их изоляция транспорта была свойством тестовых doubles, без независимого сетевого счётчика; текущая measured boundary не приписывается тому запуску. Исправленные пояснения — [synthetic-result.json](synthetic-result.json); первоначальные квитанции/длинный отчёт/контроль сохранены в intermediate/b7a73b57. Исторические input-plan/journal описывают соответствующий этап, а принятый source/network proof — только текущие квитанции и это приложение.

## Повтор

Нужны штатные quiet_guard&&heavy_run и macOS sandbox-exec. Каждый SHA запускается отдельным `python -I -S` с `--archive`, `--manifest`, `--expected-sha`, одобренным `--dependencies` и `--sandbox-profile`. Исходники проверяются по архиву; Git, сеть и дочерние процессы внутри target-run не используются. Результат не заменяет диагностику реального ночного цикла. Runtime/private-материалы в приложении и архивах отсутствуют.
