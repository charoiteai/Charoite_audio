"""Архив встреч для Finder: папка «дата — название», внутри вся документация.

Структура (в iCloud-вольте, рядом с графом — синкается на все устройства):
    рабочий проект/Встречи-архив/
        _ОГЛАВЛЕНИЕ.md
        2026-07-20 — Планирование релизов и задач на август/
            Стенограмма.md · Минутки.md · Подсказки и ответы.md
            Разбор.md · Ревизия Claude.md · Голоса и спикеры.md
            Граф.md   ← вики-ссылка на заметку встречи (все связи там)

Вызывается из graph_updater после каждой встречи; повторный вызов
до-подхватывает файлы, появившиеся позже (ревизия Опуса, диаризация).
Миграция всей истории: .venv/bin/python src/meeting_archive.py --all
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import enum
import json
import os
import pathlib
import re
import stat as _stat
import sys
import typing

from charoite_paths import resolve_root
from meeting_stamp import archive_time, derivative_path, files_with_stamp, graph_key, stamp_of
import channel_trace
import live_sidecar
import meeting_source
import safe_write
import graphs


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте.

    Снимок на уровне модуля считался при импорте, то есть раньше, чем точка
    входа успевала назвать корень: половина процесса жила в названном корне,
    половина — в выведенном из положения файла, и расхождение было немым
    (замер 21.09, №329).
    """
    return resolve_root(__file__)
ARCHIVE_DIR = "Встречи-архив"
# Канон поручений встречи (решение 23.09: вкладка «Задачи» показывает его
# пункты). Единственный файл папки, который правят люди: приложение, плагин,
# ночной контроль. Раскладывается не копией байтов, а `lay_canon` с паспортом (№366)
CANON_NAME = "Минутки.md"
CANON_KIND = live_sidecar.ARCHIVE_KINDS[CANON_NAME]
# суффикс исходника → человеческое имя в папке встречи
NICE = [
    ("_minutes.md", CANON_NAME),
    ("_hints.md", "Подсказки и ответы.md"),
    ("_разбор.md", "Разбор.md"),
    ("_ревизия_claude.md", "Ревизия Claude.md"),
    ("_спикеры.md", "Голоса и спикеры.md"),
    ("_live.md", "Черновик (live).md"),
]
DOCS_DIR = pathlib.PurePosixPath("Документация") / "Стенограммы встреч"
# Всё, что архиватор кладёт в папку встречи, — и только это. Уборка конфликтных
# копий (`dedup_graph.py`) и счётчик доктора берут список отсюда, а не держат
# свой: по имени вне списка копию не отличить от узла «Спринт 2» (Important
# Opus входного круга №361).
ARCHIVE_NAMES = frozenset({nice for _, nice in NICE} | {
    "Стенограмма.md", "Граф.md", "meeting.meta.json", "Открыть в Obsidian.command",
    "Саммари.md", "Тезисы.md", "Вопросы и ответы.md"})
# «Имя 2.md» … «Имя 12.md»: так iCloud называет копию, которую не смог свести
# с версией на сервере. «Имя 1» он не создаёт.
_COPY_RE = re.compile(r"^(?P<stem>.+) (?P<n>[2-9]|[1-9]\d+)(?P<ext>\.[^. ]+)$")


class ConflictCopy(typing.NamedTuple):
    """Копия «Имя N.ext» рядом с «Имя.ext». `same` — побайтно ли она равна
    оригиналу; None — не сравнивали (счётчику хватает имён)."""
    copy: pathlib.Path
    original: pathlib.Path
    same: bool | None


def _same_content(a: pathlib.Path, b: pathlib.Path) -> bool | None:
    """True/False — сравнили; None — прочитать не вышло. Нечитаемая копия — не
    «отличающаяся»: её нельзя ни убрать, ни отдать человеку как другую версию
    (Minor Opus круга 2 по №361)."""
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return None


def conflict_copies(graph: pathlib.Path, *, compare: bool = True) -> list[ConflictCopy]:
    """Конфликтные копии документов встреч: «Имя N.ext», у которой рядом лежит
    «Имя.ext». Смотрим только туда, где эти документы пишет конвейер: папки
    архива (имена из `ARCHIVE_NAMES`) и «Документация/Стенограммы встреч»
    (имена от стема стенограммы, только `.md`). Узлы графа не трогаем: у них
    число в имени бывает законным. Скрытые и служебные «_…» папки пропускаем,
    симлинки тоже."""
    places: list[tuple[pathlib.Path, frozenset[str] | None]] = []
    adir = graph / ARCHIVE_DIR
    if adir.is_dir():
        places += [(d, ARCHIVE_NAMES) for d in sorted(adir.iterdir())
                   if d.is_dir() and not d.is_symlink() and not d.name.startswith((".", "_"))]
    ddir = graph / DOCS_DIR
    if ddir.is_dir() and not ddir.is_symlink():
        places.append((ddir, None))
    found: list[ConflictCopy] = []
    for folder, names in places:
        for p in sorted(folder.iterdir()):
            m = _COPY_RE.match(p.name)
            if not m or p.is_symlink() or not p.is_file():
                continue
            base_name = m["stem"] + m["ext"]
            if names is not None and base_name not in names:
                continue
            if names is None and m["ext"] != ".md":
                continue
            base = p.with_name(base_name)
            if base.is_symlink() or not base.is_file():
                continue
            found.append(ConflictCopy(p, base, _same_content(p, base) if compare else None))
    return found


def _safe(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "-", name).strip()[:80]


def _obsidian_url(graph: pathlib.Path, rel_note: str) -> str:
    """obsidian://open на заметку. Имя вольта — из конфига Obsidian (папка,
    содержащая граф); фолбэк — родитель графа."""
    import json
    import urllib.parse
    vault = graph.parent.name
    try:
        cfg = json.loads((pathlib.Path.home() /
                          "Library/Application Support/obsidian/obsidian.json").read_text())
        for v in cfg.get("vaults", {}).values():
            vp = pathlib.Path(v.get("path", ""))
            if vp in graph.parents or vp == graph:
                vault = vp.name
                rel_note = str(pathlib.Path(graph.name) / rel_note.split(graph.name + "/", 1)[-1]) \
                    if not rel_note.startswith(graph.name) else rel_note
                break
    except Exception:  # noqa: BLE001
        pass
    q = urllib.parse.quote
    return f"obsidian://open?vault={q(vault)}&file={q(rel_note)}"


def _write_opener(path: pathlib.Path, url: str):
    """Кликабельный запуск obsidian:// из Finder. .webloc для не-HTTP схем
    macOS открывать отказывается (-10400) — .command работает всегда.
    Тот же ярлык повторно не пишется (№361), права ставятся, только если сбиты."""
    safe_write.write_text_if_changed(path, f'#!/bin/bash\nopen "{url}"\n')
    if _stat.S_IMODE(path.stat().st_mode) != 0o755:
        path.chmod(0o755)


def _excluded(graph: pathlib.Path) -> set[str]:
    """Stamp'ы, исключённые из архива руками: Встречи-архив/_исключено.md,
    по строке «2026-07-17_1029 — причина» (тесты звука, демо-зачитки)."""
    f = graph / ARCHIVE_DIR / "_исключено.md"
    if not f.exists():
        return set()
    # Штамп целиком, с секундами и суффиксом коллизии: обрезка до минуты
    # исключала владельца минуты по строке про соседку (аудит 30.08, GLM)
    return set(re.findall(r"\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2})?(?:-\d+)?", f.read_text(encoding="utf-8")))


def _manifest_id(folder: pathlib.Path) -> str | None:
    """meeting_id из манифеста папки; None — манифеста нет (наследие) или он бит."""
    try:
        return json.loads((folder / "meeting.meta.json").read_text(encoding="utf-8")).get("meeting_id")
    except (OSError, ValueError, AttributeError):
        return None


def _folders_for(graph: pathlib.Path, stamp: str) -> list[pathlib.Path]:
    """Все папки архива, относящиеся к этой встрече, — свежие первыми.

    Ищем по дате и времени в начале имени: тема в хвосте меняется, встреча —
    нет. Время сравнивается целиком («12-58 — », а не префиксом «12-58»):
    вторая встреча той же минуты лежит как «12-58-12 — …», и минутный ключ
    не должен её захватывать — раньше `_folders_for` переименовывал чужую
    папку под новую тему (аудит 17.08, карточка №39). Папка с манифестом,
    чей meeting_id — другая встреча, тоже чужая. Свежие первыми, потому что
    при склейке дублей выживать должна та папка, куда писали последней.
    """
    head = f"{stamp[:10]} {archive_time(stamp)}"
    root = graph / ARCHIVE_DIR
    if not root.exists():
        return []
    found = []
    for d in root.iterdir():
        if not d.is_dir() or not (d.name == head or d.name.startswith(head + " ")):
            continue
        owner = _manifest_id(d)
        if owner is not None and owner != stamp:
            continue
        found.append(d)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


# У саммари всё, что влияет на вывод, уже в хеше материалов: FRESH-пересборка
# была бы 13 секундами модели и новым текстом самого читаемого файла при каждом
# касании без повода (критика 1 GLM входного круга по №314). Одна политика на
# живой путь и обход — MISSING/STALE: доставка ревизии старит саммари через
# минутки (STALE), новая встреча — MISSING, и ни один автоматический путь не
# трогает UNKNOWN — незнание о 298 легаси не повод переписывать их моделью на
# первом касании (критика DS и GLM круга 2, схождение). UNKNOWN строит только
# явная команда `retro_fill --summary=rebuild` (после присвоения исправных),
# `--summary=adopt` не строит ничего — только присваивает.
SUMMARY_POLICY = live_sidecar.POLICY_RETRO
SUMMARY_POLICY_REBUILD = live_sidecar.POLICY_LIVE - {live_sidecar.FRESH}


class SummaryMode(str, enum.Enum):
    """Режим прохода по саммари — то единственное, чем живой путь отличается от
    `--summary=adopt` и `--summary=rebuild`. Режим едет через швы как значение
    перечисления, а не как пара «политика + флаг»: пустая политика («не строить
    ничего») ложна в Python, и подстановка дефолта по истинности на шве молча
    подменяла её дефолтом — adopt платил модели (Critical DS и GLM круга 3 по
    №314). У перечисления пустого значения нет, у цепочки «флаг → множество →
    истинность» — нет звеньев."""
    AUTO = "auto"          # живой путь и обход: MISSING/STALE строим, UNKNOWN не трогаем
    ADOPT = "adopt"        # присвоить исправное легаси без модели, ничего не строить
    REBUILD = "rebuild"    # присвоить исправное, остальное (MISSING/STALE/UNKNOWN) собрать

    @property
    def policy(self) -> frozenset[str]:
        return _MODE_POLICY[self]

    @property
    def adopts(self) -> bool:
        return self is not SummaryMode.AUTO


# единственный переводчик режима в политику — рядом с политиками, не на шве
_MODE_POLICY: dict[SummaryMode, frozenset[str]] = {
    SummaryMode.AUTO: SUMMARY_POLICY,
    SummaryMode.ADOPT: frozenset(),
    SummaryMode.REBUILD: SUMMARY_POLICY_REBUILD,
}


class Archived(typing.NamedTuple):
    """Исход архивации: папка и что стало с саммари. Исход едет возвратом по
    цепочке швов (`write_derivative` → `summary_pass` → `archive_meeting` →
    вызывающий), а не пересобирается чтением диска этажом выше — на этом дважды
    сошлись DS и GLM выходных кругов по №314 (круг 1 — шов записи, круг 2 —
    архивация выбрасывала исход, и отчёт ретро-обхода врал «пропущено» о только
    что пересобранном)."""
    folder: pathlib.Path
    summary: "SummaryOutcome"
    # последним и с умолчанием: `Archived` конструируют позиционно (№366);
    # None — в плане не было минуток, раскладка канона не звалась
    canon: "CanonOutcome | None" = None


def plan_materials(tdir: pathlib.Path, key: str, expected_debrief: pathlib.Path | None,
                   extra: typing.Mapping[str, pathlib.Path] | None = None) -> dict[str, pathlib.Path]:
    """План папки встречи: имя в папке → ровно один источник.

    «Писать только при изменении» даёт тишину, только если у каждого имени один
    источник. `files_with_stamp` отсекает соседку по секундам, но не двойника по
    теме: при ключе «…_Бюджет» в выдачу попадают и «…_Бюджет_MVP.md», и его
    минутки. Два источника на одно имя переписывали путь дважды на каждом
    проходе (Important Opus круга 2 по №361). Здесь побеждает точное имя
    «<ключ>.md» / «<ключ><суффикс>», а без него — первый по сортировке,
    как и раньше. Разбор пишет только `expected_debrief`, если он есть: его
    двойня под другим именем — не наш разбор, а сам он в цикле больше не
    пишется вторым разом (Minor Sonnet круга 2). Источник из `extra` главнее
    всего.
    """
    plan: dict[str, pathlib.Path] = {}
    exact: set[str] = set()
    for f in files_with_stamp(tdir, key, suffix=".md"):
        dest, suffix = "Стенограмма.md", ".md"
        for suf, nice in NICE:
            if f.name.endswith(suf):
                dest, suffix = nice, suf
                break
        if dest == "Разбор.md" and expected_debrief is not None:
            continue
        is_exact = f.name == f"{key}{suffix}"
        if dest not in plan or (is_exact and dest not in exact):
            plan[dest] = f
            if is_exact:
                exact.add(dest)
    if expected_debrief is not None:
        plan["Разбор.md"] = expected_debrief
    plan.update(extra or {})
    return plan


def archive_meeting(graph: pathlib.Path, tdir: pathlib.Path, stamp: str, title: str,
                    files_key: str | None = None, *,
                    mode: SummaryMode = SummaryMode.AUTO,
                    extra: typing.Mapping[str, pathlib.Path] | None = None,
                    unhide: bool = True) -> Archived | None:
    """Собирает/обновляет папку встречи; возвращает папку и исход саммари
    (None — встреча исключена). `mode` — режим прохода по саммари
    (`SummaryMode`); дефолт — в сигнатуре, архивация режим не интерпретирует и
    не подставляет, только пробрасывает в `summary_pass` (круг 3 по №314).

    `files_key` — стем главного файла встречи («2026-08-03_113012» у ещё не
    переименованной посекундной встречи, «2026-08-03_1130_Планёрка» после
    наката темы): её файлы — ровно `<стем>.md` и `<стем>_*.md`. Без ключа
    ищем по минутному штампу с границей — так зовут retro-прогоны, где стем
    и есть минутный штамп с темой.

    `extra` — «имя в папке → источник», который вызывающий знает точнее
    ключа файлов: облачная ревизия называет свой файл минутным штампом, а ключ
    посекундной встречи его не находит. Такой источник главнее найденного по
    ключу. Файлы папки пишет только архиватор: раньше ревизия дописывала копию
    сама, поверх той, что архиватор положил миллисекундой раньше (Critical
    Opus входного круга №361).

    Всё пишется только при изменении и не на месте (`safe_write.copy_if_changed`
    и `write_text_if_changed`): повторный проход без новостей папку не трогает.
    Канон минуток — не копия, а `lay_canon` с паспортом: правленый человеком
    канон раскладка не трогает (№366), исход — в `Archived.canon`.

    `unhide=False` — для массовых обходов (`migrate_all`, цикл `retro_fill`):
    снятие UF_HIDDEN идёт по всему графу (замер 25.09 — медиана 477 мс на
    9191 файл), и обход бэклога платил его на каждой встрече, ≈151 с из ≈200.
    Такой обход зовёт `_unhide` сам, один раз, в `finally` вокруг цикла.
    Оглавление пересобирается и тогда на каждой встрече: после `kill -9`
    посреди обхода оно остаётся верным, а починить его потом нечем (№362).
    """
    if stamp in _excluded(graph):
        return None
    pretty = (title or "").replace("_", " ").strip() or "встреча"
    # время в имени папки: 5 встреч в день неотличимы по «дата — тема»,
    # а mtime врёт после доработок (ревизии дописывают файлы). Двоеточие
    # в имени нельзя (Finder/Windows/синк) — «11-30» читаемо и безопасно
    # Посекундный ключ (вторая встреча той же минуты) — «12-58-12», чтобы
    # в Finder две встречи лежали рядом, но порознь.
    nice_time = archive_time(stamp)
    folder = graph / ARCHIVE_DIR / f"{stamp[:10]} {nice_time} — {_safe(pretty)}"
    for legacy in (graph / ARCHIVE_DIR / f"{stamp} — {_safe(pretty)}",
                   graph / ARCHIVE_DIR / f"{stamp[:10]} — {_safe(pretty)}"):
        if legacy.exists() and not folder.exists():
            legacy.rename(folder)   # старые форматы: тихо мигрируем при обновлении
    # Тема встречи уточняется при повторных разборах, и папка называется по
    # теме. Прежде это плодило вторую папку на ту же встречу: у 21 встречи из
    # 62 в архиве оказалось по две — «Бюджет MVP» и «Бюджет и ресурсы MVP».
    # Папка на встречу одна: старую переименовываем.
    if not folder.exists():
        for old in _folders_for(graph, stamp):
            if old != folder:
                old.rename(folder)
                break
    folder.mkdir(parents=True, exist_ok=True)
    # Только файлы ЭТОЙ встречи: минутный штамп — префикс секундного, и голый
    # глоб `{stamp}*.md` тянул в папку файлы второй встречи той же минуты
    # (крэш-рестарт), причём с перезаписью по одноимённому назначению
    # (аудит DeepSeek 16.08). Правило границы — одно, в meeting_stamp.
    # Разбор — тот, что назвали писатели (`derivative_path` от главного файла),
    # а не любой файл с суффиксом под префиксом стема: у голой посекундной
    # стенограммы разбор назван минутным ключом графа и под префикс стема не
    # попадает вовсе, а старое имя от стема рядом с ним — двойня (№309, круг 2).
    # Ожидаемого файла нет — прежний порядок: что нашлось по префиксу.
    main = tdir / f"{files_key or stamp}.md"
    expected_debrief = derivative_path(main, "debrief", graph) if main.is_file() else None
    if expected_debrief is not None and not expected_debrief.is_file():
        expected_debrief = None
    canon = None
    for dest, src in plan_materials(tdir, files_key or stamp, expected_debrief, extra).items():
        if dest == CANON_NAME:
            canon = lay_canon(src, folder / dest, main)
        else:
            safe_write.copy_if_changed(src, folder / dest)
    obs_url = _obsidian_url(graph, f"{graph.name}/Встречи/{stamp}")
    # Ссылку на заметку пишем, ТОЛЬКО если заметка есть в ЭТОМ графе. Папка
    # архива и узел встречи расходятся штатно: сфера встречи определяется по
    # стенограмме уже после старта разбора, и личная встреча уезжает узлом в
    # свой граф, а папку в рабочем оставляет прежний проход; тот же расход
    # даёт непоставленный узел. Ссылка в никуда после этого живёт вечно —
    # снятие мёртвых ссылок идёт раньше этой записи и её не видит (№276).
    # Замер боевого графа 16.09: 20 папок из 278 ссылались на несуществующую
    # заметку, 6 из них — сентябрьские.
    note = graph / "Встречи" / f"{stamp}.md"
    if note.is_file():
        where = (f"[Открыть заметку встречи в Obsidian]({obs_url}) — дальше «Локальный граф» "
                 f"покажет все связи (люди, системы, решения).\n\n"
                 f"Внутри Obsidian: [[Встречи/{stamp}]] · оглавление проекта [[_MOC]]\n")
    else:
        # Без ссылки и без URL: и то и другое ведёт в пустоту. Сообщение —
        # человеку, который открыл папку и ищет заметку.
        where = ("Заметки этой встречи в графе «" + graph.name + "» нет: она могла уехать "
                 "в граф своей сферы (личный, проектный) или не сложиться вовсе. "
                 "Документы встречи — рядом, в этой папке.\n")
    safe_write.write_text_if_changed(folder / "Граф.md",
        f"---\ntype: ссылка\nдата: {stamp}\n---\n"
        f"# Граф этой встречи\n\n" + where,
        encoding="utf-8",
    )
    # двойной клик в Finder → Obsidian на заметке встречи (Граф.md открывался текстом)
    if note.is_file():
        _write_opener(folder / "Открыть в Obsidian.command", obs_url)
    else:
        # Ярлык прежнего прогона открывал бы Obsidian на несуществующей заметке
        (folder / "Открыть в Obsidian.command").unlink(missing_ok=True)
    # оговорка о неполной записи — из сайдкара оригинала (факт о записи живёт
    # там); хвост копии — запасной путь для архива без сайдкара (критика GLM круга 3).
    # Читается один раз: и тезисы, и саммари получают одно значение (Minor GLM по №314)
    recording_note = channel_trace.recording_note(main)
    _derive_extras(folder, recording_note=recording_note)
    # саммари — после обновления копий материалов, одним проходом «решить →
    # присвоить → построить», исход значением (Critical DS и Important GLM круга 2)
    summary = summary_pass(folder, main, recording_note, mode=mode)
    _write_manifest(folder, stamp, pretty)
    _rebuild_index(graph)
    # Флаг снимаем со ВСЕГО графа, а не только с архивной папки.
    # iCloud метит UF_HIDDEN что угодно в своём контейнере, и на папках
    # «Люди», «Системы», «Встречи» это уже случилось: обходчик поиска
    # (FileManager с .skipsHiddenFiles) видел 546 файлов из 1172 — сердце
    # графа стало невидимым, и приложение отвечало «в памяти этого нет» про
    # людей, с которыми встречи были на этой неделе. Поиск с тех пор на флаг
    # не смотрит, но и графу незачем оставаться помеченным: он же открывается
    # в Finder и Obsidian.
    if unhide:
        _unhide(graph)
    return Archived(folder, summary, canon)


# Названия разделов саммари на трёх языках. Одно место на весь модуль:
# отсюда собирается промпт (пишем на языке конфига) и отсюда же читается
# готовый документ (разбираем на ЛЮБОМ языке).
#
# Разница принципиальная. Генерация зависит от `sufler.language` — что человек
# выбрал, на том и пишем. Разбор от конфига зависеть не должен: в архиве лежат
# встречи, записанные до переключения языка, и пересборка старой русской
# встречи при `language: en` не имеет права потерять её решения. Раньше и то,
# и другое было жёстко русским: при en/zh минутки выходили английские, а
# Саммари.md — всё равно русское.
SUMMARY_SECTIONS: dict[str, dict[str, str]] = {
    "ru": {
        "gist": "Суть одной строкой:",
        "topics": "О чём говорили",
        "decisions": "Решили",
        "tasks": "Поручения",
        "questions": "Открытые вопросы",
        "history": "Связь с прошлыми встречами",
        "none": "решений не было",
    },
    "en": {
        "gist": "Bottom line:",
        "topics": "What we talked about",
        "decisions": "Decisions",
        "tasks": "Action items",
        "questions": "Open questions",
        "history": "Link to past meetings",
        "none": "no decisions were made",
    },
    "zh": {
        "gist": "一句话概括：",
        "topics": "讨论了什么",
        "decisions": "决定",
        "tasks": "任务",
        "questions": "待解决问题",
        "history": "与过往会议的关联",
        "none": "没有做出决定",
    },
}

#: Все написания раздела на всех языках — для разбора готового документа.
def section_names(key: str) -> tuple[str, ...]:
    names = [SUMMARY_SECTIONS[lang][key] for lang in ("ru", "en", "zh")]
    # Исторические написания: «Решения» встречалось наравне с «Решили».
    if key == "decisions":
        names.append("Решения")
    return tuple(dict.fromkeys(names))


def _config_lang() -> str:
    """Язык НОВЫХ документов — из `sufler.language`, с откатом на русский.

    Отдельно от `summary_lang`: тот отвечает на вопрос «на чём написан этот
    файл», а этот — «на чём писать следующий». Конфиг может не читаться
    (архив зовут и из тестов, и из миграции) — тогда пишем по-русски, как
    было всегда.
    """
    try:
        import yaml
        cfg = yaml.safe_load((_root() / "config" / "config.yaml").read_text(encoding="utf-8"))
        lang = str((cfg.get("sufler") or {}).get("language", "ru")).strip().lower()
    except Exception:  # noqa: BLE001 — язык документа не повод ронять архив
        return "ru"
    return lang if lang in SUMMARY_SECTIONS else "ru"


def _lang_name() -> str:
    """Как назвать язык в промпте (промпт остаётся русским, меняется ответ)."""
    return {"ru": "по-русски", "en": "по-английски", "zh": "по-китайски"}[_config_lang()]


def summary_lang(text: str) -> str:
    """На каком языке написано это саммари — по его же заголовкам.

    Нужен там, где документ обрабатывается после генерации: обрезка по лимиту
    и восстановление раздела решений. Конфиг здесь не годится — он говорит,
    на чём писать НОВОЕ, а на диске лежат встречи всех прошлых языков.
    """
    for lang in ("ru", "en", "zh"):
        words = SUMMARY_SECTIONS[lang]
        for key in ("decisions", "tasks", "questions", "topics"):
            if re.search(rf"(?m)^##\s*{re.escape(words[key])}\s*$", text):
                return lang
    return "ru"


def summary_gist(text: str) -> str | None:
    """Суть одной строкой — на любом из трёх языков."""
    for lang in ("ru", "en", "zh"):
        marker = SUMMARY_SECTIONS[lang]["gist"]
        match = re.search(rf"\*\*{re.escape(marker)}\*\*\s*(.+)", text)
        if match:
            return match.group(1).strip()
    return None


def _manifest_items(text: str, names: tuple[str, ...]) -> list[str]:
    """Пункты секции саммари для стабильных полей манифеста."""
    for name in names:
        match = re.search(
            rf"(?m)^##\s*{re.escape(name)}\s*$\n(.*?)(?=^##\s|\Z)",
            text,
            re.S | re.M,
        )
        if not match:
            continue
        items = [
            re.sub(r"^\s*[-*]\s+", "", line).strip()
            for line in match.group(1).splitlines()
            if re.match(r"^\s*[-*]\s+\S", line)
        ]
        return [item for item in items if item]
    return []


def _manifest_participants(transcript: str) -> list[str]:
    for line in transcript.splitlines()[:8]:
        if not line.startswith(("Участники", "Participants", "参会者")) or ":" not in line:
            continue
        return [name.strip() for name in line.split(":", 1)[1].split(",") if name.strip()]
    return []


def _manifest_duration(transcript: str) -> int | None:
    times = []
    for line in transcript.splitlines():
        if not line.startswith("**"):
            continue
        for hour, minute in re.findall(r"\b(\d{1,2}):(\d{2})\b", line):
            h, m = int(hour), int(minute)
            if h < 24 and m < 60:
                times.append(h * 60 + m)
    if len(times) < 2:
        return None
    span = times[-1] - times[0]
    if span < 0:
        span += 24 * 60
    return span or None


def build_manifest(folder: pathlib.Path, stamp: str, title: str) -> dict:
    """Производный индекс встречи; все поля можно восстановить из Markdown —
    поэтому состояния паспорта саммари здесь нет: оно живёт в сайдкаре
    стенограммы и читается `summary_state()`, а копия в манифесте расходилась
    бы с паспортом при каждой записи и обнулялась переименованием (Important
    DS и GLM выходного круга по №314)."""
    summary_path = folder / "Саммари.md"
    transcript_path = folder / "Стенограмма.md"
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    gist = summary_gist(summary)
    files = {
        key: name for key, name in (
            ("transcript", "Стенограмма.md"),
            ("summary", "Саммари.md"),
            ("minutes", "Минутки.md"),
            ("debrief", "Разбор.md"),
        ) if (folder / name).exists()
    }
    started = None
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(?:\d{2})?", stamp)
    if match:
        started = f"{match.group(1)}T{match.group(2)}:{match.group(3)}:00"
    return {
        "schema_version": 1,
        "meeting_id": stamp,
        "title": title,
        "started_at": started,
        "duration_minutes": _manifest_duration(transcript),
        "participants": _manifest_participants(transcript),
        "summary": gist,
        "decisions": _manifest_items(summary, section_names("decisions")),
        "action_items": _manifest_items(summary, section_names("tasks")),
        "open_questions": _manifest_items(summary, section_names("questions")),
        "files": files,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _write_manifest(folder: pathlib.Path, stamp: str, title: str) -> None:
    """Атомарно обновить манифест после сборки человекочитаемых файлов.

    `updated_at` — время последнего настоящего изменения, а не последнего
    прохода: манифест, у которого поменялась бы одна метка времени, не
    переписывается (№361). Читатели манифеста (`MeetingCard`, `GraphStore`,
    `_manifest_id`) это поле не используют — проверено входным кругом."""
    path = folder / "meeting.meta.json"
    fresh = build_manifest(folder, stamp, title)
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    if isinstance(old, dict) and {k: v for k, v in old.items() if k != "updated_at"} \
            == {k: v for k, v in fresh.items() if k != "updated_at"}:
        return
    safe_write.write_text(path, json.dumps(fresh, ensure_ascii=False, indent=2) + "\n")


def _history_context(folder: pathlib.Path) -> str:
    """История для саммари: Ядра (хроника до даты встречи) + 2 прошлых саммари.

    Для перегенерации старых встреч будущее не утекает в прошлое: строки
    хроники ядра позже даты встречи отсекаются, саммари берутся только более
    ранние (сортировка имён папок = сортировка дат).
    """
    date_cut = folder.name[:10]
    parts: list[str] = []
    cores = folder.parent.parent / "Ядра"
    if cores.exists():
        for p in sorted(cores.glob("*.md")):
            if p.name.startswith("_"):
                continue
            text = p.read_text(encoding="utf-8")
            # формат хроники: «- [[Встречи/2026-07-20_1053]] — событие»
            hist = [ln for ln in text.splitlines()
                    if (m := re.search(r"- \[\[Встречи/(\d{4}-\d{2}-\d{2})", ln))
                    and m.group(1) <= date_cut]
            if hist:
                # хроника пишется newest-first (upsert_core вставляет под заголовок) —
                # берём ВЕРХНИЕ 3 (ближайшие к дате встречи), не hist[-3:] (самые старые)
                parts.append(f"Ядро «{p.stem}»:\n" + "\n".join(hist[:3]))
    prev = [p for p in sorted(folder.parent.iterdir())
            if p.is_dir() and p.name < folder.name and (p / "Саммари.md").exists()]
    for p in prev[-2:]:
        parts.append(f"Саммари встречи {p.name}:\n"
                     + (p / "Саммари.md").read_text(encoding="utf-8")[:1200])
    return "\n\n".join(parts)[:3500]


def decisions_of(folder: pathlib.Path) -> list[str]:
    """Решения встречи так, как их записали минутки, — по пункту на строку.

    Минутки и разбор пишут решения структурно: заголовок «Решения» (иногда
    «### ✅ Решения:») и под ним список. Просить модель найти их заново —
    лишний риск: замер 03.08 на одной и той же встрече дал 1 попадание из 3.
    Дешевле подать готовое.
    """
    for name in (CANON_NAME, "Разбор.md"):
        f = folder / name
        if not f.exists():
            continue
        text = _read_material(f)
        m = re.search(r"(?m)^#{2,4}[^\n]*Решени\w*[^\n]*$\n(.*?)(?=\n#{2,4} |\Z)", text, re.S)
        if not m:
            continue
        items = [re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", ln).strip()
                 for ln in m.group(1).splitlines()
                 if re.match(r"\s*(?:[-*]|\d+[.)])\s+\S", ln)]
        if items:
            return items
    return []


def _force_decisions(text: str, decisions: list[str], per_item: int = 165) -> str:
    """Вернуть в саммари решения, если модель написала «решений не было».

    Промпт этот раздел не удерживает: модель то находит решения, то нет, и
    цена ошибки высокая — «решений не было» поверх трёх записанных решений
    читается как факт. Раз данные есть, последнее слово за кодом.
    """
    if not decisions:
        return text
    # Язык берём из самого документа, а не из конфига: пересборка старой
    # русской встречи при `language: en` обязана лечить её же русский раздел.
    lang = summary_lang(text)
    words = SUMMARY_SECTIONS[lang]
    if words["none"].lower() not in text.lower():
        return text

    def short(s: str) -> str:
        s = re.sub(r"\*\*", "", s).strip()
        if len(s) <= per_item:
            return s
        return s[:per_item].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"

    head = f"## {words['decisions']}"
    block = head + "\n" + "\n".join(f"- {short(d)}" for d in decisions[:3])
    return re.sub(rf"(?ms)^{re.escape(head)}\n.*?(?=^## |\Z)", block + "\n\n", text)


def _trim_summary(text: str, limit: int = 900, per_item: int = 165, per_section: int = 3) -> str:
    """Гарантирует лимит саммари структурно, а не обрезкой по символу.

    Промптом объём не удержать: модель не считает собственную длину (замер
    22.07: при лимите «900 знаков / 120 слов» qwen выдавал 1421-1830). Поэтому
    режем детерминированно — длинные пункты по границе слова, лишние пункты
    сверх per_section, а если всё ещё длинно — целиком наименее важные разделы
    с конца. Суть, решения и поручения переживают обрезку последними.
    """
    if len(text) <= limit:
        return text

    def cut(line: str) -> str:
        if len(line) <= per_item:
            return line
        head = line[:per_item].rsplit(" ", 1)[0].rstrip(" ,;:—-")
        return head + "…"

    blocks = re.split(r"(?m)^(?=## )", text)
    out_blocks: list[str] = []
    for b in blocks:
        lines = b.rstrip().splitlines()
        kept, items = [], 0
        for ln in lines:
            if ln.lstrip().startswith(("- ", "* ")):
                if items >= per_section:
                    continue
                items += 1
                kept.append(cut(ln))
            else:
                kept.append(cut(ln) if len(ln) > per_item * 2 else ln)
        out_blocks.append("\n".join(kept))

    # Всё ещё длинно — жертвуем разделами по важности, а не по месту в тексте.
    # Прежде отбрасывался хвост, и «Поручения» гибли раньше обзорного «О чём
    # говорили»: у встречи 15.07 из выжимки пропало, кто что должен сделать, —
    # то есть ровно то, ради чего её открывают. Порядок жертв — от наименее
    # ценного к более ценному; суть, решения и поручения не трогаем.
    words = SUMMARY_SECTIONS[summary_lang(text)]
    for head in (f"## {words['history']}", f"## {words['questions']}", f"## {words['topics']}"):
        if len(("\n\n".join(out_blocks)).strip()) <= limit:
            break
        out_blocks = [b for b in out_blocks if not b.lstrip().startswith(head)]
    # Если и теперь длинно — убираем по одному пункту из самого раскормленного
    # раздела, а не раздел целиком. Иначе на очень длинной встрече исчезали
    # «Поручения»: они шли последними и попадали под нож, хотя пережить обрезку
    # должны были в первую очередь.
    def size() -> int:
        return len(("\n\n".join(out_blocks)).strip())

    while size() > limit:
        fattest, best = -1, 0
        for i, b in enumerate(out_blocks):
            items = [ln for ln in b.splitlines() if ln.lstrip().startswith(("- ", "* "))]
            if len(items) > 1 and len(b) > best:
                fattest, best = i, len(b)
        if fattest < 0:
            break                       # резать больше нечего, лимит недостижим
        lines = out_blocks[fattest].splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].lstrip().startswith(("- ", "* ")):
                del lines[i]
                break
        out_blocks[fattest] = "\n".join(lines)
    return ("\n\n".join(out_blocks)).strip()


# Обрезки материалов — константы канона: они входят в хеш источника саммари.
# Смена потолка — законный повод пересобрать (модель увидит другое).
SUMMARY_CAPS = (("Минутки.md", 3500), ("Тезисы.md", 1500), ("Разбор.md", 2000), ("Стенограмма.md", 4000))
class MaterialUnreadable(Exception):
    """Материал саммари не прочитался — строго, как его читает хеш источника.
    Пропустить его нельзя: `summary_source_sha` считается по составу списка,
    и пропуск сделал бы исправное саммари STALE — модель пересобрала бы его без
    минуток, а после восстановления файла ещё раз. Поэтому читатели остаются
    строгими, а вызывающий получает имя файла значением (у `UnicodeDecodeError`
    его нет) и решает сам: правленый канон минуток (№366) бывает в чужой
    кодировке, и ронять им архивацию встречи нельзя."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


def _read_material(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise MaterialUnreadable(path.name) from e


def summary_materials(folder: pathlib.Path) -> list[tuple[str, str]]:
    """Что модель увидит как материалы: имя файла → обрезка по канону (у
    стенограммы важнее конец — итоги, у остальных — начало). Одна функция для
    промпта и для хеша источника: писатель паспорта и читатель свежести не
    расходятся (урок №317)."""
    parts: list[tuple[str, str]] = []
    for name, cap in SUMMARY_CAPS:
        f = folder / name
        if f.exists():
            text = _read_material(f)
            parts.append((name, text[-cap:] if name == "Стенограмма.md" else text[:cap]))
    return parts


def summary_source_sha(materials: list[tuple[str, str]], decided: list[str],
                       recording_note: str | None) -> str:
    """Хеш источника саммари — канон входов, НЕ рендер промпта: слова шаблона и
    язык конфига в него не входят. Правка формулировки промпта не старит
    саммари; смена языка конфига говорит, на чём писать НОВОЕ, и не имеет права
    переписывать документы прошлого языка (Important DS и Minor GLM входного
    круга по №314). История (ядра, прошлые саммари) — тоже вне: её смена меняет
    раздел связи с прошлым, не факты встречи."""
    canon = json.dumps({"materials": materials, "decided": decided, "note": recording_note or ""},
                       ensure_ascii=False, sort_keys=True)
    return live_sidecar.sha(canon)


SUMMARY_HEAD = "---\ntype: саммари\n"    # начало нашего документа — гейт присвоения легаси


def summary_adoptable(folder: pathlib.Path, recording_note: str | None) -> str | None:
    """Годится ли саммари без паспорта под присвоение (None) и почему нет
    (причина строкой). Критерий — весь канон источника, не половина: материалы
    не новее файла (mtime, допуск 1 с — `copy2` хранит исходные mtime, саммари
    пишется после копий), документ наш по структуре (`SUMMARY_HEAD`, 299 из 299
    боевых 19.09) и оговорка о записи либо пуста, либо уже в тексте — иначе
    паспорт заморозил бы саммари, которое оговорку не видело (Critical GLM
    выходного круга по №314). Иначе на первом живом касании модель переписала бы
    224 из 298 исправных саммари боевого архива (Critical DS входного круга);
    материал новее — знания нет, UNKNOWN остаётся (74 из 298 на 19.09)."""
    out = folder / "Саммари.md"
    try:
        text = out.read_text(encoding="utf-8")
        own = out.stat().st_mtime
        newer = [name for name, _ in SUMMARY_CAPS
                 if (folder / name).exists() and (folder / name).stat().st_mtime > own + 1]
    except (OSError, UnicodeDecodeError):
        return "файл не читается"
    if not text.strip():
        return "файл пуст"
    if not text.startswith(SUMMARY_HEAD):
        return "не наш документ"
    if newer:
        return "материалы новее: " + ", ".join(newer)
    if recording_note and recording_note not in text:
        return "без оговорки о записи"
    return None


@dataclasses.dataclass(frozen=True)
class SummaryOutcome:
    """Что случилось с саммари за один проход — значение, не строка отчёта и не
    состояние, из которого исход выводят задним числом (№277 «причина как
    значение»; Critical DS круга 2 по №314). `state` — состояние паспорта после
    прохода, `reason` — почему не присвоено или не построено."""
    ADOPTED = "adopted"    # легаси получило паспорт без модели
    BUILT = "built"        # собрано моделью и записано с паспортом
    KEPT = "kept"          # не трогали: FRESH или HUMAN
    SKIPPED = "skipped"    # политика не строит это состояние (UNKNOWN вне rebuild, MISSING при adopt)
    FAILED = "failed"      # модель не ответила или упала — повтор прогона имеет смысл
    REFUSED = "refused"    # запись отклонена (файл менялся под рукой) — повтор бессмыслен, смотреть файл
    NONE = "none"          # материалов нет — саммари не о чём

    action: str
    state: str | None
    reason: str | None = None

    def line(self) -> str | None:
        """Слова отчёта — одно место на все вызывающие (Minor GLM круга 2:
        пересказ оракула в каждом отчёте расходился с ним словами)."""
        if self.action == self.NONE:
            return None
        if self.action == self.ADOPTED:
            return "саммари присвоено"
        if self.action == self.BUILT:
            return "саммари"
        if self.action in (self.FAILED, self.REFUSED):
            return f"саммари — {self.reason}"
        tail = f": {self.reason}" if self.reason else ""
        return f"саммари {self.state}{tail}"

    @property
    def made(self) -> bool:
        return self.action in (self.ADOPTED, self.BUILT)


@dataclasses.dataclass(frozen=True)
class CanonOutcome:
    """Что раскладка сделала с каноном минуток `Минутки.md` за проход (№366) —
    значением, по образцу `SummaryOutcome`: слова действий те же там, где тот же
    смысл. `state` — состояние паспорта канона (после записи — переспрошенное у
    оракула), `differing` — сколько строк канона и источника различаются
    (у правленого человеком), `source` — источник канона из плана: копию в
    «Документации» пишут из канона ровно для этого файла."""
    CREATED = "created"      # канона не было (или был пуст) — записан текст источника
    UPDATED = "updated"      # канон наш, источник сменился — переписан
    UNCHANGED = "unchanged"  # писать нечего или некуда — причина в `reason`
    ADOPTED = "adopted"      # текст равен источнику — паспорт без записи файла
    KEPT = "kept"            # канон не наш (или без паспорта и отличается) — не трогали
    REFUSED = "refused"      # гейт отказал: файл менялся под рукой — повтор бессмыслен
    FAILED = "failed"        # не прочиталось — повтор имеет смысл

    action: str
    state: str | None
    reason: str | None = None
    differing: int = 0
    source: pathlib.Path | None = None

    def line(self) -> str:
        """Слова отчёта — одно место на все вызывающие."""
        tail = f": {self.reason}" if self.reason else ""
        if self.action == self.KEPT and self.reason != CANON_NOT_UTF8:
            tail += f"; строк расходится с машинной версией: {self.differing}"
        return f"канон минуток {self.action}{tail}"

    @property
    def alarming(self) -> bool:
        """Печатать ли исход на живом пути: канон не тронут, не прочитался или
        паспорт после записи не встал. Последнее молча замораживало бы канон:
        сменится источник — следующий проход увидит UNKNOWN без паспорта и
        `kept` навсегда (Important DS выходного круга по №366)."""
        return self.action in (self.KEPT, self.REFUSED, self.FAILED) or self.reason == _CANON_NO_PASSPORT


CANON_NOT_UTF8 = "канон не в UTF-8"
_CANON_RACE = "файл менялся под рукой"
_CANON_UNREAD = "канон не прочитался"
_CANON_NO_PASSPORT = "паспорт не записан"


def _differing(a: str, b: str) -> int:
    """Симметричная разность строк с кратностью: сколько строк есть в одном
    тексте и нет в другом."""
    ca, cb = collections.Counter(a.splitlines()), collections.Counter(b.splitlines())
    return sum(((ca - cb) + (cb - ca)).values())


def lay_canon(src: pathlib.Path, canon: pathlib.Path, main: pathlib.Path) -> CanonOutcome:
    """Разложить минутки `src` в канон `canon` папки встречи — с паспортом
    производной в сайдкаре стенограммы `main` (вид `canon_minutes`, №366).

    До №366 канон клался байтами источника на каждом проходе и стирал отметки
    человека (№370). Теперь раскладка переписывает канон, только пока он —
    её собственный текст (FRESH/STALE); чужая правка любого писателя
    (приложение, плагин, ночной контроль) делает его HUMAN, и такой канон не
    трогается. Слияние машинных правок с правленым каноном — №391.

    Источник и канон читаются текстом, как их читает оракул
    `derivative_state` (строгий UTF-8, универсальные переводы строк), а не
    `bytes.decode`: иначе источник с CRLF получал бы паспорт, которого оракул
    не воспроизводит, и каждый проход кончался бы присвоением (инвариант №317:
    писатель паспорта и читатель свежести берут текст одной функцией).
    Источник читается один раз: времена канона и записанный текст — одной
    версии. Канон с текстом, равным источнику, не переписывается никогда
    (новый inode на каждом проходе — конфликтные копии iCloud, №361); без
    паспорта он получает паспорт на сравнённый текст. Без стенограммы паспорт
    не пишется ни в одной ветке: `merge` и `remember` создали бы сироту."""
    def out(action: str, state: str | None, reason: str | None = None, differing: int = 0) -> CanonOutcome:
        return CanonOutcome(action, state, reason, differing, src)

    try:
        with open(src, encoding="utf-8") as fh:
            st = os.fstat(fh.fileno())
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return out(CanonOutcome.FAILED, None, "источник не прочитался")
    if not text:
        # пустой канон оракул читает как MISSING — без этого правила канон
        # переписывался бы на каждом проходе
        return out(CanonOutcome.UNCHANGED, None, "источник пуст")
    times = (st.st_atime_ns, st.st_mtime_ns)
    source_sha = live_sidecar.sha(text)

    before = safe_write.stat_snapshot(canon)          # ДО любого чтения канона
    current = None
    if before is not None:
        try:
            current = canon.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # оракул дал бы HUMAN при паспорте и UNKNOWN без него — причина из
            # чтения раскладки. Строк не считаем: текст с заменой байтов дал бы
            # «разошлось всё». Выход — у человека: пересохранить в UTF-8 (№275)
            return out(CanonOutcome.KEPT, None, CANON_NOT_UTF8)
        except OSError:
            return out(CanonOutcome.FAILED, None, _CANON_UNREAD)
    owner = main.is_file()
    meta = live_sidecar.read(main) or {}
    state = live_sidecar.derivative_state(canon, meta, CANON_KIND, source_sha)

    def after() -> str:
        return live_sidecar.derivative_state(canon, live_sidecar.read(main) or {}, CANON_KIND, source_sha)

    if current == text:
        # равный текст не пишется ни при каком состоянии; без паспорта — признать
        # своим на СРАВНЁННЫЙ текст, не `adopt`: тот перечитал бы файл сам, и
        # правка между сравнением и его чтением получила бы паспорт машины
        if state == live_sidecar.FRESH:
            return out(CanonOutcome.UNCHANGED, state)
        if not owner:
            return out(CanonOutcome.UNCHANGED, state, "стенограммы нет — паспорт некуда записать")
        if safe_write.stat_snapshot(canon) != before:
            return out(CanonOutcome.UNCHANGED, state, _CANON_RACE)
        if not live_sidecar.merge(main, {f"{CANON_KIND}_sha256": live_sidecar.sha(current),
                                         f"{CANON_KIND}_source_sha256": source_sha,
                                         f"{CANON_KIND}_adopted": live_sidecar.adopted_stamp()}):
            return out(CanonOutcome.UNCHANGED, state, _CANON_NO_PASSPORT)
        return out(CanonOutcome.ADOPTED, after())
    if before is None and state != live_sidecar.MISSING:
        return out(CanonOutcome.FAILED, state, _CANON_UNREAD)     # stat упал на существующем файле
    if state == live_sidecar.MISSING:
        if before is None:
            wrote = safe_write.write_text(canon, text, expect_absent=True, times=times)
        else:
            wrote = safe_write.write_text(canon, text, expect=before, times=times)
        if not wrote:
            return out(CanonOutcome.REFUSED, state, _CANON_RACE)
        if not owner:
            return out(CanonOutcome.CREATED, after(), "создан без паспорта: стенограммы нет")
        attested = live_sidecar.attest(main, CANON_KIND, text, source_sha)
        return out(CanonOutcome.CREATED, after(), None if attested else _CANON_NO_PASSPORT)
    if state == live_sidecar.FRESH:
        return out(CanonOutcome.UNCHANGED, state)
    if state == live_sidecar.STALE:
        if not owner:
            # запись без нового паспорта оставила бы старый хеш, и следующий
            # проход назвал бы машинный текст HUMAN
            return out(CanonOutcome.UNCHANGED, state, "стенограммы нет — канон не обновлён")
        if not safe_write.write_text(canon, text, expect=before, times=times):
            return out(CanonOutcome.REFUSED, state, _CANON_RACE)
        attested = live_sidecar.attest(main, CANON_KIND, text, source_sha)
        return out(CanonOutcome.UPDATED, after(), None if attested else _CANON_NO_PASSPORT)
    if state == live_sidecar.HUMAN:
        # сюда же — отметки ночного контроля «снято по сроку»: иначе их стёр бы
        # следующий проход. Машинные правки в такие каноны — №391
        return out(CanonOutcome.KEPT, state, "канон правлен не раскладкой", _differing(current, text))
    if live_sidecar.valid_sha(meta.get(f"{CANON_KIND}_sha256")):
        return out(CanonOutcome.FAILED, state, _CANON_UNREAD)     # UNKNOWN при паспорте: оракул не прочитал
    # UNKNOWN без паспорта: отличие могло прийти от человека — признавать нельзя
    return out(CanonOutcome.KEPT, state, "паспорта нет, канон отличается от источника",
               _differing(current, text))


def summary_pass(folder: pathlib.Path, live: pathlib.Path, recording_note: str | None, *,
                 mode: SummaryMode = SummaryMode.AUTO) -> SummaryOutcome:
    """Один проход по саммари встречи: один снимок канона → решить оракулом →
    в режимах ADOPT/REBUILD присвоить легаси по канону → построить по политике
    режима → исход значением. Присвоение и сборка делят снимок канона и папку
    (Important DS круга 2: присвоение по папке другого резолвера и до обновления
    копий материалов давало паспорт на старый канон, и модель всё равно
    работала). HUMAN не строится ни одним режимом."""
    mode = SummaryMode(mode)      # единственная нормализация на границе: голая строка — ValueError здесь
    try:
        materials = summary_materials(folder)
        decided = decisions_of(folder)
    except MaterialUnreadable as e:
        # без сборки и без паспорта: хеш без материала — чужой канон (№366)
        return SummaryOutcome(SummaryOutcome.FAILED, None, f"материал не читается: {e.name}")
    if not materials:
        return SummaryOutcome(SummaryOutcome.NONE, None, "материалов нет")
    source_sha = summary_source_sha(materials, decided, recording_note)
    out = folder / "Саммари.md"
    meta = live_sidecar.read(live) or {}
    state = live_sidecar.derivative_state(out, meta, "summary", source_sha)
    reason = None
    if mode.adopts:
        # причина «почему не присвоено» — здесь, у единственного прохода, которому
        # сказали присваивать; отчёт читает её из исхода (Important DS круга 3)
        if state == live_sidecar.UNKNOWN:
            reason = summary_adoptable(folder, recording_note)
            if reason is None:
                reason = live_sidecar.adopt(live, "summary", out, source_sha)
                if reason is live_sidecar.ADOPT_OK:
                    return SummaryOutcome(SummaryOutcome.ADOPTED, live_sidecar.FRESH)
        elif state == live_sidecar.MISSING:
            reason = live_sidecar.missing_reason(out)     # причина — у оракула, не вторым чтением здесь
    if state == live_sidecar.HUMAN or not live_sidecar.wants_build(state, mode.policy):
        action = SummaryOutcome.KEPT if state in (live_sidecar.FRESH, live_sidecar.HUMAN) else SummaryOutcome.SKIPPED
        return SummaryOutcome(action, state, reason)
    return _build_summary(folder, live, materials, decided, source_sha, recording_note)


def adopt_summary(folder: pathlib.Path, live: pathlib.Path, recording_note: str | None) -> str:
    """Присвоить легаси-саммари без модели — исход словами (диагностика, тесты):
    «присвоено», «уже с паспортом (fresh)» или причина отказа из исхода."""
    o = summary_pass(folder, live, recording_note, mode=SummaryMode.ADOPT)
    if o.action == SummaryOutcome.ADOPTED:
        return "присвоено"
    return o.reason or f"уже с паспортом ({o.state})"


def summary_state(folder: pathlib.Path, live: pathlib.Path, recording_note: str | None) -> str | None:
    """Состояние саммари для читателя (диагностика, тесты): тот же канон и тот
    же оракул, что у писателя; None — материалов нет. Отчёт ретро-обхода этим
    НЕ пользуется — он печатает исход `summary_pass`, полученный возвратом
    (Critical DS круга 2). В манифест состояние не копируется (Important DS и
    GLM круга 1)."""
    try:
        materials = summary_materials(folder)
        decided = decisions_of(folder)
    except MaterialUnreadable:
        return live_sidecar.UNKNOWN       # материал не прочитался — знания нет (№366)
    if not materials:
        return None
    source_sha = summary_source_sha(materials, decided, recording_note)
    return live_sidecar.derivative_state(folder / "Саммари.md", live_sidecar.read(live) or {},
                                         "summary", source_sha)


def _gen_summary(folder: pathlib.Path, live: pathlib.Path, *,
                 mode: SummaryMode = SummaryMode.AUTO, recording_note: str | None = None) -> str | None:
    """Саммари.md — выжимка встречи на минуту чтения (первое, что открывают).

    Формат по практикам минуток: суть одной строкой → решили → поручения
    (кто/что/срок) → открытое. 100-300 слов, списки, без таблиц.

    С №314 саммари — производная с паспортом в сайдкаре стенограммы `live`
    (вид `summary`): источник — канон материалов (`summary_source_sha`),
    состояние — `live_sidecar.derivative_state`, строить ли — режим прохода;
    правленное руками (HUMAN) не трогается никогда. Замер 19.09: 74 из 298
    саммари боевого архива были старше своих минуток — ревизия (№238/№239)
    переписывала минутки, саммари собиралось один раз. Это обёртка над
    `summary_pass` (один проход, исход значением), возвращает состояние
    паспорта ПОСЛЕ прохода. Боевых вызывающих у обёртки нет — единственная
    точка прохода в проде `archive_meeting`; обёртка держится для тестов
    состояний (Minor DS круга 4). Писателя без стенограммы больше нет: файл без
    паспорта в модуле-владельце паспортов — обход шва по построению, и с
    политикой MISSING/STALE он не пересобрался бы уже никогда (Important DS
    круга 3); `force` снят — «пересобрать» выражается режимом."""
    return summary_pass(folder, live, recording_note, mode=mode).state


def _build_summary(folder: pathlib.Path, live: pathlib.Path, materials: list[tuple[str, str]],
                   decided: list[str], source_sha: str, recording_note: str | None) -> SummaryOutcome:
    """Собрать саммари моделью и записать через единственный шов
    `live_sidecar.write_derivative` (снимок в `.prev`, гейт expect, паспорт).
    Исход — значением: BUILT с состоянием после записи; FAILED — модель не
    ответила или упала (повтор имеет смысл); REFUSED — запись отклонена, файл
    менялся под рукой (повтор бессмыслен — критика GLM круга 3). При отказе
    состояние переспрашивается у оракула: прежнее недействительно (Minor GLM
    круга 2)."""
    out = folder / "Саммари.md"

    def failed(action: str, why: str) -> SummaryOutcome:
        state = live_sidecar.derivative_state(out, live_sidecar.read(live) or {}, "summary", source_sha)
        return SummaryOutcome(action, state, why)
    src_parts = [f"=== {name} ===\n{text}" for name, text in materials]
    history = _history_context(folder)
    words = SUMMARY_SECTIONS[_config_lang()]
    decided_block = (f"\n\n=== Решения встречи (перенеси их в раздел «{words['decisions']}», "
                     "сократив каждое до строки) ===\n"
                     + "\n".join(f"- {d}" for d in decided)) if decided else ""
    hist_block = (
        "\n\n=== История (Ядра и прошлые встречи) — ТОЛЬКО для раздела "
        f"«{words['history']}» ===\n" + history) if history else ""
    hist_tpl = (
        f"\n\n## {words['history']}\n"
        "(1-3 пункта «- **тема** — было: … (DD.MM) → сегодня: …» — ТОЛЬКО темы, "
        "которых сегодняшняя встреча реально касалась: продвижение, подтверждение "
        "или отмена прошлой договорённости. Нет пересечений — пропусти раздел)"
    ) if history else ""
    try:
        from llm import LLM
        # Модель и адрес — из конфига через llm.py, а не хардкодом: боевой
        # конфиг 12.08 переехал на mlx-сборку, а Саммари продолжало звать
        # старую модель мимо него (аудит 14.08). Лестница resolve_model
        # заодно даёт фолбэк, если основная модель не установлена.
        # Ленивый импорт: модуль обязан импортироваться без сторонних
        # пакетов — morning_brief живёт на голом python3 (круг-1 по #418,
        # GLM: единственный нетривиальный импорт у него — meeting_archive).
        from config_loader import load_user_or_example
        cfg = load_user_or_example(_root())
        client = LLM(cfg)
        text = client.complete(
            "<материалы>\n" + "\n\n".join(src_parts) + decided_block + hist_block
            + "\n</материалы>\n\n" + client.recording_block(recording_note)
            + f"Составь саммари {_lang_name()} по шаблону "
            "(заголовки — дословно как здесь):\n"
            f"**{words['gist']}** …\n\n"
            f"## {words['topics']}\n(до 3 пунктов «- **тема** — что по ней», не проза)\n\n"
            # «кто внедряет» тут стояло — и глушило весь раздел. У решений
            # в минутках исполнителя обычно нет («признаны неподходящими»,
            # «отказ от эскалации»), модель не находила его и писала
            # «решений не было» поверх трёх записанных решений. Замер 03.08
            # на четырёх встречах: без этого требования — 4 из 4 верно.
            f"## {words['decisions']}\n(список «- **тема решения** — суть одной "
            "строкой»; если в материалах нет ни одного решения — "
            f"«{words['none']}»)\n\n"
            f"## {words['tasks']}\n(список «- **Кто** — что — срок»)\n\n"
            f"## {words['questions']}\n(список; это последний раздел — следующие "
            "шаги уже перечислены в поручениях)" + hist_tpl,
            system=(
                # правила позитивные и данные в тегах — qwen следует такому лучше,
                # чем стопке «БЕЗ / НЕ / никогда» (замер 22.07 на минутках)
                "Ты делаешь выжимку рабочей встречи для быстрого чтения. Пишешь "
                f"{_lang_name()}, сухо, по фактам из материалов. Оформляешь списками "
                "«- …» с жирным ключом в начале пункта. Держишь весь текст в "
                "пределах 120 слов (900 знаков): максимум 3 пункта в разделе, "
                "пункт — одна строка до 12 слов, в пустом разделе одно слово «нет». "
                "Саммари читают за минуту — это выжимка; детали остаются в "
                "Минутках и Разборе."),
            think=False,
            # 560 токенов ≈ 1900 знаков: потолок НЕ должен резать (у русского в qwen
            # ~3.4 знака на токен, прежние 420 обрубали саммари на полуслове).
            # Объём держит промпт (120 слов), потолок — лишь страховка от простыни
            temperature=0.2, num_predict=560, num_ctx=8192,
            timeout=180,
        )
        if text:
            # Последнее слово за кодом: раздел решений слишком дорог, чтобы
            # зависеть от того, разглядела ли модель их в этот раз.
            text = _force_decisions(text, decided)
            text = _trim_summary(text)  # лимит гарантирует код, не промпт
            # оговорка о неполной записи — строкой в документ, как у минуток (№317)
            text = meeting_source.with_note(text, recording_note)
            date = folder.name[:10]
            # progressive disclosure: из выжимки видно, куда идти за деталями
            deeper = " · ".join(
                f"[[{ARCHIVE_DIR}/{folder.name}/{n}|{n}]]"
                for n in ("Минутки", "Разбор", "Стенограмма")
                if (folder / f"{n}.md").exists())
            body = (f"---\ntype: саммари\nдата: {date}\n---\n\n"
                    f"# Саммари — {folder.name}\n\n{text}\n"
                    + (f"\n---\nПодробнее: {deeper}\n" if deeper else ""))
            # единственный шов записи производных с паспортом (.prev, гейт expect);
            # состояние после записи — от шва (Important DS и GLM круга 1)
            wrote = live_sidecar.write_derivative(
                live, out, "summary", body, source_sha,
                log=lambda msg: print(f"саммари: {msg}", file=sys.stderr))
            if wrote.refused == live_sidecar.WriteOutcome.RACE:
                return failed(SummaryOutcome.REFUSED, "запись отклонена: файл менялся под рукой")
            if not wrote.written:
                # прежняя версия не сохранилась (диск, права на .prev) — файл не тронут,
                # повтор имеет смысл: это FAILED, не REFUSED (Important DS круга 4)
                return failed(SummaryOutcome.FAILED, "прежняя версия не сохранена — повторить позже")
            return SummaryOutcome(SummaryOutcome.BUILT, wrote.state)
        return failed(SummaryOutcome.FAILED, "модель не ответила")
    except Exception as e:  # noqa: BLE001
        print(f"саммари: {e}", file=sys.stderr)
        return failed(SummaryOutcome.FAILED, f"сбой сборки: {type(e).__name__}")


def _unhide(path: pathlib.Path):
    """iCloud-контейнер помечает элементы UF_HIDDEN — Finder показывал архив
    «пустым» (20.07). Снимаем флаг с архива и всего содержимого."""
    try:
        for p in (path, *path.rglob("*")):
            fl = p.stat().st_flags
            if fl & _stat.UF_HIDDEN:
                os.chflags(p, fl & ~_stat.UF_HIDDEN)
    except Exception:  # noqa: BLE001 — косметика, не валим архивацию
        pass


# строка живого контура: цитата, (время,) знак тезиса СРАЗУ — не любая цитата
# со знаком где-то внутри (цитата из прежней сводки в разговоре — не тезис;
# Minor DS круга 2 по №309)
_COTHINKING_LINE = re.compile(r"^> (?:\d{1,2}:\d{2} )?[📌💎💭🔬]")


def cothinking_notes(text: str) -> list[str]:
    """Строки живого ко-мышления в стенограмме: «> HH:MM 📌/💎/💭/🔬 …». Одно
    правило на архив (собирает из них Тезисы.md) и retro_fill (у такой встречи
    модель за тезисы не платит: живые тезисы старше ретро-сводки, №309)."""
    return [line[2:].strip() for line in text.splitlines() if _COTHINKING_LINE.match(line)]


def _derive_extras(folder: pathlib.Path, recording_note: str | None = None):
    """Производные файлы: Тезисы.md и Вопросы и ответы.md из уже скопированных."""
    tr = folder / "Стенограмма.md"
    if tr.exists():  # тезисы ко-мышления: строки «> HH:MM 📌/💎/💭/🔬 …»
        notes = cothinking_notes(tr.read_text(encoding="utf-8"))
        # существующие тезисы не переписываем: LLM-тезисы retro_fill с паспортом
        # затирались цитатами ко-мышления при каждом архивировании, и паспорт
        # переставал совпадать с диском (Critical DS входного круга по №309)
        if notes and not (folder / "Тезисы.md").exists():
            # строка о неполной записи — механически, как у остальных производных
            # (Important DS круга 2 по №317): из сайдкара оригинала, иначе из хвоста копии
            note = recording_note or meeting_source.note_in_tail(tr.read_text(encoding="utf-8"))
            safe_write.write_text(folder / "Тезисы.md", meeting_source.with_note(
                "# Тезисы встречи (📌 КТ · 💎 факты · 💭 мысли · 🔬 переоценка)\n\n"
                + "\n".join(f"- {n}" for n in notes) + "\n", note))

    qa: list[str] = []
    rb = folder / "Разбор.md"
    if rb.exists():  # аналитический раздел «вопрос → ответ/открыт» после встречи
        m = re.search(r"##\s*Вопросы встречи и ответы?\s*\n(.*?)(?=\n##\s|\Z)",
                      rb.read_text(encoding="utf-8"), re.S)
        if m and m.group(1).strip():
            qa += ["## Вопросы встречи и ответы (аналитика после встречи)", "",
                   m.group(1).strip(), ""]
    h = folder / "Подсказки и ответы.md"
    if h.exists():
        # построчный парс блоков «## [HH:MM] <тип>» (регекс-вариант молча давал 0);
        # эпизод вопроса: ❓/⚡ открывает, ☁️ прикрепляется; порядок в эпизоде
        # СТРОГО «вопрос → локальная модель (⚡) → Claude (☁️)» — облако можно
        # отключить, структура файла не изменится
        blocks: list[tuple[str, str, list[str]]] = []  # (тип, заголовок, строки)
        for line in h.read_text(encoding="utf-8").splitlines():
            mm = re.match(r"##\s*(\[\d{1,2}:\d{2}\])\s*(.*)", line)
            if mm:
                head = mm.group(2).strip()
                kind = ("q" if head.startswith("❓") else
                        "local" if head.startswith("⚡") else
                        "cloud" if "☁" in head else "hint")
                blocks.append((kind, f"{mm.group(1)} {head}", []))
            elif blocks:
                blocks[-1][2].append(line)
        episodes: list[dict] = []
        for kind, head, body in blocks:
            text = "\n".join(body).strip()
            if not text or kind == "hint":
                continue
            if kind in ("q", "local") or not episodes:
                episodes.append({})
            ep = episodes[-1]
            if kind in ep:  # тот же тип повторно — новый эпизод
                episodes.append({})
                ep = episodes[-1]
            ep[kind] = (head, text)
        if episodes:
            qa += ["## Ответы в темпе встречи", ""]
            for ep in episodes:
                for kind, label in (("q", "Вопрос"), ("local", "Локальная модель (⚡)"),
                                    ("cloud", "Claude (☁️)")):
                    if kind in ep:
                        head, text = ep[kind]
                        qa += [f"**{label}** {head}", "", text, ""]
                qa.append("---")
            if qa[-1] == "---":
                qa.pop()
    if qa and not (folder / "Вопросы и ответы.md").exists():   # тот же класс: не затирать готовое
        safe_write.write_text(folder / "Вопросы и ответы.md",
            "# Вопросы и ответы\n\n" + "\n".join(qa) + "\n")


def _rebuild_index(graph: pathlib.Path):
    adir = graph / ARCHIVE_DIR
    folders = sorted((p for p in adir.iterdir() if p.is_dir()), reverse=True)
    lines = ["# Архив встреч\n",
             "Папка = встреча: дата — о чём говорили. Внутри вся документация "
             "и ссылка на граф.\n"]
    for p in folders:
        names = sorted(f.stem for f in p.glob("*.md") if f.stem != "Граф")
        # Саммари — первое, что читают: ссылка ведёт на него и в списке оно первое
        target = "Саммари" if "Саммари" in names else "Стенограмма"
        if "Саммари" in names:
            names.remove("Саммари")
            names.insert(0, "Саммари")
        lines.append(f"- [[{ARCHIVE_DIR}/{p.name}/{target}|{p.name}]] — {', '.join(names)}")
    safe_write.write_text_if_changed(adir / "_ОГЛАВЛЕНИЕ.md", "\n".join(lines) + "\n")


def canon_tally_line(tally: typing.Mapping[str, int]) -> str:
    """Сводка действий канона минуток за массовый проход — одна строка на
    `migrate_all` и `retro_fill`: правленые каноны (`kept`) раскладка не
    трогает, и без сводки об этом узнавали бы по одной встрече (№366)."""
    counts = ", ".join(f"{k} {v}" for k, v in sorted(tally.items()))
    return f"канон минуток — {counts or 'раскладок не было'}"


def migrate_all(graph: pathlib.Path, tdir: pathlib.Path) -> int:
    """Разовая миграция истории: все стенограммы transcripts/ → папки архива."""
    done = 0
    canon_tally: collections.Counter = collections.Counter()
    # UF_HIDDEN снимаем один раз на обход, а не на встречу (№362); `finally` —
    # чтобы и упавший на k-й встрече обход снял флаг с уже разложенного
    try:
        for f in sorted(tdir.glob("*.md")):
            if any(f.name.endswith(suf) for suf, _ in NICE):
                continue  # это артефакт, не стенограмма
            if f.stat().st_size < 600:
                continue  # пустышка (тест старт/стоп) — не встреча
            bare = stamp_of(f.stem)
            if bare is None:
                continue
            # Ключ — как у graph_updater: минута у владельца, секунды у соседки;
            # минутный регэксп пропускал посекундные стенограммы целиком.
            stamp = graph_key(tdir, f.stem, graph)
            slug = f.stem[len(bare) + 1:] if f.stem != bare else ""
            archived = archive_meeting(graph, tdir, stamp, slug, files_key=f.stem, unhide=False)
            if archived is not None and archived.canon is not None:
                canon_tally[archived.canon.action] += 1
            done += 1
    finally:
        _unhide(graph)
    print(f"архив: {canon_tally_line(canon_tally)}")
    return done


if __name__ == "__main__":
    # корень данных называет тот, кто запускает: приложение и демон передают
    # CHAROITE_ROOT, ручной запуск без него получает рецепт и код 5 (№340)
    from charoite_paths import name_data_root_or_exit
    name_data_root_or_exit(__file__)
    import yaml
    cfg = yaml.safe_load((_root() / "config" / "config.yaml").read_text(encoding="utf-8"))
    graph = graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")
    tdir = _root() / cfg["log"]["transcripts_dir"]
    if "--all" in sys.argv:
        n = migrate_all(graph, tdir)
        print(f"архив: {n} встреч в {graph / ARCHIVE_DIR}")
    else:
        print("использование: meeting_archive.py --all (миграция истории)")
