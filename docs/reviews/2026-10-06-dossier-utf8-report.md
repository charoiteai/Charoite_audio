# Отчёт исполнителя: UTF-8 в ночной сборке досье

База: `charoiteai/Charoite_audio`, `main` **7f3eaa9fbb27ae12f48a35f08998484c1709adef**. Ветка: `fix/dossier-damaged-utf8`. Отчёт для проверки перед PR.

Изменены только `scripts/nightly_dossier.py`, `src/charoite_graph/dossier.py`; добавлен `tests/test_dossier_encoding.py`. Патч применяется к чистой базе (`git apply --check`, код 0). SHA-256 патча: `21165d2d10bbeef2eec9deab0a5752095aad3b8a82cd1421ead69b040576e68b`.

## Результат

- **36/36 новых тестов красные на исходном main**, ни одного зелёного в сданном новом файле. Финальный повтор на базе сделан после усиления тестов, с возвратом обоих исходных файлов через `git show HEAD:<путь>` и восстановлением правки в `finally`.
- **108 тестов зелёные**: весь `tests/test_dossier.py`, весь `tests/test_nightly_cloud_reports.py`, новый `tests/test_dossier_encoding.py`. Выход pytest — 0.
- **44/44 мутационных опыта убиты** после усиления одного утверждения. Каждый опыт меняет поведение отдельно, гоняет новый файл тестов и восстанавливает исходник в `finally`. Засчитывается только выход pytest 1 с упавшими тестами; ошибка сборки не засчитывается.
- `ruff check` по трём изменённым файлам — 0. `git diff --check` — 0.

## Реализация

`DossierLoad` — неизменяемый dataclass: `state` (`ok`, `damaged`, `unreadable`, `missing`) и `text` (`str | None`). `load_dossier` читает байты один раз и строго декодирует весь файл, включая хвост за пределами шапки. Только ошибка строгого декодирования создаёт `damaged`; затем те же байты декодируются с `errors="replace"`. Пустой файл — `ok` с пустой строкой; исходный, корректно закодированный U+FFFD не означает повреждение. Переводы строк нормализуются как при прежнем `Path.read_text`.

`_DossierReads.load` в ночном шаге хранит снимки и множество повреждённых путей. На тему — одна попытка чтения, на повреждённый путь — один диагностический лог. Ветка закрытого окна использует тот же помощник: уже прочитанная текущая тема берётся из кеша, хвост читается по одному разу. `run` не определяет повреждение и не считает его по символам текста.

`_manual_for_rebuild` вызывается до `generate`. Повреждённая секция или не найденный `KEEP_RE` отказывают теме без увеличения `отказы`; `unreadable` увеличивает `отказы`. `_keep_in_index` оставляет прежнее досье в обоих индексах для любого отказа/пропуска. В индекс идут дата и фактический отпечаток из снимка, с прежними запасными значениями. Дата разбирается через `date.fromisoformat` и выводится через `.isoformat()`.

Повреждение само по себе не вызывает пересборку. Условие инкрементальности и `--full` сохранены. `backup` **не изменён**: перед записью вызывается прежний `shutil.copy2`, копия проверяется по исходным байтам. Ошибка копии оставляет файл и индекс темы. Файлы графа при повреждении читаются с заменой, включаются в кластер, логируются по пути; mtime по-прежнему берётся с диска, алгоритм отпечатка не изменён.

Ключ `битые` присутствует в раннем и обычном результате `run`, печатается `main`. Интеграционный тест трёх повреждённых авторских секций проверяет код `main() == 0`, отсутствие вызовов модели и `битые: 3`.

## Список чтений в двух файлах

| Место | До правки | После правки |
|---|---|---|
| `dossier.scan`, бывшая строка 155, теперь 157 | `p.read_text(encoding="utf-8")`, только `OSError` | `p.read_bytes()`; строгая проверка и замена при ошибке UTF-8. Это чтение **файлов графа**, папка досье исключена схемой |
| `dossier.read_fingerprint`, бывшая строка 489, теперь функция 523 | Собственное строгое чтение досье | Переданный снимок; при отдельном вызове с Path — через `load_dossier` |
| `nightly_dossier._собрано`, бывшая строка 291, теперь функция 316 | Собственное строгое чтение досье | Переданный снимок; при отдельном вызове с Path — через `load_dossier`; разбор и нормализация даты |
| `nightly_dossier.run`, бывшая строка 222 | Строгое чтение для «Правок автора» после генерации | Чтение убрано. `_manual_for_rebuild` (106) работает с уже прочитанным текстом до модели |
| `_index_entry_from_disk`, теперь 69 | Косвенно перечитывал досье двумя читателями метаданных | Принимает `DossierLoad`, дополнительных чтений нет |
| `_DossierReads.load` → `dossier.load_dossier`, теперь 508 | Не было | **Единственное чтение байтов досье** в шаге, один раз на путь |
| `dossier.load_index`, бывшая строка 565, теперь 599 | Чтение JSON индекса | Без изменений; это отдельная задача, обработка UnicodeDecodeError не добавлена |

Других чтений содержимого досье в этих двух файлах нет. `Path.exists` при сортировке/проверке инкрементальности — проверка наличия, не чтение текста. `backup` — отдельная побайтовая копия перед мутацией, а не повторная загрузка досье для принятия решения.

## Требуемые случаи и дополнительные проверки

| Случай | Тест/наблюдаемое свойство |
|---|---|
| А: байт в теле | `test_damaged_body_or_fingerprint_rebuilds_every_theme[body]`: три вызова модели, оба индекса, исходные байты в копии, авторская секция сохранена |
| Б: байт в отпечатке | Та же проверка с `[fingerprint]` |
| В: байт в авторском тексте | `test_damaged_author_text_refuses_before_model[manual]`: модель средней темы не вызвана, файл неизменен, тема в индексе, `отказы=0` |
| Г: файл графа | `test_damaged_graph_source_stays_in_cluster`: источник присутствует в фактических members и files, замена видна в тексте, mtime совпадает со stat, лог пути один |
| Д: OSError досье | `test_unreadable_refuses_before_model_and_keeps_index`: PermissionError в `Path.open` только для целевого пути, до модели, индекс сохранён, `отказы=1` |
| Е: дата | `test_collected_date_is_parsed_and_normalized`: мусор → today; `20261006` → `2026-10-06`; `2026-W41-1` → `2026-10-05` |
| Ж: совпал отпечаток | `test_matching_fingerprint_does_not_force_damaged_rebuild`: модель не вызвана, `битые=1`, одно чтение/лог |
| З: нет тем | Пустой граф и граф с узлом без кластера: `битые=0` в обоих выходах |
| И: окно закрыто | `test_closed_window_reads_tail_once`: индекс содержит все три темы, по одному чтению; дополнительно проверен запасной отпечаток **каждой** темы хвоста |
| К: повреждён заголовок | `test_damaged_author_text_refuses_before_model[heading]`: отказ до модели и до мутации, тема в индексе |
| Все соседние ветки | `[limit, model, locked, backup, dry]`: одно чтение, правильный учёт, сохранение байтов/индекса, фактического отпечатка и даты |
| Missing | Новую тему без файла не выдумываем в индекс при лимите/отказе/занятом графе/закрытом окне; после успешной сборки она появляется |
| Строго корректный UTF-8 | Пустой файл, отсутствие заголовка, настоящий U+FFFD, CRLF — без отказа по правилу повреждения |
| Дверь загрузки | Состояния missing/ok/damaged/unreadable; байт после 700 символов; публичные читатели Path проходят через load_dossier |
| --full | Совпавший отпечаток повреждённого файла всё равно пересобирается с исходной байтовой копией |

Синтетический граф имеет три независимые темы размером 5/4/3 источника, так что повреждённая Бета оказывается средней. Прежние досье имеют `отпечаток: старый`. `generate` подменён, вызовы записываются. OSError подменяется в `Path.open`, общем нижнем слое `read_text`/`read_bytes`, а не в `Path.read_text`.

## Опровергающие опыты

В первом круге опыт 15 (подмена сохранённого отпечатка текущим) **выжил**: тесты проверяли наличие темы, но не метаданные прежней записи. Усилена существующая проверка веток отказа/пропуска: утверждаются `отпечаток == "старый"` и исходная дата. Повтор только опыта 15: **4 failed, 32 passed**, выход 1. Исходный результат сохранён в `mutation-results.json` и `mutation-summary.log`; повтор — в `mutation-results-rerun.json`; итог — в `mutation-results-final.json`.

Новые исполняемые участки `run` проверяются опытами: ранний результат — 25; снимок/чтение отпечатка — 10/13; сохранение индекса без изменений/сверх лимита — 31/32; хвост окна и его отпечаток — 33/34; решение до модели и отказ — 42/35/36; соседние отказы модели/замка/копии — 37/38/39; итоговый учёт — 26. Учёт, лог, защита авторского текста и метаданные проверяются отдельными опытами. Неизменённую дверь `backup` дополнительно проверяют опыты 40/41: копия после перезаписи и текстовая копия с заменой недопустимы.

| Опыт | Результат на окончательных утверждениях |
|---|---|
| `01-strict-dossier-decode` | Убит; 16 failed, 20 passed in 1.11s |
| `02-ignore-dossier-invalid-bytes` | Убит; 3 failed, 33 passed in 0.51s |
| `03-damage-unmarked` | Убит; 15 failed, 21 passed in 0.78s |
| `04-check-only-header` | Убит; 1 failed, 35 passed in 0.63s |
| `05-unreadable-is-missing` | Убит; 2 failed, 34 passed in 0.45s |
| `06-missing-is-unreadable` | Убит; 6 failed, 30 passed in 0.57s |
| `07-strict-graph-decode` | Убит; 1 failed, 35 passed in 0.49s |
| `08-skip-damaged-graph-file` | Убит; 1 failed, 35 passed in 0.93s |
| `09-no-graph-path-log` | Убит; 1 failed, 35 passed in 0.43s |
| `10-no-cached-reading` | Убит; 3 failed, 33 passed in 0.66s |
| `11-no-damage-count` | Убит; 14 failed, 22 passed in 0.56s |
| `12-no-dossier-path-log` | Убит; 14 failed, 22 passed in 0.78s |
| `13-fingerprint-rereads` | Убит; 24 failed, 12 passed in 1.08s |
| `14-loaded-date-discarded` | Убит; 1 failed, 35 passed in 0.70s |
| `15-loaded-fingerprint-discarded` | Убит; 4 failed, 32 passed in 0.65s |
| `16-manual-damage-allowed` | Убит; 2 failed, 34 passed in 0.59s |
| `17-broken-heading-allowed` | Убит; 1 failed, 35 passed in 0.65s |
| `18-utf8-replacement-rejected` | Убит; 3 failed, 33 passed in 0.48s |
| `19-unreadable-allowed` | Убит; 1 failed, 35 passed in 0.56s |
| `20-unreadable-not-counted` | Убит; 1 failed, 35 passed in 0.59s |
| `21-damaged-refusal-counted-as-model` | Убит; 3 failed, 33 passed in 0.64s |
| `22-no-manual-preservation` | Убит; 4 failed, 32 passed in 0.54s |
| `23-old-date-parser` | Убит; 4 failed, 32 passed in 0.47s |
| `24-date-not-canonicalized` | Убит; 3 failed, 33 passed in 0.68s |
| `25-no-empty-damage-key` | Убит; 1 failed, 35 passed in 0.54s |
| `26-no-final-damage-count` | Убит; 14 failed, 22 passed in 0.62s |
| `27-no-main-damage-summary` | Убит; 1 failed, 35 passed in 0.51s |
| `28-force-damaged-rebuild` | Убит; 1 failed, 35 passed in 0.64s |
| `29-do-not-keep-existing` | Убит; 18 failed, 18 passed in 0.68s |
| `30-index-missing-dossier` | Убит; 4 failed, 32 passed in 0.52s |
| `31-drop-unchanged-index` | Убит; 1 failed, 35 passed in 0.66s |
| `32-drop-over-limit-index` | Убит; 5 failed, 31 passed in 0.92s |
| `33-drop-late-index` | Убит; 3 failed, 33 passed in 0.44s |
| `34-wrong-late-fingerprint` | Убит; 1 failed, 35 passed in 0.63s |
| `35-drop-refusal-index` | Убит; 4 failed, 32 passed in 0.64s |
| `36-drop-refusal-count` | Убит; 1 failed, 35 passed in 0.47s |
| `37-drop-model-refusal-index` | Убит; 2 failed, 34 passed in 0.58s |
| `38-drop-locked-index` | Убит; 2 failed, 34 passed in 0.50s |
| `39-drop-backup-failure-index` | Убит; 1 failed, 35 passed in 0.51s |
| `40-backup-after-overwrite` | Убит; 4 failed, 32 passed in 0.56s |
| `41-backup-replaces-bad-bytes` | Убит; 12 failed, 24 passed in 0.67s |
| `42-manual-check-after-model` | Убит; 4 failed, 32 passed in 0.55s |
| `43-snapshot-date-reader-bypassed` | Убит; 6 failed, 30 passed in 1.07s |
| `44-snapshot-fingerprint-reader-bypassed` | Убит; 6 failed, 30 passed in 0.56s |

## Воспроизведение

На чистом main применить патч и запустить:

```bash
git apply dossier-utf8.patch
python -m pytest tests/test_dossier.py tests/test_nightly_cloud_reports.py tests/test_dossier_encoding.py -q
python -m ruff check scripts/nightly_dossier.py src/charoite_graph/dossier.py tests/test_dossier_encoding.py
git diff --check
```

Опровергающие опыты запускаются на уже применённом патче. `CHAROITE_CHECKOUT` указывает корень проверяемого checkout:

```bash
CHAROITE_CHECKOUT="$PWD" python /путь/verify_mutations.py
```

Для красного прогона применить только новый тестовый файл к исходному main, без двух продуктовых файлов. Тесты загрузчика дополнительно проверяют новый API (на main его ещё нет); сценарии А–К воспроизводят реальные прежние отказы независимо от формы результата загрузчика.

Среда: Python 3.12.14, pytest 9.1.1, pytest-timeout 2.4.0, ruff 0.16.10. Модели и сеть для тестов не используются. Ограничение среды: общая обвязка conftest сообщает, что audio/daemon/dictate/main не импортировались из-за отсутствующей системной PortAudio. Для этих трёх тестовых файлов аудиозахват не нужен; все 108 тестов выполняются без skip/xfail. Полный тестовый набор проекта и проверки реального облака/моделей не заявляются.

## Полный красный прогон на main

Команда: `python -m pytest tests/test_dossier_encoding.py -q --tb=short`, выход **1**. Ниже весь stdout/stderr финального повторного опыта, без удаления диагностик обвязки:

```text
ВНИМАНИЕ: реестр №329 не импортировался целиком — свидетель корня проверит не всех:
  audio: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/audio.py", line 15, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

  daemon: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/daemon.py", line 57, in <module>
    import live_nemotron  # noqa: E402
    ^^^^^^^^^^^^^^^^^^^^
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/live_nemotron.py", line 71, in <module>
    import audio
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/audio.py", line 15, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

  dictate: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/dictate.py", line 16, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

  main: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/main.py", line 24, in <module>
    from audio import AudioHub, list_devices  # noqa: E402
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/audio.py", line 15, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF                                     [100%]
=================================== FAILURES ===================================
_________ test_damaged_body_or_fingerprint_rebuilds_every_theme[body] __________
tests/test_dossier_encoding.py:104: in test_damaged_body_or_fingerprint_rebuilds_every_theme
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 117: invalid start byte
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104931/
  ✓ Альфа: 5 источников, 77 зн., 0с
______ test_damaged_body_or_fingerprint_rebuilds_every_theme[fingerprint] ______
tests/test_dossier_encoding.py:104: in test_damaged_body_or_fingerprint_rebuilds_every_theme
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 46: invalid start byte
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104931/
  ✓ Альфа: 5 источников, 77 зн., 0с
____________ test_damaged_author_text_refuses_before_model[manual] _____________
tests/test_dossier_encoding.py:122: in test_damaged_author_text_refuses_before_model
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 185: invalid start byte
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104931/
  ✓ Альфа: 5 источников, 77 зн., 0с
____________ test_damaged_author_text_refuses_before_model[heading] ____________
tests/test_dossier_encoding.py:122: in test_damaged_author_text_refuses_before_model
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 141: invalid start byte
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104931/
  ✓ Альфа: 5 источников, 77 зн., 0с
__________________ test_damaged_graph_source_stays_in_cluster __________________
tests/test_dossier_encoding.py:135: in test_damaged_graph_source_stays_in_cluster
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:129: in run
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:155: in scan
    text = p.read_text(encoding="utf-8")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 45: invalid start byte
_____________ test_unreadable_refuses_before_model_and_keeps_index _____________
tests/test_dossier_encoding.py:148: in test_unreadable_refuses_before_model_and_keeps_index
    _all_index(folder)  # main теряет тему именно здесь
    ^^^^^^^^^^^^^^^^^^
tests/test_dossier_encoding.py:83: in _all_index
    assert {e["тема"] for e in entries} == set(THEMES)
E   AssertionError: assert {'Альфа', 'Гамма'} == {'Альфа', 'Бета', 'Гамма'}
E
E     Extra items in the right set:
E     'Бета'
E     Use -v to get more diff
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104931/
  ✓ Альфа: 5 источников, 77 зн., 0с
  ✗ Бета: прежнее досье не прочитано ([Errno 13] Permission denied: '/tmp/pytest-of-root/pytest-52/test_unreadable_refuses_before0/Граф/Досье/Бета.md') — не трогаю
  копия прежнего досье: Досье/.backup/2026-10-06_104931/
  ✓ Гамма: 3 источников, 77 зн., 0с
_ test_collected_date_is_parsed_and_normalized[\u043c\u0443\u0441\u043e\u0440-None] _
tests/test_dossier_encoding.py:163: in test_collected_date_is_parsed_and_normalized
    assert _all_index(folder)["Бета"]["собрано"] == (expected or date.today().isoformat())
E   AssertionError: assert 'мусор' == '2026-10-06'
E
E     - 2026-10-06
E     + мусор
----------------------------- Captured stdout call -----------------------------
  пропущено 3: без изменений 0, сверх лимита 3, граф занят 0, не успели за ночь 0
______ test_collected_date_is_parsed_and_normalized[20261006-2026-10-06] _______
tests/test_dossier_encoding.py:163: in test_collected_date_is_parsed_and_normalized
    assert _all_index(folder)["Бета"]["собрано"] == (expected or date.today().isoformat())
E   AssertionError: assert '20261006' == '2026-10-06'
E
E     - 2026-10-06
E     ?     -  -
E     + 20261006
----------------------------- Captured stdout call -----------------------------
  пропущено 3: без изменений 0, сверх лимита 3, граф занят 0, не успели за ночь 0
_____ test_collected_date_is_parsed_and_normalized[2026-W41-1-2026-10-05] ______
tests/test_dossier_encoding.py:163: in test_collected_date_is_parsed_and_normalized
    assert _all_index(folder)["Бета"]["собрано"] == (expected or date.today().isoformat())
E   AssertionError: assert '2026-W41-1' == '2026-10-05'
E
E     - 2026-10-05
E     + 2026-W41-1
----------------------------- Captured stdout call -----------------------------
  пропущено 3: без изменений 0, сверх лимита 3, граф занят 0, не успели за ночь 0
___________ test_matching_fingerprint_does_not_force_damaged_rebuild ___________
tests/test_dossier_encoding.py:176: in test_matching_fingerprint_does_not_force_damaged_rebuild
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 121: invalid start byte
______________________ test_empty_graph_has_damage_count _______________________
tests/test_dossier_encoding.py:184: in test_empty_graph_has_damage_count
    assert _script().run(tmp_path, {}, full=False, dry=False, limit=12)["битые"] == 0
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   KeyError: 'битые'
______________________ test_closed_window_reads_tail_once ______________________
tests/test_dossier_encoding.py:192: in test_closed_window_reads_tail_once
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:193: in run
    entries.append(_index_entry_from_disk(
scripts/nightly_dossier.py:76: in _index_entry_from_disk
    "собрано": _собрано(path) or today,
               ^^^^^^^^^^^^^^
scripts/nightly_dossier.py:291: in _собрано
    m = re.search(r"^собрано:\s*(\S+)", path.read_text(encoding="utf-8")[:400], re.M)
                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 117: invalid start byte
----------------------------- Captured stdout call -----------------------------
  ⏹ время ночного прогона вышло — остальные темы завтра
_________________ test_every_exit_reuses_loaded_dossier[limit] _________________
tests/test_dossier_encoding.py:211: in test_every_exit_reuses_loaded_dossier
    result = nd.run(graph, {}, full=False, dry=branch == "dry", limit=0 if branch == "limit" else 12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 117: invalid start byte
_________________ test_every_exit_reuses_loaded_dossier[model] _________________
tests/test_dossier_encoding.py:211: in test_every_exit_reuses_loaded_dossier
    result = nd.run(graph, {}, full=False, dry=branch == "dry", limit=0 if branch == "limit" else 12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 117: invalid start byte
----------------------------- Captured stdout call -----------------------------
  … Альфа: попытка 1 — ответ не по формату, повтор
  … Альфа: попытка 2 — ответ не по формату, повтор
________________ test_every_exit_reuses_loaded_dossier[locked] _________________
tests/test_dossier_encoding.py:211: in test_every_exit_reuses_loaded_dossier
    result = nd.run(graph, {}, full=False, dry=branch == "dry", limit=0 if branch == "limit" else 12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 117: invalid start byte
----------------------------- Captured stdout call -----------------------------
  ⏸ Альфа: граф занят соседом дольше 5 мин — тема уйдёт на следующую ночь
________________ test_every_exit_reuses_loaded_dossier[backup] _________________
tests/test_dossier_encoding.py:211: in test_every_exit_reuses_loaded_dossier
    result = nd.run(graph, {}, full=False, dry=branch == "dry", limit=0 if branch == "limit" else 12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 117: invalid start byte
----------------------------- Captured stdout call -----------------------------
  ✗ Альфа: копия прежнего досье не сделана ([Errno 13] Permission denied) — не перезаписываю
__________________ test_every_exit_reuses_loaded_dossier[dry] __________________
tests/test_dossier_encoding.py:211: in test_every_exit_reuses_loaded_dossier
    result = nd.run(graph, {}, full=False, dry=branch == "dry", limit=0 if branch == "limit" else 12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 117: invalid start byte
----------------------------- Captured stdout call -----------------------------
  [план] Альфа: 5 источников (изменилось)
______________ test_strict_utf8_never_refuses_author_text[empty] _______________
tests/test_dossier_encoding.py:240: in test_strict_utf8_never_refuses_author_text
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Альфа: 5 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Бета: 4 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Гамма: 3 источников, 77 зн., 0с
____________ test_strict_utf8_never_refuses_author_text[no_heading] ____________
tests/test_dossier_encoding.py:240: in test_strict_utf8_never_refuses_author_text
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Альфа: 5 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Бета: 4 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Гамма: 3 источников, 77 зн., 0с
_______ test_strict_utf8_never_refuses_author_text[literal_replacement] ________
tests/test_dossier_encoding.py:240: in test_strict_utf8_never_refuses_author_text
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Альфа: 5 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Бета: 4 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Гамма: 3 источников, 77 зн., 0с
_______________ test_strict_utf8_never_refuses_author_text[crlf] _______________
tests/test_dossier_encoding.py:240: in test_strict_utf8_never_refuses_author_text
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Альфа: 5 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Бета: 4 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Гамма: 3 источников, 77 зн., 0с
___________ test_missing_dossier_is_only_indexed_after_build[limit] ____________
tests/test_dossier_encoding.py:262: in test_missing_dossier_is_only_indexed_after_build
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  пропущено 3: без изменений 0, сверх лимита 3, граф занят 0, не успели за ночь 0
___________ test_missing_dossier_is_only_indexed_after_build[model] ____________
tests/test_dossier_encoding.py:262: in test_missing_dossier_is_only_indexed_after_build
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  … Бета: попытка 1 — ответ не по формату, повтор
  … Бета: попытка 2 — ответ не по формату, повтор
  … Альфа: попытка 1 — ответ не по формату, повтор
  … Альфа: попытка 2 — ответ не по формату, повтор
  … Гамма: попытка 1 — ответ не по формату, повтор
  … Гамма: попытка 2 — ответ не по формату, повтор
___________ test_missing_dossier_is_only_indexed_after_build[locked] ___________
tests/test_dossier_encoding.py:262: in test_missing_dossier_is_only_indexed_after_build
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  ⏸ Бета: граф занят соседом дольше 5 мин — тема уйдёт на следующую ночь
  ⏸ Альфа: граф занят соседом дольше 5 мин — тема уйдёт на следующую ночь
  ⏸ Гамма: граф занят соседом дольше 5 мин — тема уйдёт на следующую ночь
  пропущено 3: без изменений 0, сверх лимита 0, граф занят 3, не успели за ночь 0
____________ test_missing_dossier_is_only_indexed_after_build[late] ____________
tests/test_dossier_encoding.py:262: in test_missing_dossier_is_only_indexed_after_build
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  ⏹ время ночного прогона вышло — остальные темы завтра
  пропущено 3: без изменений 0, сверх лимита 0, граф занят 0, не успели за ночь 3
___________ test_missing_dossier_is_only_indexed_after_build[build] ____________
tests/test_dossier_encoding.py:262: in test_missing_dossier_is_only_indexed_after_build
    _account(result, counts, paths, capsys, damaged=0)
tests/test_dossier_encoding.py:90: in _account
    assert result["битые"] == damaged
           ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
----------------------------- Captured stdout call -----------------------------
  ✓ Бета: 4 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Альфа: 5 источников, 77 зн., 0с
  копия прежнего досье: Досье/.backup/2026-10-06_104932/
  ✓ Гамма: 3 источников, 77 зн., 0с
_________________ test_full_rebuilds_matching_damaged_dossier __________________
tests/test_dossier_encoding.py:274: in test_full_rebuilds_matching_damaged_dossier
    result = nd.run(graph, {}, full=True, dry=False, limit=0)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 121: invalid start byte
----------------------------- Captured stdout call -----------------------------
  копия прежнего досье: Досье/.backup/2026-10-06_104933/
  ✓ Альфа: 5 источников, 77 зн., 0с
_____________ test_main_reports_damage_without_model_failure_exit ______________
tests/test_dossier_encoding.py:287: in test_main_reports_damage_without_model_failure_exit
    assert nd.main() == 0, "три повреждения не означают, что модель молчит"
           ^^^^^^^^^
scripts/nightly_dossier.py:336: in main
    r = run(g, c, full=args.full, dry=args.dry, limit=remaining)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:152: in run
    old_fp = dossier.read_fingerprint(path)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 189: invalid start byte
----------------------------- Captured stdout call -----------------------------
=== Граф
_____________ test_graph_with_nodes_but_no_themes_has_damage_count _____________
tests/test_dossier_encoding.py:299: in test_graph_with_nodes_but_no_themes_has_damage_count
    assert result["тем"] == 0 and result["битые"] == 0
                                  ^^^^^^^^^^^^^^^
E   KeyError: 'битые'
_______________ test_load_dossier_contract[missing-missing-None] _______________
tests/test_dossier_encoding.py:315: in test_load_dossier_contract
    loaded = dossier.load_dossier(path)
             ^^^^^^^^^^^^^^^^^^^^
E   AttributeError: module 'charoite_graph.dossier' has no attribute 'load_dossier'
____________________ test_load_dossier_contract[empty-ok-] _____________________
tests/test_dossier_encoding.py:315: in test_load_dossier_contract
    loaded = dossier.load_dossier(path)
             ^^^^^^^^^^^^^^^^^^^^
E   AttributeError: module 'charoite_graph.dossier' has no attribute 'load_dossier'
___________ test_load_dossier_contract[valid_replacement-ok-\ufffd] ____________
tests/test_dossier_encoding.py:315: in test_load_dossier_contract
    loaded = dossier.load_dossier(path)
             ^^^^^^^^^^^^^^^^^^^^
E   AttributeError: module 'charoite_graph.dossier' has no attribute 'load_dossier'
_ test_load_dossier_contract[tail-damaged-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\ufffd] _
tests/test_dossier_encoding.py:315: in test_load_dossier_contract
    loaded = dossier.load_dossier(path)
             ^^^^^^^^^^^^^^^^^^^^
E   AttributeError: module 'charoite_graph.dossier' has no attribute 'load_dossier'
____________ test_load_dossier_contract[unreadable-unreadable-None] ____________
tests/test_dossier_encoding.py:315: in test_load_dossier_contract
    loaded = dossier.load_dossier(path)
             ^^^^^^^^^^^^^^^^^^^^
E   AttributeError: module 'charoite_graph.dossier' has no attribute 'load_dossier'
_________________ test_path_metadata_readers_use_load_dossier __________________
tests/test_dossier_encoding.py:323: in test_path_metadata_readers_use_load_dossier
    assert dossier.read_fingerprint(path) == "old"
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
src/charoite_graph/dossier.py:489: in read_fingerprint
    head = path.read_text(encoding="utf-8")[:600]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 749: invalid start byte
___________ test_closed_window_falls_back_to_each_theme_fingerprint ____________
tests/test_dossier_encoding.py:337: in test_closed_window_falls_back_to_each_theme_fingerprint
    result = nd.run(graph, {}, full=False, dry=False, limit=12)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
scripts/nightly_dossier.py:193: in run
    entries.append(_index_entry_from_disk(
scripts/nightly_dossier.py:76: in _index_entry_from_disk
    "собрано": _собрано(path) or today,
               ^^^^^^^^^^^^^^
scripts/nightly_dossier.py:291: in _собрано
    m = re.search(r"^собрано:\s*(\S+)", path.read_text(encoding="utf-8")[:400], re.M)
                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/pathlib.py:1028: in read_text
    return f.read()
           ^^^^^^^^
<frozen codecs>:322: in decode
    ???
E   UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 5: invalid start byte
----------------------------- Captured stdout call -----------------------------
  ⏹ время ночного прогона вышло — остальные темы завтра
=========================== short test summary info ============================
FAILED tests/test_dossier_encoding.py::test_damaged_body_or_fingerprint_rebuilds_every_theme[body]
FAILED tests/test_dossier_encoding.py::test_damaged_body_or_fingerprint_rebuilds_every_theme[fingerprint]
FAILED tests/test_dossier_encoding.py::test_damaged_author_text_refuses_before_model[manual]
FAILED tests/test_dossier_encoding.py::test_damaged_author_text_refuses_before_model[heading]
FAILED tests/test_dossier_encoding.py::test_damaged_graph_source_stays_in_cluster
FAILED tests/test_dossier_encoding.py::test_unreadable_refuses_before_model_and_keeps_index
FAILED tests/test_dossier_encoding.py::test_collected_date_is_parsed_and_normalized[\u043c\u0443\u0441\u043e\u0440-None]
FAILED tests/test_dossier_encoding.py::test_collected_date_is_parsed_and_normalized[20261006-2026-10-06]
FAILED tests/test_dossier_encoding.py::test_collected_date_is_parsed_and_normalized[2026-W41-1-2026-10-05]
FAILED tests/test_dossier_encoding.py::test_matching_fingerprint_does_not_force_damaged_rebuild
FAILED tests/test_dossier_encoding.py::test_empty_graph_has_damage_count - Ke...
FAILED tests/test_dossier_encoding.py::test_closed_window_reads_tail_once - U...
FAILED tests/test_dossier_encoding.py::test_every_exit_reuses_loaded_dossier[limit]
FAILED tests/test_dossier_encoding.py::test_every_exit_reuses_loaded_dossier[model]
FAILED tests/test_dossier_encoding.py::test_every_exit_reuses_loaded_dossier[locked]
FAILED tests/test_dossier_encoding.py::test_every_exit_reuses_loaded_dossier[backup]
FAILED tests/test_dossier_encoding.py::test_every_exit_reuses_loaded_dossier[dry]
FAILED tests/test_dossier_encoding.py::test_strict_utf8_never_refuses_author_text[empty]
FAILED tests/test_dossier_encoding.py::test_strict_utf8_never_refuses_author_text[no_heading]
FAILED tests/test_dossier_encoding.py::test_strict_utf8_never_refuses_author_text[literal_replacement]
FAILED tests/test_dossier_encoding.py::test_strict_utf8_never_refuses_author_text[crlf]
FAILED tests/test_dossier_encoding.py::test_missing_dossier_is_only_indexed_after_build[limit]
FAILED tests/test_dossier_encoding.py::test_missing_dossier_is_only_indexed_after_build[model]
FAILED tests/test_dossier_encoding.py::test_missing_dossier_is_only_indexed_after_build[locked]
FAILED tests/test_dossier_encoding.py::test_missing_dossier_is_only_indexed_after_build[late]
FAILED tests/test_dossier_encoding.py::test_missing_dossier_is_only_indexed_after_build[build]
FAILED tests/test_dossier_encoding.py::test_full_rebuilds_matching_damaged_dossier
FAILED tests/test_dossier_encoding.py::test_main_reports_damage_without_model_failure_exit
FAILED tests/test_dossier_encoding.py::test_graph_with_nodes_but_no_themes_has_damage_count
FAILED tests/test_dossier_encoding.py::test_load_dossier_contract[missing-missing-None]
FAILED tests/test_dossier_encoding.py::test_load_dossier_contract[empty-ok-]
FAILED tests/test_dossier_encoding.py::test_load_dossier_contract[valid_replacement-ok-\ufffd]
FAILED tests/test_dossier_encoding.py::test_load_dossier_contract[tail-damaged-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\ufffd]
FAILED tests/test_dossier_encoding.py::test_load_dossier_contract[unreadable-unreadable-None]
FAILED tests/test_dossier_encoding.py::test_path_metadata_readers_use_load_dossier
FAILED tests/test_dossier_encoding.py::test_closed_window_falls_back_to_each_theme_fingerprint
36 failed in 2.47s

```

## Полный зелёный прогон с патчем

```text
ВНИМАНИЕ: реестр №329 не импортировался целиком — свидетель корня проверит не всех:
  audio: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/audio.py", line 15, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

  daemon: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/daemon.py", line 57, in <module>
    import live_nemotron  # noqa: E402
    ^^^^^^^^^^^^^^^^^^^^
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/live_nemotron.py", line 71, in <module>
    import audio
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/audio.py", line 15, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

  dictate: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/dictate.py", line 16, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

  main: OSError: PortAudio library not found
Traceback (most recent call last):
  File "/workspace/scratch/8724b0754be4/Charoite_audio/tests/conftest.py", line 99, in pytest_configure
    importlib.import_module(имя)
  File "/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/importlib/__init__.py", line 90, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 999, in exec_module
  File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/main.py", line 24, in <module>
    from audio import AudioHub, list_devices  # noqa: E402
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/scratch/8724b0754be4/Charoite_audio/src/audio.py", line 15, in <module>
    import sounddevice as sd
  File "/root/.local/lib/python3.12/site-packages/sounddevice.py", line 73, in <module>
    raise OSError('PortAudio library not found')
OSError: PortAudio library not found

........................................................................ [ 66%]
....................................                                     [100%]
108 passed in 1.10s

```

## Ruff

```text
All checks passed!
```
