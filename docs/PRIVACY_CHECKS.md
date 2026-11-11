# Reproducible privacy checks / Воспроизводимая проверка приватности

This is the maintainer's prepublication protocol. It uses the existing
`scripts/check_private_markers.py` and installed hooks; it does not replace
them with a second scanner. The private marker list stays outside Git and
is read only by the canonical local checker used by those hooks. Never put
its contents in shell arguments, model context, CI or a receipt.

Это протокол сопровождающего перед публикацией. Единственный движок —
`scripts/check_private_markers.py` и штатные hooks. Реальный список читает
только этот локальный проверяющий механизм; список не передаётся в аргументы
команд, моделям, CI или в отчёт. Raw `git grep` по склеенному списку маркеров
не является проверкой допуска: он иначе трактует regex, границы и media.

## Contract / Контракт

| Area / Область | Required behavior / Обязательное поведение |
| --- | --- |
| Matching / Сопоставление | NFC for both inputs, literal escaping, case-insensitive. Up to 4 Unicode code points: Python Unicode `\b` on both ends; longer markers: substring. NFC обоих входов, литеральное экранирование, без учёта регистра; до 4 символов включительно — `\b`, длиннее — подстрока. |
| Boundaries / Границы | Letters, digits and `_` are word characters; hyphen is a separator. Punctuation-ended short markers still use literal Python `\b`, not a natural-language word rule. Буквы, цифры и `_` входят в слово; дефис разделяет. Для маркеров с пунктуацией на краях действует именно Python `\b`. |
| Scope / Охват | Whole candidate tree **and** every outgoing commit, including names/messages/identity; a clean final diff is insufficient. Всё дерево кандидата **и** каждый отправляемый коммит, имена/сообщения/авторство; чистого итогового diff недостаточно. |
| Binaries / Двоичные | Preserve existing signature, encoding and exact-blob allow-list rules. Never blanket-skip binaries. Сохранять действующие проверки сигнатур, кодировок и разрешённых версий blob; не исключать всё двоичное. |
| Failures / Отказы | Missing/empty list, Git/checker failure or incomplete scope means BLOCKED, never PASS. Отсутствующий/пустой список, ошибка Git/сканера или неполный охват — BLOCKED. |
| CI | `--public-only` is a separate format check, not proof that private markers are absent. Это проверка публичных форматов, не замена локального допуска. |

## Procedure / Порядок

1. **Freeze the candidate.** Use a clean, reviewed checkout. Record exact
   base/head OIDs, Git/Python versions and SHA-256 of the checker and installed
   hooks. Confirm the checker matches the reviewed version. An uncommitted
   tree, unknown base, changed refs/checker/policy or modified candidate
   invalidates the receipt. Do not record the hash or contents of the private
   list. Any local list change requires a new check.

   **Зафиксировать кандидата.** Чистый проверенный checkout, точные base/head,
   версии Git/Python и SHA-256 scanner/hooks. Сверить scanner с проверенной
   версией. Неизвестная база или изменение дерева/refs/политики/списка требует
   новой проверки. Hash приватного списка не сохранять.

2. **Review content outside text scanning.** Inspect the candidate inventory
   of HEAD and **every outgoing commit on every outgoing ref**, including
   replaced or deleted versions. Enumerate commits with `git rev-list` and
   inspect each tree with `git ls-tree -r --full-tree`; deduplicate by object
   identity, not filename. The template below records one reviewed base/head
   range; reconcile the union of all actual outbound refs before publication.
   Include media, mode `160000` submodules, LFS pointers and external attachments.
   Review each distinct payload version (including the object behind each LFS
   or submodule pointer); a pointer is not its payload. Non-commit refs need
   their own object inventory; unknown coverage is BLOCKED. Record `none`,
   `separately approved` or `BLOCKED` for each relevant
   class. Unexamined payload blocks full privacy clearance. A media skip or
   approved blob hash is not proof about visible text, audio or metadata;
   those require a separate permitted review. Do not open private originals
   to fill this gap: prepare approved synthetic/public replacements instead.

   **Проверить нетекстовый охват.** Инвентаризировать HEAD и каждый уходящий
   коммит всех отправляемых refs, включая заменённые и удалённые версии.
   Перечень коммитов даёт `git rev-list`, дерево каждого — `git ls-tree -r --full-tree`.
   Объединять по идентичности объекта, не имени файла. Шаблон ниже покрывает
   одну пару base/head; перед публикацией сверить объединённый охват всех refs.
   Проверить каждую версию media, submodules (`160000`), LFS и внешних вложений,
   включая содержимое за указателями. Не-коммитные refs требуют отдельной
   инвентаризации объектов; неизвестный охват — BLOCKED. Указатель не заменяет содержимое.
   Для каждого класса: отсутствует, отдельно проверен или BLOCKED. Непроверенные
   изображения/звук/метаданные не получают полный допуск от текстового сканера.
   Реальные приватные оригиналы ради этого не читать; использовать разрешённые
   публичные или синтетические замены. Это отдельное действие сопровождающего,
   а не возможность, автоматически реализованная сканером.

3. **Run the canonical checks.** The following is a template for a dedicated
   shell, from the clean candidate root; substitute the reviewed base OID.
   Output goes to a private temporary directory, never into an agent/public
   log. Any failed step exits before a success message. It intentionally does
   not publish anything. The receipt directory is created before Git runs;
   all validation diagnostics stay inside it, and creation/redirection errors
   are kept off the console. Only the neutral success message is public.
   Never append an unconditional push or suppress failure.

   **Выполнить штатные проверки.** Шаблон ниже — отдельный shell в чистом
   checkout; подставить согласованный base OID. Вывод остаётся в приватном
   временном каталоге, не в агентском/публичном логе. Ошибка завершает shell.
   Каталог отчёта создаётся до Git; вся диагностика проверки приватна, ошибки
   создания каталога/перенаправления не выводят пути в консоль. Наружу выходит
   только нейтральное сообщение успеха. Публикации здесь нет; безусловный push
   после команды запрещён.

   ```sh
   set -eu
   umask 077
   receipt_dir=$(mktemp -d "${TMPDIR:-/tmp}/charoite-privacy.XXXXXX" 2>/dev/null)
   (
     set -eu
     base=REPLACE_WITH_REVIEWED_BASE_OID
     head=$(git rev-parse --verify 'HEAD^{commit}')
     git rev-parse --verify "${base}^{commit}" >/dev/null
     candidate_status=$(git status --porcelain)
     test -z "$candidate_status"
     git ls-tree -r --full-tree "$head" >"$receipt_dir/head-tree.log"
     outgoing_commits=$(git rev-list "$base..$head")
     for outgoing_commit in $outgoing_commits; do
       printf 'commit %s\n' "$outgoing_commit"
       git ls-tree -r --full-tree "$outgoing_commit"
     done >"$receipt_dir/outgoing-trees.log"
     python3 scripts/check_private_markers.py --all >"$receipt_dir/tree.log" 2>&1
     python3 scripts/check_private_markers.py --range "$base..$head" >"$receipt_dir/range.log" 2>&1
     current_head=$(git rev-parse HEAD)
     test "$current_head" = "$head"
     candidate_status=$(git status --porcelain)
     test -z "$candidate_status"
   ) 2>/dev/null >"$receipt_dir/validation.log" 2>&1
   printf '%s\n' 'TEXT_CHECKS_PASS; payload review and final pre-push still required'
   ```

4. **Publication is a separate authorized action.** Keep normal pre-commit
   and pre-push hooks enabled. The installed owner pre-push uses the canonical
   checker on `main`, processes every actual outbound ref and checks identity.
   `--range` does not replace that identity check; `--range-from-env` alone does
   not establish owner-list or multi-ref coverage. Record each actual
   old/new OID, including new branches, force updates and tags. Deletions have
   no new payload, but still need their own authorization. If remote tips are
   unavailable, or the coverage cannot be explained, stop and refresh the
   relevant refs; unknown remote objects are not evidence of a clean range.
   The checker may conservatively scan extra history when tips are missing;
   that does not waive the protocol's scope reconciliation. Record final hook
   status only after the actual authorized action. A dry run is not publication.

   **Публикация — отдельное разрешённое действие.** Оба hook обязательны.
   Финальный pre-push использует канон `main`, проверяет все фактические refs
   и авторство. `--range` не заменяет его; `--range-from-env` не доказывает
   owner/multi-ref охват. Сохранить old/new OID всех refs, включая новые ветки,
   force и tags. Удаление не несёт нового payload, но требует своего разрешения.
   Если база/remote tips или охват не подтверждены — остановиться и сверить refs.
   Более широкий консервативный поиск scanner не заменяет эту сверку.
   Dry-run не называть выполненной публикацией.

## Receipt / Отчёт

Keep a local receipt with UTC time, exact base/head, tool/checker/hook versions,
ref OIDs, counts of files/commits and skipped/unreadable objects, each exit code,
payload-review disposition and synthetic controls result. Use `PASS`, `FAIL`,
`BLOCKED`, `NOT RUN` or `NOT APPLICABLE` with a reason. Overall PASS requires
every applicable step; a zero exit code does not cover an omitted step.
Keep raw diagnostics private. Share only reviewed aggregate counts and neutral
reason codes, never marker values, matching lines, private paths or ref names.

Локальный отчёт: UTC, base/head, версии инструментов/scanner/hooks, OID refs,
числа файлов/коммитов и пропущенных/непрочитанных объектов, коды возврата,
результат отдельной проверки payload и синтетики. Статусы PASS/FAIL/BLOCKED/
NOT RUN/NOT APPLICABLE с причиной. Пропущенная обязательная проверка запрещает
общий PASS. Сырой вывод приватен; наружу — только проверенные агрегаты и коды,
без маркеров, совпавших строк, приватных путей или имён refs.

## Reproduction / Повтор на синтетике

After the applicable recording/resource gate, run these two standalone suites
with the project's Python environment. `--noconftest` isolates the privacy
suites from application-wide model/data fixtures. The deselected test reads
the real owner list directly and is deliberately **not** part of synthetic
qualification; it is not claimed as passed. Local release hooks remain mandatory.

После recording/resource gate запустить два автономных набора в окружении
проекта. `--noconftest` исключает общие model/data fixtures. Исключённый тест
читает реальный список напрямую: это не синтетика и не заявленный PASS;
штатные локальные release hooks всё равно обязательны.

```sh
python -m pytest --noconftest -o addopts= \
  tests/test_private_markers.py tests/test_pre_push_markers.py \
  -k 'not test_the_tree_of_this_repository_is_clean' -q
```

| Controls / Контроли | Coverage / Покрытие |
| --- | --- |
| Short ASCII/Cyrillic, case, digits/underscore/hyphen/Unicode, literal regex, long substring, NFC/NFD both inputs | `test_protocol_*` in `test_private_markers.py`, through actual loading and scanning / через загрузку и scanner |
| Same synthetic bytes, legacy false alarm vs canonical result, standalone positive control | `test_protocol_raw_grep_and_canonical_scan_same_synthetic_snapshot` |
| Removed/replaced media versions remain in the review inventory | `test_protocol_inventory_keeps_replaced_and_deleted_media` |
| Shell Git/checker failure cannot reach the publication sink or expose diagnostics | `test_protocol_documented_shell_stops_before_publication` (executes the actual template / исполняет сам шаблон) |
| Missing/empty list; denial leaves remote refs unchanged | `test_protocol_failed_gate_keeps_synthetic_remote_unchanged` |
| Intermediate leak then cleanup, Git failure, new/multiple refs, force, tags, deletion, unavailable/stale tips | Existing temporary-repository `test_pre_push_markers.py` cases / существующие сценарии с временными Git-репозиториями |
| Media signature/spoof, non-UTF8, unknown binary and exact-blob approval | Existing `test_private_markers.py` / `test_pre_push_markers.py` cases |
| LFS/submodule/external payload and visual/audio privacy | Separate inventory/review in step 2; scanner does not certify them / отдельная проверка шага 2, не гарантия scanner |

The temporary Git tests use only synthetic markers and local bare remotes.
They do not publish to an external service. Compare the same fixtures and
record exact source versions; changing inputs invalidates a before/after claim.

Git-тесты используют синтетические маркеры и локальные bare-remotes без внешней
публикации. Для «до/после» сохранять одинаковые входы и точные версии исходников.
