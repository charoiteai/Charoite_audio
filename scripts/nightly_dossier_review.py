#!/usr/bin/env python3
"""Облачная ревизия досье: Opus правит то, что локальная модель собрала ночью.

Разделение труда. Локальная модель собирает досье массово и дёшево — по всем
темам графа. Она хорошо пересказывает, но плохо видит связи между источниками:
что одно решение отменяет другое, что срок протух, что два узла говорят разное.
Opus это видит. Поэтому он идёт вторым проходом и правит уже готовые сводки.

    .venv/bin/python scripts/nightly_dossier_review.py            # текущий граф
    .venv/bin/python scripts/nightly_dossier_review.py --all-graphs
    .venv/bin/python scripts/nightly_dossier_review.py --dry      # показать, не писать
    .venv/bin/python scripts/nightly_dossier_review.py --limit 5

Правит только при включённом `sufler.cloud_edit_graph`. Выключен — пишет
рекомендации в отчёт и ничего не трогает. Общий рубильник облака старше
(спрашивается через `src/privacy.py`): выключен — шаг молчит совсем.

Неприкосновенно в любом режиме: стенограммы, минутки и раздел «Правки автора».
Перед каждой правкой — бэкап в `Досье/.backup/<дата>/`.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import pathlib
import re
import subprocess
import sys
import time

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py. Вставка —
# только чтобы импортировать сам канон.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import charoite_paths  # noqa: E402
import cloud  # noqa: E402
import cloud_door  # noqa: E402
from charoite_graph import dossier, graph_names  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402
import file_locks  # noqa: E402
import graphs  # noqa: E402
import live_gate  # noqa: E402
import pii_mask  # noqa: E402
import privacy  # noqa: E402
from charoite_graph import safe_write  # noqa: E402
from charoite_paths import resolve_root  # noqa: E402
from exit_codes import EXIT_PRIVACY_REFUSED, EXIT_STAGE_PENDING  # noqa: E402
from config_loader import load_user_or_example  # noqa: E402


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте."""
    return resolve_root(__file__)

FRESH_DAYS = 3          # смотрим досье, собранные за последние сутки-трое
MAX_SRC_CHARS = 45_000  # потолок на один запрос к Opus
BACKUP_KEEP = 40      # каталог .backup/ общий с пересборкой: два штампа за ночь (GLM M3 по #561)
KEEP_REPORTS = 14       # служебных отчётов в корне графа
DEFAULT_LIMIT = 6       # досье за прогон: облако не бесплатное по времени
SECTIONS = ("## Сейчас", "## Как пришли", "## Решено", "## Открыто", "## Кто в теме")
# Ревизия короче 60% прежнего тела — не правка, а потеря: модель ответила
# обрывком, «подытожила» или выбросила разделы. Вверх не ограничиваем —
# настоящая ревизия растёт (замер 22.08: 4,2 → 11,4 КБ, 25 пометок ⚠️).
MIN_KEEP = 0.6


# Раскладка досье (защищённые разделы, тело) — в `dossier.py`: её читают и
# ночная сборка, и доктор графа (n815-in2 DS C2, Sonnet I5).
PROTECTED_HEADINGS = dossier.PROTECTED_HEADINGS
_HEADING_RE = re.compile(r"(?m)^\s*#.*$")
strip_protected = dossier.strip_protected
split_dossier = dossier.split_dossier


# Сколько ждать чужой разбор встречи, прежде чем работать без правок:
# ночь под потолком, дольше стоять смысла нет.
LOCK_WAIT = 10 * 60
# Исходы ревизии, которые не отказ по содержанию (круг n841-opus-fix): тело то же —
# поле не ставим и в «Отклонено» не пишем (DS I3); тело то же, кроме меток
# диаризации, — чистку пишем, поле не ставим (Sonnet I2, DS M7)
NO_EDITS = "правок нет — поле `ревизия:` не ставим"
LABELS_ONLY = "сняты только метки диаризации — поле `ревизия:` не ставим"


def _link_key(target: str) -> str:
    """Ссылка как имя файла на диске: последний сегмент (как dossier.scan),
    плюс мягче — без .md и без регистра: на APFS `[[Иванов.md]]` и
    `[[иванов]]` — тот же файл, а модель, переформулируя пункт, пишет
    ссылку как видит (круг-1 по PR #382, DeepSeek)."""
    return target.strip().split("/")[-1].removesuffix(".md").strip().lower()


def links(body: str) -> set[str]:
    """Пустые `[[ ]]` и `[[.md]]` — не ссылки: scan их тоже не считает."""
    return {k for x in dossier.LINK_RE.findall(body or "") if (k := _link_key(x))}


def check_revision(old_body: str, new_body: str) -> str | None:
    """Почему переписанное досье нельзя класть на место старого — или None.

    До этого на запись пускал `looks_valid`: «## Сейчас» где-то есть и четыре
    заголовка из пяти, — то есть проходили ответ без «## Кто в теме», ответ с
    лишним разделом и ответ, растерявший половину ссылок. Источники досье —
    это [[ссылки]] в конце пунктов; пропавшая ссылка означает выброшенный
    факт, а промпт просит ничего не выбрасывать без причины (карточка №87).
    """
    # Любая строка, начинающаяся с «#», — заголовок: «# Важное», «###
    # Правки автора» и «##Сейчас» считаются наравне с «## …».
    heads = [re.sub(r"\s+", " ", h.strip()) for h in _HEADING_RE.findall(new_body)]
    want = [h.strip() for h in SECTIONS]
    if heads != want:
        return f"разделы не по формату: {', '.join(heads) or 'нет заголовков'}"
    if len(new_body) < MIN_KEEP * len(old_body):
        return f"ответ короче {int(MIN_KEEP * 100)}% прежнего ({len(new_body)} из {len(old_body)} зн.)"
    lost = sorted(links(old_body) - links(new_body))
    if lost:
        shown = ", ".join(f"[[{x}]]" for x in lost[:5]) + (" …" if len(lost) > 5 else "")
        return (f"потеряны ссылки на источники ({len(lost)}): {shown} — "
                "ревизия целиком не применена")
    if _same_text(new_body, old_body):
        # то же тело — поле `ревизия:` заморозило бы тему без единой правки (финальный n841 Opus I2)
        return NO_EDITS
    return None


def _same_text(a: str, b: str) -> bool:
    """Тело то же по словам и строкам: пробелы внутри строки, хвостовые и пустые
    строки — не правка (круг n841-opus-fix: Sonnet I1, DS M5). Строки не склеиваем:
    «- a» и «- b» в одну строку — другая разметка, а не то же тело."""
    norm = lambda s: [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines() if ln.strip()]
    return norm(a) == norm(b)


def revision_stats(old_body: str, new_body: str) -> str:
    """Одна строка про то, что изменилось: для утреннего отчёта владельцу."""
    before = collections.Counter(ln.strip() for ln in old_body.splitlines() if ln.strip())
    after = collections.Counter(ln.strip() for ln in new_body.splitlines() if ln.strip())
    removed = sum((before - after).values())
    added = sum((after - before).values())
    return (f"+{added}/−{removed} строк, ⚠️ {new_body.count('⚠️')}, "
            f"ссылок {len(links(old_body))}→{len(links(new_body))}")


def _cfg() -> dict:
    return load_user_or_example(_root()) or {}



def _backup(folder: pathlib.Path, stamp: str, path: pathlib.Path) -> None:
    """Автомат без бэкапа — не автомат, а рулетка (то же правило, что в tier3).
    Реализация общая с пересборкой — dossier.backup (аудит 13.09, GLM C1)."""
    dossier.backup(folder, stamp, path, keep=BACKUP_KEEP)


PROMPT = """Ниже досье по теме «{theme}» и его источники из графа рабочих встреч.

ТЕКУЩЕЕ ДОСЬЕ:
{current}

ИСТОЧНИКИ:
{sources}

────────────────────────────────────────
ЗАДАНИЕ

Ты ночной ревизор. Досье собрала локальная модель — она пересказывает исправно,
но не замечает связей. Твоя работа — то, что видно только на всей картине:

- решение, отменённое более поздним;
- факт, противоречащий другому источнику;
- срок, который уже прошёл, а пункт числится открытым;
- поручение без исполнителя или потерянный хвост;
- ошибка в цифре, имени или названии системы против источника;
- пропущенное в сводке, но важное для темы.

Верни ИСПРАВЛЕННОЕ досье целиком, в том же формате и с теми же пятью разделами:
## Сейчас / ## Как пришли / ## Решено / ## Открыто / ## Кто в теме

Правила:
- Только факты из источников. Не додумывать.
- Ссылки [[Имя файла]] в конце пунктов сохранять ВСЕ и добавлять к новым.
- Спорное помечай «⚠️» с указанием, что с чем расходится.
- Пункты не удалять: отменённое, просроченное или опровергнутое помечай
  «⚠️» с пояснением и оставляй вместе со ссылкой. Досье, потерявшее хотя бы
  одну ссылку, будет отклонено целиком.
- {unlabeled_rule}
- Русский язык, короткие фразы, без обращений к читателю.

Ответ начни СРАЗУ со строки «## Сейчас». Никаких предисловий.
"""


def review(theme: str, path: pathlib.Path, graph: pathlib.Path,
           files: dict, members: list[str], model: str, cfg: dict,
           current: str | None = None) -> tuple[str | None, str]:
    """(исправленное тело, '') — или (None, почему отклонено).

    Причины, начинающиеся с «сбой:», — не отказ по содержанию, а сбой шага
    (сеть, лимит, код возврата): в отчёте они идут отдельным разделом.
    `current` — текст досье, уже прочитанный вызывающим: он же пойдёт в
    сборку файла, чтобы между проверкой и записью не было второго чтения.
    """
    # Сетевой выход держит собственную границу. main уже проверяет её ради
    # дешёвого раннего выхода всего прогона, но review можно вызвать отдельно
    # из теста, будущего воркера или после рефакторинга call graph.
    if not privacy.cloud_enrich_enabled(cfg):
        return None, "сбой: облако выключено"
    # И провайдер — тоже здесь, а не только в main: при tinfoil досье ждут
    # этапа 2 (№792), и текст досье не должен уйти в Anthropic вопреки выбору
    # владельца, кто бы ни позвал review (in3 DS-I7, S-I6).
    try:
        provider = privacy.night_provider(cfg)
    except privacy.PrivacyRefused as e:
        return None, f"сбой: провайдер ночи не принят: {e}"
    if provider != "anthropic":
        return None, f"сбой: провайдер ночи {provider} — досье этим путём не отправляются"
    current = path.read_text(encoding="utf-8") if current is None else current
    head, old_body, sources, _manual = split_dossier(current)
    if sources is None:
        # собрано руками или старым форматом — облаку не сверять и не трогать
        return None, "в досье нет раздела «## Источники» — не трогаем"
    # раздел «Правки автора» в запрос не отдаём и не даём его переписать
    # метки деятелем — тем же словом, что в источниках, иначе модель вернёт их в ответ (№815)
    labeled_body = old_body
    old_body = graph_names.unlabel_speakers(old_body)
    body = f"{old_body}\n\n## Источники\n{graph_names.unlabel_speakers(sources)}\n"

    parts, total = [], 0
    for m in members[: dossier.MAX_SOURCES]:
        meta = files.get(m)
        if not meta:
            continue
        block = f"### [[{m}]] ({meta['kind']})\n{dossier.source_text(meta)}\n"
        if total + len(block) > MAX_SRC_CHARS:
            break
        parts.append(block)
        total += len(block)

    # данные шаблона — эти три строки и только они: те же объекты идут в маску (n793-out18 DS-C1)
    src_text = "\n".join(parts)
    prompt = PROMPT.format(theme=theme, current=body, sources=src_text,
                           unlabeled_rule=graph_names.UNLABELED_RULE)
    # Маска ПДн (№793): в облако — только masked.text, досье пишется с именами.
    try:
        masked = cloud_door.mask_prompt(cfg, prompt, graph, data=(theme, body, src_text))
        print(f"  {theme}: {masked.summary()}")   # запасной режим маски виден в логе (n793-out18 DS-I1)
        # cwd CLI кладёт в системный промпт (n793-final-opus M1); здесь — сбой каталога не
        # диагноз «CLI не запустился» (n793-out10 DS-M5) и не traceback ночи (n793-out11 S-I1)
        workdir = None if masked.level == "off" else cloud_door.empty_workdir()
    except pii_mask.MaskRefused as e:
        return None, f"сбой: маска не прошла: {e}"
    claude = cloud.claude_bin()
    env = cloud.cli_env()      # то же окружение, что у зонда: иначе зонд не представителен (DS r2 по #499)
    r = None
    for attempt in (1, 2):
        try:
            # Досье и все источники уже В ПРОМПТЕ — инструментов этому вызову не
            # положено вовсе: инъекция из стенограммы/досье не должна читать
            # произвольные файлы и вносить их в переписанный текст (аудит 14.08).
            # stdin=DEVNULL — по правилу соседних вызовов: унаследованный поток
            # заставляет headless-claude ждать EOF до таймаута. Вызов остаётся
            # ЗДЕСЬ, под privacy-гейтом выше: сторож test_cloud_call_sites
            # держит выход в сеть именно в review().
            r = subprocess.run([claude, "-p", masked.text, "--model", model,
                                *cloud.effort_args(cloud.effort(cfg, "cloud_night_effort")),   # №214
                                *cloud.text_only_args()],
                               capture_output=True, text=True, timeout=600, env=env,
                               stdin=subprocess.DEVNULL,
                               cwd=workdir)
            break
        except OSError as e:
            # exec не удался: бинарник CLI меняется под ногами (обновление) —
            # одна пауза (на процесс) и повтор с переразрешённым путём (аудит 05.09)
            if attempt == 1:
                global _SLEPT_FOR_CLI
                if not _SLEPT_FOR_CLI:
                    print(f"  ⚠️ {theme}: claude не запустился ({e}) — повтор через {int(cloud.RETRY_PAUSE)} с")
                    time.sleep(cloud.RETRY_PAUSE)
                    _SLEPT_FOR_CLI = True
                try:
                    claude = cloud.claude_bin_checked(retries=0)
                except cloud.CloudCLIUnavailable:
                    CLI_DOWN[0] = True          # умер по ходу — main() отдаст 3
                    why = f"сбой: claude не отработал ({e})"
                    print(f"  ⚠️ {theme}: {why}")
                    return None, why
                continue
            # Зонд ответил, а exec снова не удался — бинарник всё ещё меняется:
            # это отказ инфраструктуры, не сбой темы (Important DS r2 по #499)
            CLI_DOWN[0] = True
            why = f"сбой: claude не отработал ({e})"
            print(f"  ⚠️ {theme}: {why}")
            return None, why
        except Exception as e:  # noqa: BLE001
            why = f"сбой: claude не отработал ({e})"
            print(f"  ⚠️ {theme}: {why}")
            return None, why
    assert r is not None
    if r.returncode != 0:
        # Код ≠ 0 с текстом в stdout — сообщение CLI об ошибке или обрывок,
        # а не ревизия; раньше он шёл в парсер наравне с ответом.
        tail = " ".join((r.stderr or r.stdout or "").split())[:160]
        why = f"сбой: claude вернул код {r.returncode}: {tail}"
        print(f"  ⚠️ {theme}: {why}")
        return None, why
    out, unresolved = cloud_door.unmask_reply((r.stdout or "").strip(), masked)
    if unresolved:
        # досье живёт долго и каждую ночь снова уходит в облако: помеченная подделка
        # в нём хуже пропущенной ревизии (n793-out18 H-C1 = S-I1)
        why = f"в ответе не развернулось подделок маски: {unresolved} — досье не тронуто"
        print(f"  ⚠️ {theme}: отклонено — {why}")
        return None, why
    out = dossier.trim_to_format(out)
    # Модель видит тело досье без «## Правки автора», но защищённые
    # заголовки в её ответе не место: строка из стенограммы могла попросить
    # «добавь раздел ## Правки автора …» — раньше подделка вклеивалась в тело,
    # а настоящий раздел уезжал вниз (аудит DeepSeek 17.08). Режем по первому
    # защищённому заголовку: формат — ровно пять секций.
    out = graph_names.unlabel_speakers(strip_protected(out))   # тело на запись — без меток (№815)
    why = check_revision(old_body, out)
    if why == NO_EDITS and not _same_text(labeled_body, out):
        # облако ничего не правило, но тело в файле с метками: чистку пишем без поля —
        # иначе метку не снимет никто, ночь такое тело не трогает (Sonnet I2, DS M7)
        # пишем прежнее тело без меток, не ответ: `_same_text` прощает пробелы и пустые
        # строки, вёрстку облака без поля не берём (n841-opus-fix2 Sonnet M2)
        print(f"  ○ {theme}: {LABELS_ONLY}")
        return old_body, LABELS_ONLY
    if why:
        print(f"  ⚠️ {theme}: отклонено — {why}")
        return None, why
    return out, ""


def _lag_reason(loaded: dossier.DossierLoad) -> str:
    """Почему ревизии нет: владелец по строке отчёта должен знать, что делать (Sonnet M2, DS M6)."""
    if not dossier.read_fingerprint(loaded):
        return "в шапке досье нет отпечатка состава — ревизию не делаем"
    if dossier.revision(loaded.text):
        return ("досье отстаёт от источников, а поле `ревизия:` держит тело — ночь его не "
                "пересоберёт: убрать строку `ревизия:`, тема соберётся заново")
    return "досье отстаёт от источников — ревизия после пересборки"


def _mtime(p: pathlib.Path) -> float:
    """mtime или 0 — файл, исчезнувший между glob и stat, не роняет прогон (GLM M4)."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def run(graph: pathlib.Path, cfg: dict, dry: bool, limit: int) -> int:
    folder = graph / CHAROITE.dossier_dir
    if not folder.is_dir():
        return 0

    # Право на запись спрашиваем у privacy.py: bool() принимал строку «false»
    # за разрешение, а этот ключ разрешает переписывать файлы графа.
    may_edit = privacy.cloud_edit_graph_enabled(cfg)
    model = cloud.model(cfg, "cloud_model")
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    cl = dossier.clusters(files, backlinks, schema=CHAROITE)

    cutoff = dt.datetime.now() - dt.timedelta(days=FRESH_DAYS)
    # Берём только досье, у которых есть кластер-источник: написанные руками
    # сводки облаку сверять не с чем, и трогать их оно не должно.
    fresh = [p for p in sorted(folder.glob("*.md"))
             if not p.name.startswith("_")
             and dt.datetime.fromtimestamp(_mtime(p)) > cutoff
             and cl.get(p.stem)]
    if not fresh:
        print("  свежих автособранных досье нет — пропуск")
        return 0

    # Штамп с секундами: два прогона за день (ручной поверх ночного) иначе
    # клали бэкап в один каталог, и копия до первого прогона терялась.
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # Замок графа общий с разбором встречи (cloud_review): без него правки
    # этой ревизии попадали в чужую сверку и уезжали в карантин (аудит облака
    # 26.08, GLM I3). С 13.09 замок берётся НА ОДНУ ЗАПИСЬ внутри цикла, как у
    # пересборки: прогон держал его под ожиданием живой встречи и облачными
    # вызовами до часа, и разбор только что закончившейся встречи не дожидался
    # замка — уходил «на чтение» (аудит 13.09, GLM I2). secure_dir — как у
    # соседа: каталог данных может ещё не существовать (ENOENT → замок молча
    # «не берётся»).
    lock_dir = None
    why_readonly = "Запись выключена: `sufler.cloud_edit_graph: false`. Включите тумблер, если хотите, чтобы облако правило граф само."
    if may_edit:
        try:
            lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=_root()))
        except OSError as e:
            print(f"  замок графа не взять ({e}) — правки не пишу, только отчёт")
            may_edit = False
            why_readonly = f"Запись не состоялась: замок графа не взять ({e}); тумблер cloud_edit_graph включён."
    return _review_loop(graph, folder, cl, files, fresh, stamp, model, cfg,
                        dry=dry, limit=limit, may_edit=may_edit, lock_dir=lock_dir,
                        why_readonly=why_readonly)


# Сбои шага (не отказ по содержанию) за прогон: main() отдаёт по ним код 2 —
# раньше они жили только в служебном md внутри графа, и ночь с лежащим
# облаком выглядела успешной (аудит GLM/DS по main 05.09)
FAILED_STEPS: list[str] = []
# Пауза перед повтором exec — один раз на процесс: обновление CLI посреди
# прогона иначе усыпляло бы каждую тему на 60 с, и ночь вылезала за потолок
# (GLM r1 по #499)
_SLEPT_FOR_CLI = False
_REPROBED = [False]


def cli_back() -> bool:
    """Один повторный зонд на процесс (прогон всех графов) после аварии CLI.

    Обновление CLI — штатное событие, и один не ответивший зонд посреди ночи
    (30 с таймаута `--version` во время самозамены бинарника) не должен
    списывать все оставшиеся досье до утра; второй раз уже не верим — иначе
    прогон зондировал бы каждую тему (критика GLM r2 по #499).
    """
    if _REPROBED[0]:
        return False
    _REPROBED[0] = True
    try:
        cloud.claude_bin_checked(retries=0)
    except cloud.CloudCLIUnavailable:
        return False
    CLI_DOWN[0] = False
    print("  ↻ CLI облака снова отвечает — продолжаем")
    return True
# CLI умер посреди прогона (обновление началось после зонда): повторный зонд
# не ответил — остальные темы не трогаем, main() отдаёт 3, а не 2 (DS r1)
CLI_DOWN = [False]


def _proposed(theme: str, fixed: str) -> str:
    """Правка в отчёт: заголовки досье на уровень ниже, чтобы «## Сейчас» не
    спорил с разделами самого отчёта."""
    body = fixed.replace(chr(10) + "## ", chr(10) + "#### ").replace("## ", "#### ", 1)
    return f"### {theme}\n{body}\n"


def _review_loop(graph, folder, cl, files, fresh, stamp, model, cfg, *,
                 dry: bool, limit: int, may_edit: bool,
                 lock_dir: pathlib.Path | None = None,
                 why_readonly: str = "Запись выключена: `sufler.cloud_edit_graph: false`.") -> int:
    if may_edit and not dry and lock_dir is None:
        # запись без замка графа — тот самый путь в карантин (аудит 26.08); ошибка вызова, не режим
        raise ValueError("_review_loop: запись включена, а каталог замка графа не передан")
    done, notes, applied, rejected, failed, unchanged = 0, [], [], [], [], []
    # оплаченный ответ облака, не записанный из-за гонки с владельцем, — в отчёт со своей
    # причиной: у `notes` причина одна на раздел, тумблер или замок (n853-out2 Haiku I-1)
    raced = []
    for path in fresh[:limit]:
        theme = path.stem
        members = cl[theme]
        if dry:
            print(f"  [план] {theme}: {len(members)} источников")
            done += 1
            continue

        # Утренняя встреча посреди хвоста ночи: пока суфлёр слушает, машина
        # его — ревизия подождёт. Гейт стоял только в сборке досье, а висел
        # 21.08 именно этот шаг: прогон, начатый в 04:16, к 11:36 всё ещё
        # держал процессор, и живая запись рвалась (потолок — чтобы ночь не
        # стала днём). Окно — одна дверь у владельца гейта, потолок внутри.
        if not live_gate.night_window_open(_root(), what="ревизия досье"):
            print("  ⏹ время ночного прогона вышло — остальные досье завтра")
            break
        if CLI_DOWN[0] and not cli_back():
            print("  ⏹ CLI облака не запускается — остальные досье завтра")
            break
        try:
            old = path.read_text(encoding="utf-8")     # одно чтение на проверку и запись
        except (OSError, UnicodeDecodeError) as e:
            # файл исчез между glob и чтением (владелец удалил, iCloud выгрузил)
            # или не в UTF-8 — не ронять ночь целиком (аудит 13.09, GLM M4)
            failed.append(f"- **{theme}** — сбой: досье не прочитано ({e})")
            continue
        # Пригодность досье к записи зависит только от `old` — до платного вызова (out5 Sonnet I2);
        # без записи поле не нужно, предложение в отчёт идёт как раньше (out6 DS I1, Haiku I1).
        # «Источники» `review` проверяет сам, до облака
        # Состав — по диску сейчас, а не по скану начала прогона: «забыть встречу» посреди
        # прогона иначе не видно, и текст забытой встречи из снимка ушёл бы в облако
        # (круг n841-opus-fix: Sonnet C1, DS C1). Гейт и без записи: отставшее тело само
        # может нести пересказ забытого (Sonnet M1 отклонён)
        loaded_old = dossier.DossierLoad("ok", old)
        if not dossier.in_sync(loaded_old, dossier.fingerprint_on_disk(members, files)):
            # тело отстаёт от состава (ночь не дошла, «забыть встречу» прошло днём) —
            # поле заморозило бы пересказ ушедшего источника (финальный Opus C1)
            rejected.append(f"- **{theme}** — {_lag_reason(loaded_old)}")
            continue
        head0 = split_dossier(old)[0]
        if may_edit and dossier.revision(dossier.with_revision(head0, stamp[:10])) != stamp[:10]:
            # незакрытая шапка поля не примет; правка без поля — пересказ следующей ночью (out4 DS I1);
            # по одной шапке: черта `---` в теле не сойдёт за её конец (out5 Haiku I2)
            failed.append(f"- **{theme}** — сбой: поле `ревизия:` не ставится (шапка не закрыта) — "
                          "правка не применена")
            continue
        fixed, why = review(theme, path, graph, files, members, model, cfg, current=old)
        if not fixed:
            why = " ".join(why.split())
            if why == NO_EDITS:
                unchanged.append(f"- **{theme}**")
                continue
            (failed if why.startswith("сбой:") else rejected).append(f"- **{theme}** — {why}")
            continue
        labels_only = why == LABELS_ONLY

        if not may_edit:
            notes.append(_proposed(theme, fixed))
            print(f"  ○ {theme}: правка готова, но запись выключена (cloud_edit_graph)")
            done += 1
            continue

        head, old_body, sources, manual = split_dossier(old)   # шапка как была
        if sources is None:     # недостижимо: `review` бракует такое досье до облака; страховка типа
            rejected.append(f"- **{theme}** — в досье нет раздела «## Источники» — не трогаем")
            continue
        manual = manual if manual and manual != "—" else None
        text = (head + fixed + "\n\n## Источники\n" + (sources + "\n\n" if sources else "\n")
                + "## Правки автора\n\n" + (manual or "—") + "\n")
        # Тело теперь ревизии: ночная пересборка его не трогает, снимает поле
        # «забыть встречу» (№841); поле — в той же записи, что строка
        # «Применено» отчёта, и не разойдётся с ним (вход n841-in1 S-C2)
        if not labels_only:
            text = dossier.with_revision(text, stamp[:10])     # шапка та же, что проверена до вызова

        # замок на одну запись; за время облачного вызова файл мог смениться —
        # перечитываем и сверяем с `old`, чужие правки не затираем (аудит 13.09, GLM I2).
        # Ожидание — не дольше остатка ночи: 10 мин на каждую из шести тем
        # вылезали за потолок на час (DS I1 по #561). None потолок не отдаёт
        # никогда (контракт night_wait_cap) — мёртвая ветка убрана.
        cap = live_gate.night_wait_cap(default=LOCK_WAIT)
        lock_wait = min(LOCK_WAIT, cap)
        with file_locks.graph_lock(lock_dir, lock_wait) as taken:
            if taken:
                try:
                    # снимок до чтения: между сверкой текста и записью идёт копия, и
                    # правка владельца в это окно (Obsidian замок не берёт) затиралась бы (n853-out1 Haiku I1)
                    seen = safe_write.stat_snapshot(path)
                    if seen is None:
                        # без снимка `expect=None` — запись без гейта: не пишем (n853-out2 Sonnet M1)
                        failed.append(f"- **{theme}** — сбой: снимок досье не взят перед записью")
                        continue
                    if path.read_text(encoding="utf-8") != old:
                        rejected.append(f"- **{theme}** — досье сменилось под рукой за время ревизии — не трогаем")
                        raced.append(_proposed(theme, fixed))
                        continue
                    # и состав — под тем же замком, что берёт «забыть встречу»: забытое за время
                    # облачного вызова не получит поле на пересказе (Sonnet C2, DS C1 второе окно)
                    if not dossier.in_sync(loaded_old, dossier.fingerprint_on_disk(members, files)):
                        rejected.append(f"- **{theme}** — источники сменились за время ревизии — не трогаем")
                        continue
                except (OSError, UnicodeDecodeError) as e:
                    failed.append(f"- **{theme}** — сбой: досье не перечитано перед записью ({e})")
                    continue
                try:
                    _backup(folder, stamp, path)
                except OSError as e:
                    # без копии не перезаписываем — правило tier3 (DS I2 / GLM I1 по #561)
                    failed.append(f"- **{theme}** — сбой: копия до правки не сделана ({e}) — без копии не перезаписываю")
                    continue
                # с переносом прав и меток файла (DS M2 по #561)
                if not safe_write.write_text(path, text, expect=seen):
                    # копия в `.backup/{stamp}/` остаётся: текст до правки владельца, не мусор
                    rejected.append(f"- **{theme}** — досье сменилось под рукой за время ревизии — не трогаем")
                    raced.append(_proposed(theme, fixed))
                    continue
        if not taken:
            # Замок не взят: граф занят соседом (разбор ночной встречи, ревизия до
            # 30 мин) или недоступен (нет каталога, ФС без flock). Это не сбой ночи:
            # остаток прогона идёт отчётом-рекомендацией, оплаченный ответ облака
            # не выбрасываем (GLM I2 и критика 1–2, DS M1 по #561)
            may_edit = False
            why_readonly = (f"Запись не состоялась: замок графа не взят (граф занят дольше "
                            f"{int(lock_wait // 60)} мин или замок недоступен); тумблер "
                            "cloud_edit_graph включён — правки ниже можно перенести руками.")
            notes.append(_proposed(theme, fixed))
            print(f"  ○ {theme}: замок графа не взят — правка в отчёт, дальше только отчёт")
            done += 1
            continue
        done += 1
        # счёт — против тела без меток: сама чистка не правка ревизии (финальный Opus n815 M4)
        stats = LABELS_ONLY if labels_only else revision_stats(graph_names.unlabel_speakers(old_body), fixed)
        applied.append(f"- **{theme}** — {stats}; копия до правки — "
                       f"`{CHAROITE.dossier_dir}/.backup/{stamp}/`")
        print(f"  ✓ {theme}: правки применены ({stats})")

    if (notes or raced or applied or rejected or failed or unchanged) and not dry:
        # Отчёт пишется в ОБОИХ режимах: с включённой правкой владелец раньше
        # узнавал о переписанном досье только из строки в логе (карточка №87).
        dest = graph / f"Служебное_ревизия_досье_{stamp}.md"
        report = (f"---\ntype: служебное\nдата: {stamp}\nмодель: {model}\n---\n"
                  f"# Ревизия досье ({model})\n\n")
        if applied:
            report += "## Применено\n\n" + "\n".join(applied) + "\n\n"
        if rejected:
            report += "## Отклонено\n\n" + "\n".join(rejected) + "\n\n"
        if unchanged:
            report += "## Без правок\n\n" + "\n".join(unchanged) + "\n\n"
        if failed:
            report += "## Сбои шага (не отказ по содержанию)\n\n" + "\n".join(failed) + "\n\n"
            FAILED_STEPS.extend(failed)
        if notes:
            # причина — фактическая: тумблер или замок, а не всегда «тумблер»
            # (аудит 13.09, GLM M5)
            report += ("## Предложено, но не применено\n\n" + why_readonly + "\n\n"
                       + "\n".join(notes) + "\n\n")
        if raced:
            report += ("## Предложено, но не применено: досье сменилось под рукой\n\n"
                       "Владелец правил досье за время ревизии — правку ниже можно перенести руками.\n\n"
                       + "\n".join(raced))
        safe_write.write_text(dest, report.rstrip() + "\n")   # обрыв не оставит обрезанный отчёт (GLM M3)
        print(f"  отчёт: {dest.name}")
        # ретеншн ПОСЛЕ записи: отчёты копились бесконечно (аудит GLM 17.08)
        for old in sorted(graph.glob("Служебное_ревизия_досье_*.md"))[:-KEEP_REPORTS]:
            old.unlink(missing_ok=True)
    return done


def main() -> int:
    charoite_paths.harden_umask()   # досье и отчёт ревизии — только владельцу (№385)
    ap = argparse.ArgumentParser(description="Облачная ревизия досье")
    ap.add_argument("--graph")
    ap.add_argument("--all-graphs", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = ap.parse_args()

    cfg = _cfg()
    if not privacy.cloud_enrich_enabled(cfg):
        print("облако выключено рубильником — пропуск")
        return 0
    try:
        provider = privacy.night_provider(cfg)
    except privacy.PrivacyRefused as e:
        print(f"провайдер ночи не принят: {e} — ревизия досье не начата")
        return EXIT_PRIVACY_REFUSED
    if provider == "tinfoil":
        # Молчаливый ноль не виден ни в статусе ночи, ни в брифе (in3 S-I6):
        # свой код, ночь ставит метку пропуска.
        print("досье через Tinfoil — этап 2 (№792), шаг пропущен: досье никуда не отправлены")
        return EXIT_STAGE_PENDING
    if provider != "anthropic":
        print(f"провайдер ночи {provider!r} шагу досье неизвестен — ничего не отправлено")
        return EXIT_PRIVACY_REFUSED

    if args.all_graphs:
        # Все vault-ы, а не только iCloud: настроенный graph_dir вне iCloud
        # раньше не попадал в ночную ревизию досье (аудит 17.08).
        graph_list = graphs.all_graphs(CHAROITE.dossier_dir)
    else:
        # --graph: голое имя («Работа») — сначала папка рядом с настроенным
        # графом, потом путь от корня данных; путь с «/», «~» или «.» — как
        # путь. Порядок важен: при совпадении имени с папкой в корне данных
        # брался бы не тот граф (круг-1 по PR #385, Sonnet).
        chosen = None
        if args.graph:
            base = graphs.graph_dir(cfg)
            bare = "/" not in args.graph and not args.graph.startswith(("~", "."))
            candidates = [base.parent / args.graph] if bare and base is not None else []
            candidates.append(graphs.resolve(args.graph))
            chosen = next((c for c in candidates if c is not None and c.is_dir()), None)
        else:
            chosen = graphs.graph_dir(cfg)
        if chosen is None or not chosen.is_dir():
            print(f"граф не найден: {args.graph or (cfg.get('sufler') or {}).get('graph_dir', '')}")
            return 1
        graph_list = [chosen]

    # Зонд CLI после резолва графа (при битом конфиге — сначала код 1, без
    # минуты на таймауты; DS r1 по #499) и до первой темы: сломанный бинарник —
    # не «сбой темы», а отказ инфраструктуры, ночь видит его кодом 3 (аудит 05.09)
    try:
        cloud.claude_bin_checked()
    except cloud.CloudCLIUnavailable as e:
        print(f"CLI облака не отвечает: {e} — ревизия досье не начата")
        return 3

    режим = "правит граф" if privacy.cloud_edit_graph_enabled(cfg) else "только отчёт"
    print(f"режим: {режим}")
    total = 0
    for g in graph_list:
        if not g.is_dir() or (CLI_DOWN[0] and not cli_back()):
            continue
        print(f"=== {g.name}")
        total += run(g, cfg, dry=args.dry, limit=args.limit)
    print(f"итого досье просмотрено: {total}")
    if CLI_DOWN[0]:
        print("CLI облака перестал запускаться по ходу ревизии — код 3")
        return 3
    if FAILED_STEPS:
        print(f"сбои шага: {len(FAILED_STEPS)} тем не проверено — код 2")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
