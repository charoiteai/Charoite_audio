#!/usr/bin/env python3
"""Раскладка кода: слои, рёбра импортов, точки входа, названные пути — один
машинный артефакт и один гейт.

Фаза 0 разбиения `src/` на пакеты (№320; архитектурный круг 19.09, DS и GLM):
границы слоёв держались памятью автора и разовым замером. Здесь —
единственный источник истины `docs/design/layout.json` (решения: таблица слоёв
брифа с поправками и обоснованием, стрелки, allowlist рёбер с карточками,
ручные точки входа с обоснованием) и генератор карты `docs/design/layout.md`.
Гейт — `tests/test_import_boundaries.py`: сверяет артефакт с замером как
равенство множеств В ОБЕ СТОРОНЫ по каждой сущности (круг 2 по #594: любое
одностороннее включение либо держит лишнее, либо молчит о потерянном).

Сущности и их замер:
- слои: каждый модуль `src/` отнесён, лишних имён нет; рёбра импортов — по AST,
  включая ленивые внутри функций;
- рёбра против стрелок: замер = allowlist, в обе стороны;
- точки входа = исполняемые файлы репозитория: кандидаты по расположению
  (`src/*.py`, `scripts/*`, `app/*.sh`, `*.sh` в корне), из них python — только
  с настоящим гвардом `__main__` по AST, shell — по факту;
  каждая либо названа кодом, либо объявлена ручной с обоснованием; названные
  кодом библиотеки точками входа НЕ являются (Critical DS и GLM круга 2);
- названные пути: всё, что код (Swift, shell, yml, python-литералы без
  докстрингов) и проза (документация, конфиги, toml) называют как путь к
  исполняемому файлу, обязано существовать — подсказка человеку и инструкция
  в README не должны врать после переезда; голое имя `.sh` — путь, если в
  репозитории ровно один скрипт с таким именем; несколько — проблема гейта
  («назвать полным путём»); ни одного — справка на карте (чужой или
  порождаемый скрипт).

Замер строится из ОДНОГО инвентаря (`inventory`): один обход файлов под git,
одна таблица «что это за файл» (`KINDS`), одно чтение и один разбор на файл.
Провал разбора — факт-проблема в инвентаре, никогда не исключение и никогда
не молчаливый пропуск (круг 3 по #594: тот же `SyntaxError` в трёх местах
обрабатывался тремя способами — крах, пропуск, строка). Всё, что сканер
отклонил (неоднозначное голое имя), тоже становится проблемой: сторож обязан
сторожить и собственный сборщик показаний (Как чинить GLM круга 3).

Не import-linter: ему нужен импортируемый пакет, а плоский `src/` из модулей,
импортирующих друг друга короткими именами, пакетом не является; механизм
этого класса в проекте уже есть — AST-сторожа тестом.

Запуск:
    .venv/bin/python scripts/layout_map.py            # карта в docs/design/layout.md
    .venv/bin/python scripts/layout_map.py --check    # то же, что тест, кодом выхода
    .venv/bin/python scripts/layout_map.py --regen    # allowlist по факту + карта
    .venv/bin/python scripts/layout_map.py --report   # замер швов (только код, артефакт не нужен)
"""
from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Callable, Literal, NamedTuple

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
LAYOUT = REPO / "docs" / "design" / "layout.json"
MAP = REPO / "docs" / "design" / "layout.md"

#: Каталоги раскладки — ОДНО объявление имён, из которого читают и разбор пути
#: (`form`), и шаблоны кандидатов ниже, и тест-сторож второго мнения. Пока имена
#: стояли литералами по месту, охрану приходилось ставить на НАПИСАНИЕ предиката,
#: и она молчала на идиоме, которой написан сам модуль: глоб `"src/*.py"` в
#: множество написаний не попадал (круг 3 по коду №328, GLM C1, DS I7; обе головы
#: сошлись на одном механизме — словарь каталогов в одном объявлении).
FLAT_DIR = "src"            # плоская раскладка: `src/<модуль>.py`
DIST_DIR = "packages"       # дистрибутивы: `packages/<дист>/src/<пакет>/…`
TESTS_DIR = "tests"         # тесты пакета: `packages/<дист>/tests/…`
LAYOUT_DIRS = (FLAT_DIR, DIST_DIR, TESTS_DIR)

#: Кандидаты в точки входа по расположению; исполняемым кандидата делает гвард
#: `__main__` (python) или сам факт shell-скрипта (Minor DS круга 5: «цель» читалась
#: как «исполняемый», а `src/graph_search.py` — кандидат, но не исполняемый).
#: Пакетную форму сюда глобом не записать — модуль лежит на любой глубине, — и её
#: кандидатность решает форма (`_is_candidate`, круг 3 по коду №328, GLM I2).
ENTRY_CANDIDATES = (f"{FLAT_DIR}/*.py", "scripts/*.py", "scripts/*.sh", "app/*.sh", "*.sh")

#: Классификация файла — одна таблица и один решатель `decide()`; `kind_of`,
#: отсечение каталогов при обходе без git и раздел карты читают его решение, а не
#: сопоставляют префиксы сами (Critical DS круга 5: корпусная сверка держала свою
#: копию сопоставления и не видела правило-тень). Кандидат в точки входа
#: (ENTRY_CANDIDATES) — всегда `code`; правило таблицы, желающее кандидату
#: другого вида, — конфликт и красный гейт, не тихий приоритет (Critical DS
#: круга 4: корневой `x.sh` был целью по одной таблице и `out` по другой).
#: Дальше первый совпавший префикс (каталог с «/» или точное имя). Виды: `code`
#: — упоминание пути = связь «кто зовёт»; `prose` — названный путь обязан
#: существовать; `history` — датированные снимки (ревью, релизные заметки, посты)
#: описывают код своего дня и после переезда не правятся; `out` — не читается.
#: Внутри `code` решает суффикс: код читается без комментариев, документация
#: рядом с кодом — как проза, остальное — `out`. Без совпадения: проза по
#: суффиксу, иначе `out` (Important DS круга 3). Область: `git` — удаление
#: правила меняет вид хотя бы одного файла под git (иначе оно память автора —
#: гейт); `walk` — правило накрывает ноль файлов под git (каталог существует
#: только на диске: сборки, окружения). Живость меряется пробой своего вида
#: (`probe(prefix, kind)`): удаление правила обязано изменить вид пробы, иначе
#: правило ничего не решает (Important DS круга 7: у правил области `walk`
#: живость не мерялась ничем, опечатка в префиксе проходила зелёной).
#: Сама таблица — утверждённые данные: её копия с порядком лежит в гейте
#: (Critical DS круга 6: два правила выпали при переписывании, суффиксный
#: фолбэк дал правдоподобный вид, и ни одна проверка не заметила).
KINDS: tuple[tuple[str, str, str, str], ...] = (
    ("docs/design/layout.md", "out", "git", "карта — производная замера, не источник"),
    ("tests/", "out", "git", "тесты строят синтетические деревья: пути в них — не факты о репозитории"),
    ("app/Tests/", "out", "git", "Swift-тесты приложения: те же выдуманные пути"),
    ("app-ios/", "out", "git", "телефон python и shell не запускает — пути там только в тексте"),
    ("app-android/", "out", "git", "телефон python и shell не запускает — пути там только в тексте"),
    ("docs/reviews/", "history", "git", "датированные ревью описывают код своего дня — после переезда не правятся"),
    ("devlog/_posts/", "history", "git", "датированные посты — снимок своего дня"),
    ("CHANGELOG.md", "history", "git", "релизные заметки — снимок своего дня, записи о вышедших версиях не правятся"),
    ("app/build/", "out", "walk", "сборка"),
    ("app/.build/", "out", "walk", "сборка"),
    (".build/", "out", "walk", "сборка"),
    ("build/", "out", "walk", "сборка"),
    (".venv/", "out", "walk", "окружение"),
    ("node_modules/", "out", "walk", "чужой код"),
    (".git/", "out", "walk", "служебный каталог git"),
    ("app/", "code", "git", "приложение зовёт python и shell"),
    ("scripts/", "code", "git", "скрипты зовут друг друга и модули; проза по суффиксу (README)"),
    ("src/", "code", "git", "модули зовут скрипты и подсказывают пути человеку"),
    ("packages/", "code", "git", "дистрибутивы: модуль пакета — тот же продукт, что модуль src/"),
    (".github/", "code", "git", "workflow CI — источник запуска"),
    (".pre-commit-config.yaml", "code", "git", "хуки — источник запуска"),
)
CODE_SUFFIXES = (".swift", ".sh", ".py", ".yml", ".yaml", ".plist")
PROSE_SUFFIXES = (".md", ".toml", ".in", ".txt", ".yml", ".yaml", ".cfg", ".ini")
# путь к исполняемому файлу: с каталогом (src/x.py, scripts/x.sh, app/x.sh) — или голое имя
# скрипта .sh (./make_app.sh, make_app.sh: так его зовёт CI с working-directory), резолв по имени;
# голые имена .py не считаются — «audio.py» в прозе означает модуль, а не путь запуска.
# Хвост: после имени не идёт ни символ идентификатора, ни точка с идентификатором (x.py.bak —
# не путь), а точка конца предложения («см. src/nli.py.») путь не прячет (Important GLM круга 3).
# Пакетная форма — своя альтернатива: `packages/<дист>/…/имя.py` на любой глубине.
# Без неё упаковка ломала инвариант точек входа с другого конца: файл становился
# кандидатом, а НАЗВАТЬ его в коде или в прозе было нечем — токен не собирался
# никогда, и «никто не зовёт» нельзя было погасить ни правкой кода, ни правкой
# документации (круг 4 по коду №328, GLM I1).
#: Из чего состоит сегмент пути: имя дистрибутива — это имя пакета на PyPI, а
#: там законны точка и дефис (`my-dist`, `cg.v2`). Без этого такой дистрибутив
#: неименуем: кандидат в точки входа есть, а погасить «никто не зовёт» нечем —
#: тот же тупик, что закрывали кругом раньше (круг 5 по коду №328, GLM I2).
DIST_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]*"
MODULE_SEGMENT = r"[A-Za-z_][A-Za-z0-9_]*"

_PATH = re.compile(rf"(?<![A-Za-z0-9_.-])(?:{DIST_DIR}/{DIST_SEGMENT}"
                   rf"(?:/{MODULE_SEGMENT})*/{MODULE_SEGMENT}\.(?:py|sh)"
                   rf"|(?:{FLAT_DIR}|scripts|app)/{MODULE_SEGMENT}\.(?:py|sh)"
                   rf"|(?:\./)?{MODULE_SEGMENT}\.sh)(?![A-Za-z0-9_]|\.[A-Za-z0-9_])")
#: Префиксы токенов-путей: те же каталоги, что в `_PATH`. Грамматика здесь
#: другая — это разбор ТЕКСТА, а не формы раскладки, — но имена каталогов те же
#: и берутся из того же объявления, а не переписываются рядом.
TOKEN_PREFIXES = (f"{FLAT_DIR}/", f"{DIST_DIR}/", "scripts/", "app/")


class LayoutError(ValueError):
    """Артефакт раскладки невалиден: правится руками, инвариант — при загрузке."""


class FileInfo(NamedTuple):
    """Один файл инвентаря: вид, что читает токенизатор, дерево python, исполняемость."""
    kind: str                       # code | prose | history | out
    haystacks: tuple[str, ...]      # литералы python / текст без комментариев / сырая проза
    tree: ast.Module | None         # только у разобранного .py вида code
    executable: str | None          # почему исполняемый, иначе None


class ProblemKind(NamedTuple):
    section: str            # заголовок раздела замера; одинаковый заголовок — один раздел
    invalidates: bool       # вид делает замер НЕДОСТОВЕРНЫМ: раздел печатается даже пустым
                            # и идёт раньше прочих, а `--report` краснеет. Не «неполным»:
                            # при коллизии корпус полон, но граф соврал бы — имя одно на
                            # двоих (круг 3 по коду №328, DS I4)


#: Виды проблемы инвентаря — ОДНО объявление, из которого читают и разделы
#: замера, и код выхода. Раньше списки видов жили литералами в `report()` и в
#: `main()`, и новый вид `collision` добавили, забыв обоих потребителей: гейт
#: коллизию видел, а замер о ней молчал и возвращал 0 (круг 2 по коду №328,
#: DS I3). Вид, не названный здесь, создать нельзя — проверка в `Problem`.
PROBLEM_KINDS: dict[str, ProblemKind] = {
    "read": ProblemKind("Не прочитано — этих файлов в замере нет", True),
    "parse": ProblemKind("Не прочитано — этих файлов в замере нет", True),
    "conflict": ProblemKind("Спорный вид — файлы в замере есть, но политика конфликтует", False),
    "collision": ProblemKind("Конфликт упаковки — одно имя на двоих, граф схлопнул бы их в один узел", True),
}


@dataclasses.dataclass(frozen=True)
class Problem:
    """Проблема инвентаря как значение, а не голая строка: потребители должны
    отличать «корпус неполон» от «политика конфликтует» (Critical GLM круга 9 —
    одна и та же строка печаталась как «не вошло в замер» у файла, который в
    замер вошёл). Что значит каждый вид — в `PROBLEM_KINDS`, и вид, которого
    там нет, создать нельзя: иначе новый вид снова заведут, забыв потребителя
    (круг 2 по коду №328, DS I3). Не `NamedTuple` ровно поэтому — там проверку
    на входе поставить некуда."""
    kind: str
    text: str

    def __post_init__(self) -> None:
        if self.kind not in PROBLEM_KINDS:
            raise LayoutError(f"неизвестный вид проблемы {self.kind!r} — объявить в PROBLEM_KINDS "
                              f"вместе с разделом замера и влиянием на полноту корпуса")

    def __str__(self) -> str:
        return self.text


def sections(problems: list[Problem]) -> list[tuple[str, list[Problem]]]:
    """Разделы замера по `PROBLEM_KINDS`, в порядке объявления и БЕЗ пропусков.

    Обход таблицы целиком — то самое место, где вид перестаёт теряться: новый
    вид получает раздел от объявления, а не от того, вспомнил ли автор про
    `report()` (круг 2 по коду №328, DS I3)."""
    out: list[tuple[str, list[Problem]]] = []
    для_раздела: dict[str, list[Problem]] = {}
    # Сначала виды, после которых замеру верить нельзя: человек обязан узнать
    # это РАНЬШЕ, чем прочтёт цифры. Пока порядок держался порядком ключей
    # словаря, первый же новый вид вставал впереди «Не прочитано (0)» и
    # отодвигал предупреждение вниз (круг 3 по коду №328, DS I3).
    for сначала in (True, False):
        for kind, вид in PROBLEM_KINDS.items():
            if вид.invalidates is not сначала:
                continue
            свои = [p for p in problems if p.kind == kind]
            if вид.section in для_раздела:
                для_раздела[вид.section] += свои
                continue
            if свои or вид.invalidates:      # «замеру верить можно» обязано звучать и когда можно
                для_раздела[вид.section] = свои
                out.append((вид.section, для_раздела[вид.section]))
    return out


class Inventory(NamedTuple):
    files: dict[str, FileInfo]
    problems: list[Problem]         # файл не читается / не разбирается / спорный вид / одно имя
                                    # у двух файлов — факт, не исключение. `collision` отличается
                                    # от прочих: замер ПОЛОН, но граф соврал бы — два файла
                                    # схлопнулись бы в один узел со своим слоем на двоих


class Scan(NamedTuple):
    """Замер: связи «кто зовёт» из кода, названные пути из прозы, голые имена
    без цели (справка на карте: чужой или порождаемый скрипт — не гейт),
    проблемы инвентаря и сканера (строками: гейту класс не важен)."""
    mentions: dict[str, set[str]]       # путь → файлы кода, которые его называют
    prose: dict[str, set[str]]          # путь → документы/конфиги, которые его называют
    loose: dict[str, set[str]]          # голое имя без цели в репозитории → кто его называет
    problems: list[str]


#: Состояния карты — один источник для сигнатуры, проверки и гейта (Important DS
#: круга 11: подсказка типа и проверка были двумя независимыми списками).
MAP_STATES = ("present", "missing", "skipped")
MapState = Literal["present", "missing", "skipped"]      # тот же кортеж, гейт сверяет их равенство

_SCHEMA = {"order": list, "brief_layers": dict, "allowed": dict, "layer_overrides": dict,
           "allowed_edges": list, "manual_entry_points": dict, "root_exemptions": dict,
           "generated": str}

#: Поля записи ребра, которыми владеет ЗАМЕР: их пишет `regen` по факту обхода
#: импортов. Всё остальное в записи — решение человека (карточка, и что добавят
#: дальше), и `regen` обязан перенести его дословно. Граница нужна именно как
#: список: пока её не было, запись собиралась из трёх полей заново, и любое
#: четвёртое исчезало без следа (входной круг №325).
MEASURED_EDGE_FIELDS = ("from", "to")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")


def load_layout(path: pathlib.Path | None = None) -> dict:
    """Строгая загрузка: любой дефект артефакта — `LayoutError`, единственный
    тип, который ловит `main`. Сначала паспорт схемы (файл читается, ключи на
    месте и нужного типа — Critical DS круга 3: битый JSON и пропавший ключ
    падали трейсбеком), потом инварианты: слои попарно не пересекаются,
    `order`/`allowed` согласованы, стрелки вниз, у поправок слоя, ручных точек
    входа и рёбер allowlist есть обоснование или карточка."""
    path = path or LAYOUT
    try:
        layout = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LayoutError(f"не читается: {e}") from e
    if not isinstance(layout, dict):
        raise LayoutError("верхний уровень — не объект")
    for key, typ in _SCHEMA.items():
        if key not in layout:
            raise LayoutError(f"нет ключа {key}")
        if not isinstance(layout[key], typ):
            raise LayoutError(f"{key}: ожидался {typ.__name__}")
    if not _STAMP.match(layout["generated"]):
        raise LayoutError("generated: ожидался штамп вида 2026-09-19T18:53Z")
    order = layout["order"]
    if len(set(order)) != len(order) or not all(isinstance(x, str) for x in order):
        raise LayoutError("order: повтор слоя или не строка")
    if set(order) != set(layout["brief_layers"]) or set(order) != set(layout["allowed"]):
        raise LayoutError("order, brief_layers и allowed называют разные слои")
    seen: dict[str, str] = {}
    for layer, mods in layout["brief_layers"].items():
        if not isinstance(mods, list) or not all(isinstance(m, str) for m in mods):
            raise LayoutError(f"brief_layers[{layer}]: ожидался список имён модулей")
        for m in mods:
            if m in seen:
                raise LayoutError(f"модуль {m} в двух слоях: {seen[m]} и {layer}")
            seen[m] = layer
    for m, ov in layout["layer_overrides"].items():
        if not isinstance(ov, dict) or ov.get("layer") not in order or not ov.get("why"):
            raise LayoutError(f"поправка слоя {m}: нужен layer из order и непустое why")
    for layer, deps in layout["allowed"].items():
        if not isinstance(deps, list):
            raise LayoutError(f"allowed[{layer}]: ожидался список слоёв")
        for d in deps:
            if d not in order:
                raise LayoutError(f"{layer}: в allowed неизвестный слой {d!r}")
            if order.index(d) >= order.index(layer):
                raise LayoutError(f"{layer} зависит не вниз: {deps}")
    for e in layout["allowed_edges"]:
        if not isinstance(e, dict) or not isinstance(e.get("from"), str) or not isinstance(e.get("to"), str):
            raise LayoutError(f"ребро allowlist без from/to: {e!r}")
        if not e.get("ticket"):
            raise LayoutError(f"ребро {e['from']} → {e['to']} без карточки")
    known = {name for name, _, _ in ROOT_SHAPES}
    for path_, shapes in layout["root_exemptions"].items():
        if not isinstance(shapes, dict) or not shapes:
            raise LayoutError(f"исключение из правила корня {path_}: нужна карта «форма → обоснование»")
        for name, why in shapes.items():
            if name not in known:
                raise LayoutError(f"исключение {path_}: форма {name!r} не из ROOT_SHAPES")
            if not isinstance(why, str) or not why:
                raise LayoutError(f"исключение {path_} по форме {name}: нужно непустое обоснование")
    for path_, why in layout["manual_entry_points"].items():
        if not _is_candidate(path_) or not isinstance(why, str) or not why:
            raise LayoutError(f"ручная точка входа {path_}: не путь к исполняемому файлу или пустое why")
    return layout


def _is_candidate(rel: str) -> bool:
    """Расположение позволяет файлу быть точкой входа. Плоскую форму задают
    шаблоны, пакетную — форма раскладки: модуль дистрибутива лежит на любой
    глубине, и глобом это не выразить. Без второй половины первый же `git mv`
    в `packages/` выносил точку входа из-под охраны молча, а объявить её ручной
    было нельзя вовсе — `load_layout` отказывал (круг 3 по коду №328, GLM I2).
    """
    f = form(rel)
    if f.dist and f.role in ("module", "package_init"):
        return True
    return any(pathlib.PurePosixPath(rel).match(p) and rel.count("/") == p.count("/") for p in ENTRY_CANDIDATES)


# ---------------------------------------------------------------- инвентарь

class Decision(NamedTuple):
    kind: str               # code | prose | history | out
    by: str                 # см. DECIDED_BY
    rule: int | None        # индекс правила KINDS, накрывающего путь (и у кандидата тоже)
    conflict: str | None    # правило таблицы, желающее кандидату не-code — красный гейт


#: Кто принял решение о виде файла. `shape` — форма раскладки: правилом `KINDS`
#: её не выразить (дистрибутив стоит в середине пути), и выдавать её ответ за
#: правило нельзя: на `by` ветвятся и замер («вне области по правилу»), и гейт
#: («дыра в таблице»), а `rule` при этом указывал бы на правило, решившее
#: ПРОТИВОПОЛОЖНОЕ (круг 3 по коду №328, DS I2, GLM M1).
DECIDED_BY = ("candidate", "rule", "shape", "default")


def _rule(rel: str) -> int | None:
    """Индекс первого правила KINDS, чей префикс (каталог с «/» или точное имя)
    накрывает путь — единственное сопоставление префиксов в модуле."""
    for i, (prefix, _kind, _scope, _why) in enumerate(KINDS):
        if rel == prefix or (prefix.endswith("/") and rel.startswith(prefix)):
            return i
    return None


def _by_suffix(base: str, rel: str) -> str:
    if base == "code" and rel.endswith(CODE_SUFFIXES):
        return "code"
    return "prose" if rel.endswith(PROSE_SUFFIXES) else "out"


def decide(rel: str) -> Decision:
    """Единственный решатель вида файла: кандидат в точки входа — код, иначе
    первое правило таблицы, иначе суффикс. Кто решил — часть ответа, чтобы
    сверка таблиц над корпусом читала решение, а не переписывала политику."""
    i = _rule(rel)
    if form(rel).role == "package_tests":
        # Форма раскладки отвечает ЗДЕСЬ, значением, которое читают все:
        # пока про тесты пакета знал один потребитель (`scan`), `report`
        # считал их модулями области и печатал их пути в замере, а гейт о них
        # молчал — два ответа на один вопрос (круг 2 по коду №328, DS I2).
        return Decision("out", "shape", i, None)
    if _is_candidate(rel):
        conflict = KINDS[i][0] if i is not None and KINDS[i][1] != "code" else None
        return Decision("code", "candidate", i, conflict)
    if i is None:
        return Decision(_by_suffix("", rel), "default", None, None)
    kind = KINDS[i][1]
    return Decision(kind if kind in ("out", "history") else _by_suffix(kind, rel), "rule", i, None)


def kind_of(rel: str) -> str:
    return decide(rel).kind


class Form(NamedTuple):
    """Что файл представляет собой по МЕСТУ в дереве — до всякой политики.

    Разбор пути на сегменты живёт здесь и больше нигде: до этого форму
    выводили четыре места четырьмя способами (`module_of` — `части[0] ==
    "src"`, `in_package_tests` — свой `split`, `package_of` — суффикс
    `/__init__.py`, `inventory` — имя дистрибутива своим срезом), и каждый
    потребитель сам выбирал, у кого спрашивать. Отсюда и узел-призрак
    `__init__`, и тесты пакета, посчитанные в замере как продукт: это не
    четыре дефекта, а одно состояние — у формы нет хозяина, есть соавторы
    (круг 2 по коду №328, DS «Как чинить»).
    """
    role: str               # см. ROLES
    dist: str               # дистрибутив под `packages/`, иначе ""
    package: str            # пакет, В КОТОРОМ лежит файл ("" — плоская раскладка)
    module: str | None      # импортируемое имя, если файл — модуль продукта


#: Роли формы. `stray_init` — `__init__.py` там, где пакета вокруг него нет
#: (`src/__init__.py`, `packages/<дист>/src/__init__.py`): в плоской раскладке
#: `src/` — каталог, а не пакет, и имя `__init__` модулем не бывает. Решение
#: принято здесь явно и один раз, а не догадкой ветки (круг 1 и 2 по коду
#: №328, обе головы независимо, каждая про свою ветку).
ROLES = ("module", "package_init", "package_tests", "stray_init", "outside")

#: Единственная функция, которой позволено называть каталоги раскладки. Не
#: список в тесте, а объявление здесь: тест-сторож читает его, и расширение
#: круга владельцев становится видимой правкой продукта, а не строкой в тесте
#: (круг 2 по коду №328, DS M8).
SHAPE_OWNER = "form"


def form(rel: str) -> Form:
    """Путь → форма. ЕДИНСТВЕННОЕ место, которое знает раскладку каталогов.

    Раньше знание было записано литералом `startswith("src/") and
    rel.count("/") == 1` дважды — в `modules()` и в `import_graph()`, — то
    есть два независимых вывода об одном и том же. Первое изменение формы
    (переезд в `packages/`) делает их несогласованными молча: `modules()`
    перестаёт знать модуль, `import_graph` перестаёт давать рёбра, а гейт
    остаётся зелёным (входной круг №328, обе головы независимо).

    Плоская форма — `src/x.py` → `x`; пакетная — `packages/<дистрибутив>/src/
    <пакет>/y.py` → `<пакет>.y`. Имя пакета, а не стем файла: после упаковки
    тот же файл импортируется как `charoite_graph.graphs`, и таблица слоёв
    обязана ключеваться тем, что пишет автор в `import`.
    """
    части = rel.split("/")
    # дистрибутив считается ОДИН раз на входе: пока каждая ветка отвечала за
    # него сама, `packages/<д>/tests/x.md` знал своего владельца, а
    # `packages/<д>/pyproject.toml` — нет (круг 3 по коду №328, DS I5, GLM M2)
    # дистрибутив — КАТАЛОГ: `packages/README.md` своим дистрибутивом не бывает
    # (круг 4 по коду №328, GLM M1 — файл лежит в дереве прямо сейчас)
    дист = части[1] if части[0] == DIST_DIR and len(части) > 2 else ""
    if дист and len(части) > 3 and части[2] == TESTS_DIR:
        # Тесты пакета — та же политика, что корневой `tests/`: пути в них
        # выдуманные, упоминания оттуда связями не считаются. Правилом `KINDS`
        # её не выразить — дистрибутив стоит в СЕРЕДИНЕ пути, а правила
        # сопоставляются префиксом (круг 1 по коду №328, DS I1).
        return Form("package_tests", дист, "", None)
    if not rel.endswith(".py"):
        return Form("outside", дист, "", None)
    хвост = части[:-1] + [части[-1][:-3]]
    if хвост[0] == FLAT_DIR and len(хвост) == 2:
        if хвост[1] == "__init__":
            return Form("stray_init", дист, "", None)
        return Form("module", дист, "", хвост[1])
    if дист and len(хвост) >= 4 and хвост[2] == FLAT_DIR:
        имена = хвост[3:]
        if имена[-1] == "__init__":
            имена = имена[:-1]
            if not имена:
                return Form("stray_init", дист, "", None)
            # `src/<пакет>/__init__.py` — файл САМОГО пакета, и относительные
            # имена в нём считаются от него, а не от родителя: `from . import
            # names` в `p/sub/__init__.py` иначе даёт `p.names` — ложное ребро
            # на чужой модуль (круг 1 по коду №328, GLM C1).
            return Form("package_init", дист, ".".join(имена), ".".join(имена))
        return Form("module", дист, ".".join(имена[:-1]), ".".join(имена))
    return Form("outside", дист, "", None)


def module_of(rel: str) -> str | None:
    """Путь → ИМПОРТИРУЕМОЕ ИМЯ модуля продукта; не модуль — `None`.

    Проекция `form` плюс политика: файл, которому правило `KINDS` дало не
    `code`, модулем продукта не считается, как бы ни лежал.
    """
    if decide(rel).kind != "code":
        return None
    return form(rel).module


def package_of(rel: str) -> str:
    """Пакет, В КОТОРОМ лежит файл: для `__init__.py` — он сам, иначе родитель.

    Проекция `form`: считается от ПУТИ, а не от имени модуля. `module_of`
    нормализует `p/__init__.py` в `p`, и по одному имени `p` уже не отличить
    «пакет `p`» от «модуль `p` внутри чего-то» (круг 1 по коду №328, GLM C1).
    """
    return form(rel).package


def imports_of(rel: str, tree: ast.Module) -> set[str]:
    """Дерево модуля → имена, которые он импортирует, КАК ИХ НАПИСАЛ АВТОР.

    Единственное место, которое знает, как читать импорт. Две формы раньше
    терялись молча, и обе — основные после упаковки:

    * точечное имя обрезалось до первого сегмента (`alias.name.split(".")[0]`),
      поэтому `from charoite_graph import graphs` в точке входа не давало
      ребра вовсе: `charoite_graph` в списке модулей нет, а `graphs` не
      искали;
    * относительные импорты не рассматривались (`node.level == 0`), а внутри
      пакета `from . import graph_names` — обычный стиль.

    Резолв «имя → модуль продукта» здесь НЕ делается: это знание графа, и
    живёт оно в `import_graph`. Здесь — только грамматика импорта.
    """
    пакет = package_of(rel)
    имена: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            имена.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                корень = пакет.split(".") if пакет else []
                подъём = node.level - 1
                if подъём >= len(корень):
                    continue                    # выше корня пакета — имя не наше
                база = ".".join(корень[: len(корень) - подъём])
                if node.module:
                    база = f"{база}.{node.module}" if база else node.module
            else:
                база = node.module or ""
            if база:
                имена.add(база)
            имена.update(f"{база}.{a.name}" if база else a.name for a in node.names)
    return имена


def _module_by_name(имя: str, mods: set[str]) -> str | None:
    """Импортируемое имя → модуль продукта: самый длинный известный префикс.

    `charoite_graph.graphs` — сам модуль; `charoite_graph.graphs.load` —
    он же плюс имя внутри него; `yaml.safe_load` — чужое, `None`.
    """
    части = имя.split(".")
    while части:
        кандидат = ".".join(части)
        if кандидат in mods:
            return кандидат
        части.pop()
    return None


def candidate_dirs() -> frozenset[str]:
    """Каталоги, в которых лежат кандидаты в точки входа — одна точка вывода из
    `ENTRY_CANDIDATES` (Minor DS и GLM круга 7: текстовая замена точки вырезала
    её из имени каталога, и защита терялась молча)."""
    out = set()
    for pat in ENTRY_CANDIDATES:
        parent = str(pathlib.PurePosixPath(pat).parent)
        out.add("" if parent == "." else parent)
    return frozenset(out)


#: Вид → суффикс пробы, который БЕЗ правила даёт другой вид (знание о различимости
#: живёт здесь и больше нигде; вид без такого суффикса — ошибка, а не «проба есть»).
PROBE_SUFFIX = {"code": "probe.swift", "out": "probe.md", "history": "probe.md", "prose": "probe.dat"}


def probe(prefix: str, kind: str) -> str:
    """Путь-проба правила: файл, на котором видно, что правило решает. Для
    правила-каталога — файл внутри с суффиксом, который без правила дал бы другой
    вид; для правила-имени — сам путь (Important GLM круга 8: для вида `prose`
    проба `.md` была неотличима от фолбэка)."""
    if not prefix.endswith("/"):
        return prefix
    if kind not in PROBE_SUFFIX:
        raise LayoutError(f"вид {kind!r} нечем доказать: нет суффикса пробы в PROBE_SUFFIX")
    return prefix + PROBE_SUFFIX[kind]


def _pruned(rel_dir: str) -> bool:
    """Обход без git: каталог, который таблица целиком относит к `out`, не
    открывается вовсе — но только если в нём и ниже не может быть кандидата:
    отсечение строго слабее `decide`, иначе правило-тень над `scripts/` спрятало
    бы конфликт от гейта в обходе без git (Important DS круга 6)."""
    if any(rel_dir == d or d.startswith(rel_dir + "/") for d in candidate_dirs()):
        return False
    i = _rule(rel_dir + "/")
    return i is not None and KINDS[i][1] == "out"


def _files(repo: pathlib.Path) -> list[str]:
    """Файлы под git (`git ls-files`): данные владельца (бэкапы графа,
    стенограммы, боевой конфиг) не в репозитории и не читаются; скрытые
    каталоги не отсекаются (Important GLM круга 2). Без git (синтетическое
    дерево в тестах) — обход диска с той же таблицей исключений."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True)
        files = [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(repo):
            rel_dir = os.path.relpath(dirpath, repo)
            rel_dir = "" if rel_dir == "." else rel_dir
            dirnames[:] = sorted(d for d in dirnames if not _pruned(f"{rel_dir}/{d}" if rel_dir else d))
            files.extend(f"{rel_dir}/{n}" if rel_dir else n for n in sorted(filenames))
    return sorted(f for f in files if (repo / f).is_file())


def _has_main_guard(tree: ast.Module) -> bool:
    """Настоящий `if __name__ == "__main__"` — узел верхнего уровня модуля
    (Minor DS круга 3: гвард внутри функции точкой входа не делает), сравнение
    на равенство (Minor DS круга 4); кавычки и порядок операндов не важны,
    подстрока в докстринге не считается."""
    for node in tree.body:
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        if len(node.test.ops) != 1 or not isinstance(node.test.ops[0], ast.Eq):
            continue                    # `!= "__main__"` — защита от прямого запуска, не гвард
        parts = [node.test.left, *node.test.comparators]
        names = {p.id for p in parts if isinstance(p, ast.Name)}
        consts = {p.value for p in parts if isinstance(p, ast.Constant)}
        if "__name__" in names and "__main__" in consts:
            return True
    return False


def _strip_comments(text: str, marker: str) -> str:
    """Снять комментарии до конца строки (`//` у Swift, `#` у shell и yml) —
    маркер внутри строкового литерала не считается: перед ним чётное число
    неэкранированных кавычек. Блочные `/* … */` у Swift — тоже. Многострочные
    raw-строки Swift этот лексер не разбирает — их в проекте три, и путей в
    них нет (проверено кругом 2); честный разбор — если появятся."""
    if marker == "//":
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    out = []
    for line in text.splitlines():
        pos = 0
        while True:
            i = line.find(marker, pos)
            if i < 0:
                break
            if len(re.findall(r'(?<!\\)"', line[:i])) % 2 == 0:
                line = line[:i]
                break
            pos = i + len(marker)
        out.append(line)
    return "\n".join(out)


def _python_literals(tree: ast.Module) -> list[str]:
    """Строковые литералы python вне докстрингов: константы, части f-строк и
    склейки путей через «/» (CODE / "src" / "x.py"). Докстринг — первый
    Expr-Constant модуля/класса/функции (Critical GLM круга 1)."""
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_ids.add(id(body[0].value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_ids:
            out.append(node.value)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts: list[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div) \
                    and isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                parts.append(cur.right.value)
                cur = cur.left
            if len(parts) >= 2:
                out.append("/".join(reversed(parts)))
    return out


def packaging_conflicts(модули: dict[str, str]) -> list[Problem]:
    """Имена модулей, которые нельзя поставить рядом, — ОДИН ответ на вопрос
    «что схлопнется при упаковке». На вход идут имена узлов ГРАФА (`module_of`),
    а не всё, что похоже на модуль по форме: сторож защищает граф.

    Проверок две, и пока они стояли двумя блоками в `inventory`, одна знала про
    плоскую раскладку, а вторая нет: `src/a.py` и `packages/x/src/a/b.py` дают
    разные имена (`a` и `a.b`), а корень `a` общий — в рантайме `import a`
    решает порядок `sys.path`, и плоский модуль молча затеняет пакет. Ровно
    это дерево и бывает при переезде №323, когда часть модулей уехала, а часть
    ещё плоская (круг 3 по коду №328, DS C1).

    `владелец` — дистрибутив или плоский корень: имя владельца тоже часть
    ответа, иначе «рядом не ставятся» нечего показать человеку. Дедуп идёт по
    СОСТАВУ сторон, а не по корню: гашение целого корня прятало ДРУГОЙ
    конфликт — стоило двум файлам совпасть точным именем, и плоское затенение
    того же корня не печаталось никогда (круг 4 по коду №328, DS C1).
    """
    out: list[Problem] = []
    по_имени: dict[str, list[str]] = {}
    for rel, имя in модули.items():
        по_имени.setdefault(имя, []).append(rel)
    сказано: set[tuple[str, frozenset[str]]] = set()
    for имя, где in sorted(по_имени.items()):
        if len(где) > 1:
            # два файла с одним импортируемым именем: у сторожа они схлопнулись
            # бы в один узел, и рёбра файла из одного слоя судились бы по слою
            # другого (круг 1 по коду №328, GLM I1)
            сказано.add((имя.split(".")[0], frozenset(_owner(rel) for rel in где)))
            out.append(Problem("collision", f"имя модуля {имя} у {len(где)} файлов "
                                            f"({', '.join(sorted(где))}) — рядом их не поставить, "
                                            f"и слой у них был бы один на двоих"))
    корни: dict[str, set[str]] = {}
    for имя, где in по_имени.items():
        for rel in где:
            корни.setdefault(имя.split(".")[0], set()).add(_owner(rel))
    for корень, владельцы in sorted(корни.items()):
        # про ЭТИ стороны уже сказано строкой выше — не повторяем одно событие
        # дважды; другой состав сторон на том же корне — другое событие
        if len(владельцы) > 1 and (корень, frozenset(владельцы)) not in сказано:
            out.append(Problem("collision", f"импортируемый корень {корень} предоставляют "
                                            f"{len(владельцы)} стороны "
                                            f"({', '.join(sorted(владельцы))}) — рядом не ставятся"))
    return out


def _owner(rel: str) -> str:
    """Кто предоставляет МОДУЛЬ: дистрибутив или плоский корень.

    Звать только на модулях продукта: для всего прочего (`scripts/`, `docs/`)
    ответ «плоский корень» — неправда, просто сегодня недостижимая (круг 5 по
    коду №328, GLM M3)."""
    return form(rel).dist or f"{FLAT_DIR}/"


def inventory(repo: pathlib.Path | None = None) -> Inventory:
    """Один обход, одна классификация, одно чтение и один разбор на файл.
    Всё дальнейшее (`import_graph`, `executables`, `scan`) — чистые функции
    от инвентаря."""
    repo = repo or REPO
    files: dict[str, FileInfo] = {}
    problems: list[Problem] = []
    for rel in _files(repo):
        d = decide(rel)
        kind = d.kind
        if d.conflict:
            problems.append(Problem("conflict", f"{rel}: кандидат в точки входа, но правило KINDS {d.conflict!r} "
                                                f"хочет другого вида — снять правило или шаблон"))
        if kind in ("out", "history"):
            files[rel] = FileInfo(kind, (), None, None)
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            problems.append(Problem("read", f"{rel} не читается ({e.strerror or e}) — упоминания из него не собраны"))
            files[rel] = FileInfo(kind, (), None, None)
            continue
        tree = None
        if kind == "prose":
            hays: tuple[str, ...] = (text,)
        elif rel.endswith(".py"):
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError as e:
                problems.append(Problem("parse", f"{rel} не разбирается ({e.msg}, строка {e.lineno}) — "
                                                 f"упоминания и импорты из него не собраны"))
                files[rel] = FileInfo(kind, (), None, None)
                continue
            hays = tuple(_python_literals(tree))
        elif rel.endswith(".swift"):
            hays = (_strip_comments(text, "//"),)
        else:
            hays = (_strip_comments(text, "#"),)
        executable = None
        if kind == "code" and _is_candidate(rel):
            if rel.endswith(".py"):
                executable = "python с гвардом __main__" if tree is not None and _has_main_guard(tree) else None
            else:
                executable = "shell-скрипт"
        files[rel] = FileInfo(kind, hays, tree, executable)
    # имена — те же, что дают узлы графа (`module_of` = форма ПЛЮС политика).
    # В круге 4 я взял здесь чистую форму по доводу «конфликт — свойство
    # раскладки»; круг 5 показал цену: файл, выведенный правилом `KINDS` в
    # `out` (штатный способ погасить чужой код внутри дистрибутива), узлом
    # графа не станет, а красное про конфликт с ним — да. Довод сильнее: этот
    # сторож защищает ГРАФ, значит и считать обязан по его узлам (круг 5 по
    # коду №328, DS I1; отменяет моё решение круга 4 по GLM M3).
    problems += packaging_conflicts({rel: m for rel in files if (m := module_of(rel)) is not None})
    return Inventory(files, problems)


# ---------------------------------------------------------------- замеры от инвентаря

def modules(inv: Inventory) -> set[str]:
    """Модули продукта — проекция `module_of`, а не свой предикат пути."""
    return {m for rel in inv.files if (m := module_of(rel)) is not None}


def import_graph(inv: Inventory) -> dict[str, set[str]]:
    """Модуль → модули репо, которые он импортирует. Обход всех узлов Import
    (и внутри функций: `llm.py` импортирует `llm_health` лениво). Модуль без
    дерева (не разобрался) — уже проблема инвентаря, здесь просто без рёбер.

    Форму пути знает `module_of`, грамматику импорта — `imports_of`; здесь
    остаётся только знание графа: какое из написанных имён — модуль продукта
    (`_module_by_name`, самый длинный известный префикс)."""
    mods = modules(inv)
    graph: dict[str, set[str]] = {m: set() for m in mods}
    for rel, info in inv.files.items():
        свой = module_of(rel)
        if свой is None or info.tree is None:
            continue                    # не модуль или не разобрался — он уже в inv.problems
        for имя in imports_of(rel, info.tree):
            цель = _module_by_name(имя, mods)
            if цель is not None and цель != свой:
                graph[свой].add(цель)
    return graph


def executables(inv: Inventory) -> dict[str, str]:
    """Исполняемые файлы: путь → почему исполняемый. Python-кандидат без гварда
    (библиотека в `src/` или хелпер в `scripts/`) исполняемым не является, что бы
    про него ни говорили подсказки; shell-скрипт исполняем по расположению."""
    return {rel: info.executable for rel, info in sorted(inv.files.items()) if info.executable}


def layer_of(layout: dict) -> dict[str, str]:
    """Слой каждого модуля: таблица брифа плюс поправки с обоснованием."""
    lay = {m: layer for layer, ms in layout["brief_layers"].items() for m in ms}
    for m, ov in layout["layer_overrides"].items():
        lay[m] = ov["layer"]
    return lay


def violations(graph: dict[str, set[str]], layout: dict) -> list[tuple[str, str]]:
    """Рёбра против стрелок: импортёр из слоя A зависит от слоя B, а B не в
    `allowed[A]`. Внутри слоя — не ребро между слоями."""
    lay = layer_of(layout)
    allowed = {k: set(v) for k, v in layout["allowed"].items()}
    out = []
    for a, deps in graph.items():
        for b in deps:
            la, lb = lay.get(a), lay.get(b)
            if la is None or lb is None or la == lb:
                continue
            if lb not in allowed.get(la, set()):
                out.append((a, b))
    return sorted(out)


def unassigned(graph: dict[str, set[str]], layout: dict) -> list[str]:
    lay = layer_of(layout)
    return sorted(m for m in graph if m not in lay)


def stale_layers(graph: dict[str, set[str]], layout: dict) -> list[str]:
    """Имена в таблице слоёв, которым в дереве не нашлось модуля."""
    return sorted(m for m in layer_of(layout) if m not in graph)


def move_candidates(graph: dict[str, set[str]], layout: dict) -> dict[str, list[str]]:
    """Имя из таблицы → модули дерева, ПОХОЖИЕ на его новое место.

    Именно кандидаты, а не вердикт: сопоставление по последнему сегменту имени
    врёт в трёх случаях — переезд с переименованием (кандидатов ноль),
    двусмысленность (их два), удаление одного модуля и появление другого с тем
    же хвостом (кандидат есть, но это чужой код). Утверждать «переехал» на
    такой опоре нельзя, поэтому функция отдаёт список, а решение остаётся
    человеку — третий исход «не уверен» вместо ложной уверенности
    (круг 1 по коду №328, GLM I2).

    Вредным был не сам промах, а ЕДИНСТВЕННЫЙ императив прежнего сообщения:
    «убрать из layout.json» гасил красное, снимая охрану с переехавшего кода
    (входной круг №328, обе головы). Поэтому совет теперь всегда называет оба
    пути, а кандидаты — подсказка к первому.
    """
    lay = layer_of(layout)
    по_хвосту: dict[str, list[str]] = {}
    for m in graph:
        if m not in lay:
            по_хвосту.setdefault(m.rpartition(".")[2], []).append(m)
    return {m: sorted(по_хвосту.get(m.rpartition(".")[2], []))
            for m in stale_layers(graph, layout)}


def _tokens(text: str) -> set[str]:
    out = set()
    for m in _PATH.finditer(text):
        tok = m.group(0)
        out.add(tok if tok.startswith(TOKEN_PREFIXES) else tok.removeprefix("./"))
    return out


def _resolve(hits: set[str], targets: set[str]) -> tuple[set[str], dict[str, list[str]], set[str]]:
    """Найденные токены → (пути целей, неоднозначные голые имена → кандидаты,
    голые имена без цели). Полный путь — как есть; голое имя скрипта — по
    basename (make_app.sh из CI с working-directory: app), и только если цель
    одна: два скрипта с одним именем — это проблема, а не тихий выбор
    (Important DS и GLM круга 3); имя без цели — чужой или порождаемый скрипт,
    показывается на карте."""
    by_name: dict[str, list[str]] = {}
    for t in targets:
        by_name.setdefault(pathlib.PurePosixPath(t).name, []).append(t)
    paths: set[str] = set()
    ambiguous: dict[str, list[str]] = {}
    loose: set[str] = set()
    for h in hits:
        if "/" in h:
            paths.add(h)
            continue
        found = sorted(by_name.get(h, []))
        if len(found) == 1:
            paths.update(found)
        elif found:
            ambiguous[h] = found
        else:
            loose.add(h)
    return paths, ambiguous, loose


def scan(inv: Inventory) -> Scan:
    """Замер названных путей: код — упоминания как связи; проза — только
    существование; голое имя без цели откуда угодно — на карту (Important DS
    круга 4: из прозы гасло молча). Проблемы инвентаря и сканера — одним
    списком, все они красят гейт."""
    mentions: dict[str, set[str]] = {}
    prose: dict[str, set[str]] = {}
    loose: dict[str, set[str]] = {}
    problems = [p.text for p in inv.problems]
    # Python внутри области кода обязан быть ЧЕМ-ТО названным: модулем продукта
    # или точкой входа. Иначе первый же новый верхний каталог (упаковка, разовая
    # утилита) уезжает из-под охраны молча — инструмент это давно знал («дыра в
    # таблице» в разделе фактов), но гейту не говорил (входной круг №328).
    for rel in sorted(inv.files):
        # «Наш ли это python» спрашивается у ФОРМЫ один раз, а не собирается из
        # её проекций: роль, отличная от `outside`, значит «раскладка уже
        # решила». Пока вопрос собирался из `module_of` + `_is_candidate`,
        # явно решённый `packages/<д>/src/__init__.py` краснел как дыра в
        # таблице — с советом, который нечем выполнить: правилом-префиксом
        # этот случай невыразим (круг 3 по коду №328, GLM I1).
        if not rel.endswith(".py") or form(rel).role != "outside" or _is_candidate(rel):
            continue
        d = decide(rel)
        # Два разных случая, и оба — дыра. `by == "default"` значит, что до
        # файла не дотянулось НИ ОДНО правило: `_by_suffix` тихо отдаёт таким
        # `out`, и первая редакция инварианта (условие `kind == "code"`) не
        # видела ровно тот случай, ради которого писалась (круг 1 по коду
        # №328, DS C1).
        if d.by == "default":
            problems.append(f"{rel}: python, до которого не дотянулось ни одно правило KINDS — "
                            f"решить явно: модуль продукта, кандидат в точки входа или вид `out` с обоснованием")
        elif d.kind == "code":
            problems.append(f"{rel}: python в области кода, но ни модуль продукта, ни кандидат в точки входа "
                            f"по форме пути — "
                            f"решить в KINDS (вид `out` с обоснованием) или положить по форме раскладки")
    targets = set(executables(inv))
    bucket = {"code": mentions, "prose": prose}
    for rel, info in inv.files.items():
        dest = bucket.get(info.kind)
        if dest is None:
            continue
        hits, ambiguous, unresolved = _resolve({t for hay in info.haystacks for t in _tokens(hay)}, targets)
        for h in hits:
            if h != rel:
                dest.setdefault(h, set()).add(rel)
        for name, found in sorted(ambiguous.items()):
            problems.append(f"{rel}: голое имя {name} неоднозначно ({', '.join(found)}) — назвать полным путём")
        for name in unresolved:
            loose.setdefault(name, set()).add(rel)
    return Scan(mentions, prose, loose, problems)


#: Что считает `--report`: имя факта → как он читается в дереве. Списки работ фазы
#: (кто читает переменную корня, кто считает корень от своего файла, кто собирает
#: шов) берутся отсюда, а не из памяти автора: три круга постановки фазы 3 дали
#: Critical на расхождении ручного перечня с фактом (10 скриптов против 22, 10
#: мест сборки против 11).
#: Формы чтения переменной окружения, которые распознаёт замер — грамматика, а не
#: «все способы». Вызовы и подписки разведены, потому что ими пользуется сам
#: распознаватель: шапка отчёта печатает то, чем он работает, а не параллельный
#: список (Important DS круга 9 — расширить список и забыть код было можно).
ENV_READ_CALLS = ("os.environ.get", "os.getenv", "environ.get")
ENV_READ_SUBSCRIPTS = ("os.environ", "environ")
ENV_READ_FORMS = ENV_READ_CALLS + tuple(f"{b}[...]" for b in ENV_READ_SUBSCRIPTS)


class ModuleEvents(NamedTuple):
    """События модуля в одной системе координат: что исполняется НА ИМПОРТЕ и что
    только при вызове (внутри тела функции; тело класса, декораторы и значения по
    умолчанию исполняются на импорте — Critical DS круга 11).
    Третий круг подряд по этому месту (8: обход в ширину, 9: минимум по всему
    дереву, 10: фраза «вставки нет» при вставке внутри функции) — поэтому здесь
    исход значением, а не строкой: у фразы нет способа обойти класс."""
    top_reads: list[int]        # чтение переменной на импорте
    inner_reads: list[int]      # чтение при вызове — с порядком импорта не спорит
    top_insert: int | None      # вставка в sys.path на импорте
    inner_insert: int | None    # вставка только внутри функции

    @property
    def order(self) -> Literal["ok", "read_before_insert", "insert_only_inside", "no_insert"]:
        """Исход сравнения: `ok` — читает после вставки или вставка не нужна;
        `read_before_insert` — читает раньше, чем `src/` станет импортируемым;
        `insert_only_inside` — вставка есть, но на импорте не срабатывает;
        `no_insert` — вставки нет вовсе."""
        if self.top_reads and self.top_insert is not None and self.top_reads[0] < self.top_insert:
            return "read_before_insert"
        if self.top_insert is None:
            return "insert_only_inside" if self.inner_insert is not None else "no_insert"
        return "ok"


def _levels(node: ast.AST, at_import: bool = True):
    """Узлы дерева с пометкой «исполняется на импорте». При вызове — только тело
    функции; тело класса, декораторы и значения по умолчанию исполняются на
    импорте (Critical DS круга 11: уровень брался у модульного оператора целиком,
    и всё под `class` объявлялось «при вызове»). Правило инвертировано — список
    того, что считается верхним уровнем, забыть расширить больше нельзя."""
    yield node, at_import
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # всё, кроме тела, — заголовок: декораторы, умолчания, аннотации,
            # возвращаемый тип. Перечислять их поимённо нельзя: такой список уже
            # забыли один раз (Critical DS круга 12 — аннотации и `returns` в него
            # не попали), поэтому инверсия и здесь. Оговорка про аннотации: под
            # `from __future__ import annotations` (PEP 563) они не вычисляются
            # вовсе — замер этого не различает, потому что чтения корня в
            # аннотациях в проекте нет; различение дороже пользы (Important GLM
            # круга 13, принято как упрощение с записью).
            for field, value in ast.iter_fields(child):
                if field == "body":
                    continue
                for part in (value if isinstance(value, list) else [value]):
                    if isinstance(part, ast.AST):
                        yield from _levels(part, at_import)
            body = child.body if isinstance(child.body, list) else [child.body]
            for stmt in body:
                yield from _levels(stmt, False)
        elif isinstance(child, ast.GeneratorExp):
            # генератор ленив: сразу вычисляется только источник первого `for`,
            # остальное — при итерации (Important DS круга 13). Списковые и
            # словарные включения вычисляются целиком и сюда не попадают.
            first = child.generators[0] if child.generators else None
            if first is not None:
                yield from _levels(first.iter, at_import)
            for part in ast.iter_child_nodes(child):
                if part is not first:
                    yield from _levels(part, False)
            if first is not None:
                for part in ast.iter_child_nodes(first):
                    if part is not first.iter:
                        yield from _levels(part, False)
        else:
            yield from _levels(child, at_import)


def module_events(tree: ast.Module, var: str) -> ModuleEvents:
    """Один обход: чтения переменной и вставки `sys.path`, разведённые по тому,
    исполняется ли узел на импорте (Critical DS круга 10 — их мерили двумя
    независимыми обходами в разных областях видимости, и номер строки чтения
    внутри функции сравнивался с номером верхнеуровневой вставки)."""
    top_reads, inner_reads = [], []
    top_insert = inner_insert = None
    for node, at_import in _levels(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in ("sys.path.insert", "sys.path.append"):
            if at_import:
                top_insert = node.lineno if top_insert is None else min(top_insert, node.lineno)
            else:
                inner_insert = node.lineno if inner_insert is None else min(inner_insert, node.lineno)
        for lineno in _env_reads(node, var, deep=False):
            (top_reads if at_import else inner_reads).append(lineno)
    return ModuleEvents(sorted(set(top_reads)), sorted(set(inner_reads)), top_insert, inner_insert)


def _env_reads(tree: ast.AST, var: str, *, deep: bool = True) -> list[int]:
    """Строки, где модуль читает переменную окружения `var` сам — формы из
    `ENV_READ_FORMS` (включая `environ.get` после `from os import environ`, Important
    GLM круга 8). Это грамматика, а не «все способы»: динамический ридер
    (`getattr(os, "environ")`) в замер не попадёт. Строка в справке argparse
    вызовом не является."""
    out = []
    for node in (ast.walk(tree) if deep else [tree]):
        if isinstance(node, ast.Call):
            fn = node.func
            reader = isinstance(fn, ast.Attribute) and ast.unparse(fn) in ENV_READ_CALLS
            if reader and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == var:
                out.append(node.lineno)
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == var:
            if ast.unparse(node.value) in ENV_READ_SUBSCRIPTS:
                out.append(node.lineno)
    return sorted(out)


#: Функции канона, которым положение файла отдают на вход: подъём вверх делают
#: они, а не вызывающий. Имя проверяется вместе с происхождением — локальная
#: функция с тем же именем каноном не становится (Important обеих голов круга 3).
ROOT_CANON_CALLS = ("resolve_root", "code_root", "require_data_root")
#: Вызовы, куда путь от `__file__` уходит целиком и корнем не становится.
ROOT_BOOTSTRAP_CALLS = ("sys.path.insert", "sys.path.append")


def _canon_names(tree: ast.Module, только: tuple[str, ...] = ROOT_CANON_CALLS) -> set[str]:
    """Имена, под которыми в модуль пришли функции канона — включая псевдонимы.

    Совпадения по последнему сегменту имени мало: локальная `def code_root(m)`
    получала бы прощение, а `from charoite_paths import resolve_root as root_of`
    краснел бы на верном коде (обе головы круга 3, независимо).

    `только` сужает набор до одной функции канона: форме `snapshot` интересен
    ровно корень ДАННЫХ, а снимок корня КОДА (`CODE = code_root(__file__)`)
    законен весь процесс — код лежит там, где лежит.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "charoite_paths":
            if any(a.name == "*" for a in node.names):
                out |= set(только)                    # `import *` приносит канон под своими именами
            out |= {a.asname or a.name for a in node.names if a.name in только}
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "charoite_paths":
                    # `charoite_paths.resolve_root(...)` — сегмент имени, а модуль назван
                    out |= {f"{a.asname or a.name}.{n}" for n in только}
    return out


def _call_args(node: ast.Call):
    """Все аргументы вызова: позиционные, распакованные и по имени.

    Ключевые обходились не всюду, и законный `resolve_root(module_file=__file__)`
    краснел на верном коде (Critical DS круга 3).
    """
    yield from node.args
    yield from (kw.value for kw in node.keywords)


#: Что можно построить вокруг `__file__` внутри законного аргумента: обернуть в
#: путь, привести к строке, привести к абсолютному. Подъём в этот список не
#: входит — `resolve_root(Path(__file__).parent.parent)` отдал бы канону чужой
#: путь, и данные уехали бы мимо переменной корня целиком (Critical обеих голов
#: круга 4). Список закрытый: прощается форма выражения, а не всё поддерево.
#: Конструкторы пути прощаются ТОЛЬКО в форме с модулем (`pathlib.Path(...)`):
#: голое имя может быть локальной тёзкой с подъёмом внутри, ровно как было с
#: именем канона (Important GLM круга 7). Встроенное `str` в этой форме не
#: бывает и остаётся голым.
ARG_TRANSPARENT_QUALIFIED = ("Path", "PurePath", "PurePosixPath", "resolve", "absolute")
ARG_TRANSPARENT_BARE = ("str",)
ARG_TRANSPARENT = ARG_TRANSPARENT_QUALIFIED + ARG_TRANSPARENT_BARE


#: Компонент пути, который поднимает вверх не атрибутом, а значением. Сравнивать
#: строку целиком мало: `'../..'`, `'../'` и `'../src'` — те же подъёмы, записанные
#: одним литералом, и `PurePosixPath` нормализует их к тем же частям (Critical
#: обеих голов круга 6). Поэтому константа разбирается как путь.
ROOT_CLIMB_PART = ".."


def _climbs_by_value(value: object) -> bool:
    """Константа уводит путь от файла? Разбор пути, а не равенство строк.

    Два способа увести: подняться вверх компонентом `..` и обнулить всё
    предыдущее якорем — соединение с абсолютным путём выбрасывает `__file__`
    из выражения целиком, и канон получил бы корень диска (Important DS круга
    7). Байты приводятся к строке: сегодня ни одна прозрачная обёртка их не
    принимает, но связка «никто не принимает байты» нигде не записана, и
    добавление одной функции в список сделало бы `b'..'` молчащим (Minor GLM).
    """
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    if not isinstance(value, str):
        return False
    parts = pathlib.PurePosixPath(value).parts
    return ROOT_CLIMB_PART in parts or pathlib.PurePosixPath(value).is_absolute()


def _plain_file_arg(arg: ast.AST, *, steps: int = 0):
    """Узлы `__file__` в аргументе, если ВСЁ выражение вокруг них безопасно.

    Прощение структурное, а не однопутевое: узел законен только когда разобраны
    все его дети. Прежний обход спускался в первого ребёнка и выбрасывал
    остальные, поэтому `Path(__file__, '..', '..')` внутри законного вызова
    проходил молча — подъём прятался в хвостовых аргументах, которые никто не
    смотрел (Critical обеих голов круга 5, воспроизведено).

    Безопасны: сам `__file__`, строковая или числовая константа без компонента
    подъёма, прозрачная обёртка (`ARG_TRANSPARENT`) со всеми безопасными
    детьми, и до `steps` обращений к родительскому каталогу. `steps` разный у
    двух назначений: канону путь отдают как есть (0 — подъём делает он сам),
    вставке пути кладут каталог модуля (1). Всё прочее — не прощается.
    """
    found: list[ast.Name] = []
    if _walk_safe(arg, steps, found):
        yield from found


def _walk_safe(node: ast.AST, steps: int, found: list[ast.Name]) -> bool:
    """Всё выражение безопасно? Попутно собирает найденные `__file__`."""
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            found.append(node)
            return True
        return False                      # переменная-посредник непрозрачна
    if isinstance(node, ast.Constant):
        return not _climbs_by_value(node.value)
    if isinstance(node, ast.Call):
        fn = node.func
        qualified = isinstance(fn, ast.Attribute)
        name = fn.attr if qualified else getattr(fn, "id", "")
        allowed = ARG_TRANSPARENT_QUALIFIED if qualified else ARG_TRANSPARENT_BARE
        if name not in allowed:
            return False
        kids = list(node.args) + [kw.value for kw in node.keywords]
        # `p.resolve()` — получатель несёт путь, его надо разобрать; `pathlib.Path(...)`
        # — слева имя модуля, данных в нём нет. Различаем по форме: выражение против
        # голого имени (иначе законная вставка пути краснеет на слове «pathlib»)
        if isinstance(fn, ast.Attribute) and not isinstance(fn.value, ast.Name):
            kids.append(fn.value)
        return all(_walk_safe(k, steps, found) for k in kids)
    if isinstance(node, ast.Attribute):
        if node.attr in ARG_TRANSPARENT:
            return _walk_safe(node.value, steps, found)
        if node.attr == "parent" and steps > 0:
            return _walk_safe(node.value, steps - 1, found)
        return False
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _walk_safe(node.left, steps, found) and _walk_safe(node.right, steps, found)
    return False


def _заведомо_мертва(node: ast.stmt) -> bool:
    """Ветка, тело которой при импорте не исполняется по условию.

    `if TYPE_CHECKING:` — типовой приём для импортов ради аннотаций, `if False:`
    и `if 0:` — выключенный код. Считать их исполняемыми значит красить гейт на
    том, чего в рантайме нет (круг 3 по коду №329, DS I5).

    Ложность условия считает сам питон (`literal_eval`), а не перечисление
    узлов: `if []:` и `if ():` — то же выключение, но это `List` и `Tuple`, и
    предикат по `Constant` их не видел (круг 5 по коду №329, DS M3).
    """
    if not isinstance(node, (ast.If, ast.While)):
        return False
    т = node.test
    try:
        return not ast.literal_eval(т)
    except Exception:             # noqa: BLE001 — `if 1/0:` считается по-настоящему
        pass                      # условие не известно на импорте — судим по форме ниже
    if isinstance(т, ast.Name):
        return т.id == "TYPE_CHECKING"
    # `typing.TYPE_CHECKING` — та же идиома в форме атрибута (круг 4, GLM I3)
    return isinstance(т, ast.Attribute) and т.attr == "TYPE_CHECKING"


def _гвард_точки_входа(node: ast.stmt) -> str:
    """Сравнение с `__main__` в условии: `"=="`, `"!="` или `""` (не гвард).

    `if __name__ == "__main__":` — единственная ветка модуля, которая при
    импорте не исполняется вовсе: корень, спрошенный там, спросила сама точка
    входа. Но отбрасывать весь узел нельзя — `else` у такого гварда идёт
    именно при импорте, а `!=` переворачивает обе ветки (круг 2 по коду №329,
    GLM C2).
    """
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return ""
    if len(node.test.ops) != 1 or not isinstance(node.test.ops[0], (ast.Eq, ast.NotEq)):
        return ""
    левое, правое = node.test.left, node.test.comparators[0]
    if not (isinstance(левое, ast.Name) and левое.id == "__name__"):
        return ""
    if not (isinstance(правое, ast.Constant) and правое.value == "__main__"):
        return ""
    return "==" if isinstance(node.test.ops[0], ast.Eq) else "!="


def _на_импорте(тело: list[ast.stmt]) -> list[ast.stmt]:
    """Узлы, которые ВЫПОЛНЯЮТСЯ при импорте модуля.

    Не «верхний уровень файла»: при импорте исполняется и тело класса, и всё,
    что обёрнуто в `if` / `try` / `with` / `for` — а `try: ROOT = …` это типовой
    bootstrap-приём, которым обложены скрипты следующего куска. Первая редакция
    обходила только `tree.body`, и живой снимок полем класса в `src/llm.py`
    (писатель аренд модели) был ей невидим (круг 1 по коду №329: GLM C1 про
    класс, DS C1 про остальные обёртки).

    Тела функций сюда не входят: там вычисление ленивое, в этом вся правка.
    Граница названа честно: `ROOT = свой_хелпер()`, где канон зовётся ВНУТРИ
    хелпера, статически отсюда не виден — такую запись ловит не гейт, а
    свидетель поведения (`tests/test_backup_offload.py`, список
    `СПРОСИТЬ_КОРЕНЬ`).
    """
    out: list[ast.stmt] = []
    for node in тело:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(node)                    # ради ЗНАЧЕНИЙ ПО УМОЛЧАНИЮ: они считаются
            continue                            # на импорте, а тело — нет, в этом вся правка
        if _заведомо_мертва(node):
            # мёртв только блок; `else` у него при импорте исполняется — тот же
            # случай, что с гвардом точки входа (круг 4 по коду №329, DS I1)
            out += _на_импорте(getattr(node, "orelse", []))
            continue
        гвард = _гвард_точки_входа(node)
        if гвард:
            # при импорте идёт ровно одна половина гварда: у `==` — else,
            # у `!=` — сам блок; вторая принадлежит запуску как скрипту
            assert isinstance(node, ast.If)
            out += _на_импорте(node.orelse if гвард == "==" else node.body)
            continue
        out.append(node)
        # дети — ВСЕ, через обход самого ast: список имён полей («body», «orelse»,
        # «handlers»…) пропустил `match`/`case`, потому что его ветки лежат в
        # `cases[i].body` — перечислять поля значит отставать на одну конструкцию
        # языка (круг 2 по коду №329, DS C1 = GLM C2)
        дети = [c for c in ast.iter_child_nodes(node) if isinstance(c, (ast.stmt, ast.match_case,
                                                                       ast.ExceptHandler))]
        вложенные: list[ast.stmt] = []
        for c in дети:
            вложенные += c.body if isinstance(c, (ast.match_case, ast.ExceptHandler)) else [c]
        if вложенные:
            out += _на_импорте(вложенные)
    return out


def _без_ленивого(node: ast.AST):
    """Обход узла, не заходящий в ленивые тела.

    Ленивы лямбда, тело функции и ГЕНЕРАТОР: `(_root() / n for n in ...)` не
    спрашивает корень, пока его не начнут перебирать (круг 4 по коду №329,
    GLM I2; генератор — круг 5, DS M2). Списковое, множественное и словарное
    включения, наоборот, вычисляются на месте и остаются под правилом.

    Граница названа честно: немедленно вызванная лямбда `(lambda: канон())()`
    корень на импорте спрашивает, а правило её не видит — ловит поведение.
    """
    if isinstance(node, (ast.Lambda, ast.GeneratorExp, ast.FunctionDef, ast.AsyncFunctionDef)):
        return
    yield node
    for c in ast.iter_child_nodes(node):
        yield from _без_ленивого(c)


def _имя(узел: ast.expr) -> str:
    """Короткое имя ссылки на функцию: `f` и `mod.f` — оба «f»."""
    return узел.attr if isinstance(узел, ast.Attribute) else getattr(узел, "id", "")


def _имена_канона_данных(tree: ast.Module) -> set[str]:
    """Имена корня ДАННЫХ в этом модуле: само имя канона и его псевдонимы.

    Точечная запись приходит как `модуль.resolve_root` — на месте вызова
    сравнивается последний сегмент, поэтому режем его здесь же.
    """
    имена = {n.rsplit(".", 1)[-1] for n in _canon_names(tree, (DATA_ROOT_CALL,))}
    return имена | {DATA_ROOT_CALL}


def _спросит_корень(call: ast.Call, свои: set[str], канон: set[str] | None = None) -> bool:
    """Этот вызов даст ответ канона о корне данных.

    Одно правило на все места, где вопрос задаётся (замыкание имён и проба
    формы): раньше выражение «имя вызываемого» стояло тремя дословными
    копиями, и они расходились по строгости (круг 5 по коду №329, DS I1).

    Имя канона засчитывается и через точку (`charoite_paths.resolve_root(...)`
    — законная запись), а СВОИ имена — только голым вызовом: у чужого объекта
    метод-тёзка живёт своей жизнью, и `json.load(...)` не становится снимком
    оттого, что в модуле есть свой `load`, читающий корень.
    """
    канон = {DATA_ROOT_CALL} if канон is None else канон
    if isinstance(call.func, ast.Attribute):
        return call.func.attr in канон
    имя = getattr(call.func, "id", "")
    return имя in канон or имя in свои


def _имена_корня(tree: ast.Module) -> set[str]:
    """Имена МОДУЛЬНОГО уровня, вызов которых даёт ответ канона о корне данных.

    Смысл «это спросит корень» имя получает двумя способами, и оба считаются
    ОДНИМ замыканием до неподвижной точки, а не двумя детективами:

    * функция модуля зовёт канон или уже известное имя — `_CFG = _cfg()` на
      верхнем уровне `src/mcp_server.py` читал конфиг по неназванному корню
      (живой дефект, круг по решению №332, DS C3);
    * имя связано с каноном без вызова — `_r = resolve_root`, а следом
      `X = _r(__file__)`: полноценный снимок, у которого на месте вызова стоит
      псевдоним (круг 5 по коду №329, GLM I2).

    Область видимости — модуль, и только он. Раньше таблица строилась обходом
    всего дерева (`ast.walk`), из-за чего методы классов и вложенные функции
    ложились в неё по ГОЛОМУ имени и затирали друг друга: два класса с методом
    `model()` — одно ведро, выигрывал объявленный позже. Это давало промах в
    одну сторону и ложную красноту в другую — на честном коде, где снимка нет
    (круг 5 по коду №329, GLM I1). Питон различает эти имена скоупом, значит и
    таблица обязана: в неё идут только определения самого модуля.

    Границы, которые гейт не закрывает и закрывать не будет (их ловит не текст,
    а поведение — свидетель `tests/test_backup_offload.py` и отказ канона на
    неназванном корне, №332):

    * метод класса, спрашивающий корень, при вызове на импорте (`X = A().m()`);
    * обёртка над каноном как ЗНАЧЕНИЕ — `functools.partial(resolve_root)`;
    * хелпер, импортированный из чужого модуля (замыкание не выходит за файл).
    """
    # тело класса при импорте исполняется, и `_на_импорте` честно отдаёт его
    # содержимое — но объявленные там имена принадлежат КЛАССУ, а не модулю:
    # позвать их именем модуля нельзя, и в таблице модульных имён им не место
    в_классе = {id(n) for c in ast.walk(tree) if isinstance(c, ast.ClassDef) for n in c.body}
    исполняется = [n for n in _на_импорте(tree.body) if id(n) not in в_классе]
    функции = {n.name: n for n in исполняется
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    псевдонимы: dict[str, str] = {}
    for n in исполняется:
        цели = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        значение = getattr(n, "value", None)
        if isinstance(значение, (ast.Name, ast.Attribute)) and len(цели) == 1 and isinstance(цели[0], ast.Name):
            псевдонимы[цели[0].id] = _имя(значение)

    канон = _имена_канона_данных(tree)
    знают: set[str] = set()
    менялось = True
    while менялось:
        менялось = False
        for имя, узел in функции.items():
            if имя in знают:
                continue
            # тело, и только оно: корень в ЗНАЧЕНИИ ПО УМОЛЧАНИЮ или в
            # декораторе замораживается на строке самого `def` — она и
            # краснеет, а вызов такой функции свежего ответа уже не даёт.
            # Ленивое внутри тела (`return lambda: канон()`) тоже не считается
            # вопросом: спросит тот, кто вызовет лямбду (круг 5, DS «вопрос 1»)
            if any(isinstance(v, ast.Call) and _спросит_корень(v, знают, канон)
                   for st in узел.body for v in _без_ленивого(st)):
                знают.add(имя); менялось = True
        for имя, источник in псевдонимы.items():
            if имя not in знают and (источник in канон or источник in знают):
                знают.add(имя); менялось = True
    return знают


def _root_snapshots(tree: ast.Module) -> list[int]:
    """Строки, где ответ канона о корне ДАННЫХ запоминается НА ИМПОРТЕ.

    Третья форма вывода корня — и самая тихая: `ROOT = resolve_root(__file__)`
    на верхнем уровне выглядит как обращение к канону, поэтому две прежние
    формы её не видят (`__file__` стоит аргументом канона — законно; чтения
    переменной нет вовсе). А вред тот же, что у своей копии правила: значение
    снимается РАНЬШЕ, чем точка входа успевает назвать корень, и процесс
    разъезжается сам с собой — половина модулей живёт в названном корне, другая
    в выведенном из положения файла, молча (замер 21.09: назвать корень после
    импорта такого модуля — два разных ответа в одном процессе).

    Корень КОДА (`code_root`) здесь не при чём: код лежит там, где лежит, его
    снимок на импорте верен весь процесс. Ловится только корень данных.
    """
    out: list[int] = []
    знают_корень = _имена_корня(tree)
    канон_модуля = _имена_канона_данных(tree)
    for node in _на_импорте(tree.body):
        # «связать имя с ответом» — не только присваивание: моржовый оператор и
        # значение по умолчанию у аргумента вычисляются при импорте ровно так же
        # (круг 2 по коду №329, DS I3)
        части: list[ast.expr] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # у функции на импорте считаются значения по умолчанию И декораторы;
            # в тело заходить нельзя — ленивый морж `(r := resolve_root(...))`
            # внутри функции это ровно та запись, которую правило советует
            # взамен снимка (круг 3, DS C1 = GLM I1; декораторы — круг 4, DS I2)
            части += node.args.defaults + [k for k in node.args.kw_defaults if k]
            части += node.decorator_list
        else:
            # декоратор КЛАССА вычисляется на импорте ровно как декоратор
            # функции; класс идёт этой веткой, и без строки ниже правило знало
            # только половину случая (круг 5 по коду №329, DS I3)
            части += getattr(node, "decorator_list", [])
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                части.append(node.value)
            части += [n for n in _без_ленивого(node) if isinstance(n, ast.NamedExpr)]
        if not части:
            continue
        # обход без ленивых тел: `X = lambda: resolve_root(...)` считается при
        # ВЫЗОВЕ лямбды, а не при импорте (круг 4 по коду №329, GLM I2)
        for inner in [i for часть in части for i in _без_ленивого(часть)]:
            if not isinstance(inner, ast.Call):
                continue
            if _спросит_корень(inner, знают_корень, канон_модуля):
                out.append(node.lineno)
                break
    return sorted(set(out))


def _file_roots(tree: ast.Module) -> list[int]:
    """Строки, где `__file__` стоит НЕ в одном из двух разрешённых мест.

    Три круга подряд правило пыталось распознать подъём вверх — сначала по
    цепочке `.parent.parent`, потом по индексу и `dirname`, потом по предкам
    узла. Каждый раз следующий круг находил написание, которое предикат не
    видит: хелпер с параметром, обёртку над конструктором пути, промежуточную
    переменную, компонент `..`, цепочку длиннее бюджета предков. Положение файла
    — материал, который течёт через присваивания и вызовы, и догонять его
    предикатом значит отставать на одно написание за круг.

    Поэтому предиката больше нет. `__file__` имеет право стоять ровно в двух
    местах: аргументом функции канона (подъём живёт внутри канона, где его видно
    человеком) и аргументом вставки пути (bootstrap, корнем не становится). Всё
    остальное — расхождение, независимо от того, что с ним делают дальше: путь к
    себе для перезапуска берётся от `code_root`, как соседние вызовы того же
    файла. Обе головы круга 3 пришли к этому независимо.

    Граница правила названа честно: подъём НАД результатом канона
    (`dirname(code_root(__file__))`) оно не ловит — `__file__` там стоит в
    законном месте. Это другой класс: не «модуль сам выводит корень» (четыре
    случая дрейфа, ради которых правило и заведено), а «взял у канона и
    испортил» — одиночная ошибка, видимая в диффе. В боевом коде таких мест
    ноль (замер по `src/` и `scripts/`), а закрытие потребовало бы вернуть
    предикат подъёма, от которого этот круг и избавился. Если случай появится,
    его место — тест канона «результат абсолютный и не поднимается», а не
    здесь (Critical GLM круга 3, отклонён с обоснованием; хвост в №321).
    """
    canon = _canon_names(tree)
    legit: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        full = ast.unparse(node.func)
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if not ((name in canon or full in canon) or full in ROOT_BOOTSTRAP_CALLS):
            continue
        steps = 1 if full in ROOT_BOOTSTRAP_CALLS else 0
        for arg in _call_args(node):
            for n in _plain_file_arg(arg, steps=steps):
                legit.add((n.lineno, n.col_offset))
    return sorted({node.lineno for node in ast.walk(tree)
                   if isinstance(node, ast.Name) and node.id == "__file__"
                   and (node.lineno, node.col_offset) not in legit})


def _calls(tree: ast.Module, names: tuple[str, ...]) -> dict[str, list[int]]:
    """Строки вызовов по имени: `LLM(`, `GraphSearch(`, `graphs.graph_dir(` и т. п.
    Имя сравнивается по последнему сегменту, чтобы ловить и `llm.LLM`, и `LLM`."""
    out: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = ast.unparse(node.func)
        for name in names:
            if callee == name or callee.endswith("." + name):
                out.setdefault(name, []).append(node.lineno)
    return {k: sorted(v) for k, v in out.items()}


#: Исход порядка → фраза отчёта. Таблица, а не цепочка `if`: полноту сверяет гейт
#: по тому же `Literal`, иначе пятый исход молча не печатался бы (Important DS
#: круга 13). Значения — функции: вычисляется только своя ветка.
ORDER_NOTES: dict[str, "Callable[[ModuleEvents, FileInfo], str]"] = {
    "ok": lambda ev, info: "",
    "read_before_insert": lambda ev, info: (f"; чтение в строке {ev.top_reads[0]} ВЫШЕ вставки "
                                            f"sys.path на импорте ({ev.top_insert})"),
    "insert_only_inside": lambda ev, info: (f"; вставки sys.path на импорте нет, есть внутри "
                                            f"функции ({ev.inner_insert})"),
    "no_insert": lambda ev, info: "; вставки sys.path в файле нет" if info.executable else "",
}


#: Единственный модуль, которому положено выводить корень: он и есть канон
#: (`resolve_root`, `code_root`). Правило родилось из четырёх независимых случаев
#: дрейфа: модуль графов скопировал разбор значения и потерял `strip` (починено
#: кругом по PR #385), ревизия ядер скопировала уже починенный разбор и потеряла
#: `resolve`, мутатор — то же самое, у скрипта моделей нет даже `strip`. Каждая
#: копия несла рядом ссылку на канон, то есть договорённость не просто не
#: сработала — она давала ложную уверенность (обе головы кругов №321).
ENV_ROOT_VAR = "CHAROITE_ROOT"
ENV_ROOT_OWNER = "src/charoite_paths.py"

#: Функция канона, отвечающая про корень ДАННЫХ. Её ответ имеет право звучать
#: на каждом обращении и не имеет права запоминаться на импорте (форма
#: `snapshot` ниже).
DATA_ROOT_CALL = "resolve_root"

#: Где инвариант уже обязан выполняться. Скрипты переводятся следующим куском
#: фазы 3: у 12 из них чтение стоит выше вставки в `sys.path`, то есть канон в
#: этот момент ещё нельзя импортировать, и перевод требует правки bootstrap.
#: Область — не потолок и не амнистия: она сокращается и расширению не подлежит.
ENV_ROOT_ENFORCED = ("src/",)

#: Формы вывода корня — таблица, а не одно правило. Первая редакция счёта ловила
#: только чтение переменной, и этого хватало ровно до первой проверки: дефект, из-за
#: которого правило и завели (модели искались от положения файла), чтения переменной
#: не содержал вовсе — вернуть прежнюю строку, и гейт оставался зелёным при всех
#: тестах (Critical DS выходного круга, воспроизведено). Новая форма — запись здесь,
#: а не ещё один цикл в гейте.
ROOT_SHAPES: tuple[tuple[str, Callable[[ast.Module], list[int]], str], ...] = (
    ("env", lambda tree: _env_reads(tree, ENV_ROOT_VAR),
     f"читает {ENV_ROOT_VAR} сам"),
    ("file", _file_roots,
     "ставит __file__ мимо канона и мимо вставки пути — подъём живёт внутри канона"),
    ("snapshot", _root_snapshots,
     f"запоминает ответ {DATA_ROOT_CALL} на импорте — раньше, чем точка входа назвала корень"),
)


def root_derivations(inv: Inventory) -> dict[str, dict[str, list[int]]]:
    """Кто выводит корень сам и какой формой — файл → форма → строки.

    Грамматика форм одна на всех (`ROOT_SHAPES` поверх `_env_reads`/`_file_roots`):
    замер печатает их человеку с порядком строк и заметками, гейт считает
    нарушителей. До этого гейт о находках не знал вовсе и правило нечем было
    выразить.
    """
    out: dict[str, dict[str, list[int]]] = {}
    for rel, info in sorted(inv.files.items()):
        if not rel.endswith(".py") or info.tree is None or info.kind in ("out", "history"):
            continue
        found = {name: lines for name, finder, _ in ROOT_SHAPES if (lines := finder(info.tree))}
        if found:
            out[rel] = found
    return out


def root_problems(derivations: dict[str, dict[str, list[int]]] | None,
                  exemptions: dict[str, dict[str, str]] | None = None) -> list[str]:
    """Расхождения правила «корень выводит один модуль» — строками.

    Отдельная функция, потому что её зовёт гейт, а считает инвентарь: так новую
    форму нельзя добавить в замер и забыть в гейте.

    `exemptions` — решения человека из артефакта: файл → ФОРМА → обоснование. Не
    список прощённых имён, а объявленные исключения по устройству, и сверяются они
    в обе стороны, как всё в этом гейте: исключение, которого больше нет в замере, —
    расхождение, его надо снять.

    Исключение даётся на форму, а не на файл целиком: обоснование покрывает одну
    форму, а прощение файла молчало бы и о любой другой. Единственное сегодняшнее
    исключение — рецепт зависимостей: ему нельзя импортировать канон по его же
    контракту, но если он однажды начнёт ещё и читать переменную, это второй канон,
    и гейт обязан сказать (обе головы круга 2, независимо).

    `derivations is None` значит «вызывающий о выводе корня не спрашивает»: тогда
    молчат обе стороны. Раньше молчала только первая, и незаданный замер печатал
    «исключение больше не выводит корень» про живое исключение — тот же
    перегруженный `None`, за который платили гейтом свежести карты (круг 9).
    """
    if derivations is None:
        return []
    hint = {name: text for name, _, text in ROOT_SHAPES}
    exempt = exemptions or {}
    out = []
    for rel, shapes in sorted(derivations.items()):
        if rel == ENV_ROOT_OWNER or not rel.startswith(ENV_ROOT_ENFORCED):
            continue
        for name, lines in sorted(shapes.items()):
            if name in exempt.get(rel, {}):
                continue
            out.append(f"{rel}:{','.join(map(str, lines))} {hint[name]} — "
                       f"взять корень у {ENV_ROOT_OWNER} (resolve_root / code_root), "
                       f"иначе копия разойдётся с каноном")
    for rel, shapes in sorted(exempt.items()):
        gone = sorted(set(shapes) - set(derivations.get(rel, {})))
        for name in gone:
            out.append(f"root_exemptions прощает {rel} форму «{name}», но замер её "
                       f"больше не находит — снять")
    return out


def report(inv: Inventory, *, env_var: str = "CHAROITE_ROOT",
           seams: tuple[str, ...] = ("LLM", "GraphSearch", "shared", "judge", "revise",
                                     "graph_dir", "resolve_root", "code_root", "harden_umask",
                                     "trim_log", "night_wait_cap")) -> str:
    """Факты о швах для постановки фазы: кто читает переменную корня (и стоит ли
    чтение выше вставки в `sys.path` — тогда переход на модуль корней требует
    переноса строки), кто выводит корень из положения файла, кто зовёт шов.
    Вывод — не оценка и не план: это замер, который бриф цитирует."""
    env: list[str] = []
    roots: list[str] = []
    seam_hits: dict[str, list[str]] = {}
    parsed = 0
    outside = 0
    unruled: list[str] = []
    for rel, info in sorted(inv.files.items()):
        if not rel.endswith(".py"):
            continue
        if info.kind in ("out", "history"):
            # «по политике» — только если так решило ПРАВИЛО; файл, до которого
            # правила не дотянулись, — не политика, а дыра в таблице, и о нём
            # надо сказать (Important DS круга 9). С №328 тот же случай красит
            # и гейт (`scan`), здесь он остаётся в замере — отчёт называет, гейт
            # требует решения.
            if decide(rel).by in ("rule", "shape"):
                outside += 1
            else:
                unruled.append(rel)
            continue
        if info.tree is None:
            continue                        # не разобрался — он уже в inv.problems, разделом ниже
        parsed += 1
        ev = module_events(info.tree, env_var)
        lines = ev.top_reads + ev.inner_reads
        if lines:
            note = ORDER_NOTES[ev.order](ev, info)
            if ev.inner_reads:
                note += f"; читается при вызове (строки {','.join(map(str, ev.inner_reads))}), не на импорте"
            env.append(f"- `{rel}`:{','.join(map(str, sorted(lines)))}{note}")
        fr = _file_roots(info.tree)
        if fr:
            roots.append(f"- `{rel}`:{','.join(map(str, fr))}")
        for name, hits in _calls(info.tree, seams).items():
            seam_hits.setdefault(name, []).append(f"`{rel}`:{','.join(map(str, hits))}")
    out = [f"# Замер швов (`scripts/layout_map.py --report`): python-модулей в области {parsed}, "
           f"вне области по правилу {outside}, без правила {len(unruled)}, "
           f"всего файлов под git {len(inv.files)}", ""]
    # Разделы — обходом `PROBLEM_KINDS` целиком, а не выборкой видов по месту:
    # недостоверный замер врёт ровно тем, ради чего заведён (Critical DS и GLM
    # круга 8), и о достоверности сказано даже когда с ней всё хорошо. Спорный
    # вид — свой раздел: такой файл в замер ВОШЁЛ (Critical GLM круга 9).
    первый = True
    for заголовок, свои in sections(inv.problems):
        out += ([] if первый else [""]) + [f"## {заголовок} ({len(свои)})", ""]
        out += [f"- {p.text}" for p in свои] or ["- нет"]
        первый = False
    if unruled:
        out += ["", f"## Python вне области, но и без правила — таблица их не знает ({len(unruled)})", ""]
        out += [f"- `{rel}`" for rel in unruled]
    # Заголовки и счётчики берутся из той же таблицы форм, что судит гейт: пока они
    # были литералами, замер описывал две формы, а гейт мог считать третью, и долг
    # по ней был невидим человеку (Important GLM круга 2).
    shape_hint = {name: text for name, _, text in ROOT_SHAPES}
    out += ["", f"## Кто выводит корень сам, форма «env» — {shape_hint['env']} ({len(env)})", ""]
    out += env or ["- нет"]
    out += ["", f"## Кто выводит корень сам, форма «file» — {shape_hint['file']} ({len(roots)})", ""]
    out += roots or ["- нет"]
    missing = [n for n, _, _ in ROOT_SHAPES if n not in ("env", "file")]
    if missing:
        # третья форма заведена в таблице, но секции ей никто не написал: замер обязан
        # сказать об этом вслух, а не молчать о долге, который гейт уже считает
        out += ["", f"## Формы без раздела в замере — {', '.join(missing)}", "",
                "- гейт их судит, а человек не видит: дописать раздел в report()"]
    out += ["", "## Точки сборки швов", ""]
    for name in seams:
        who = seam_hits.get(name, [])
        out.append(f"- **{name}** ({len(who)}): " + (", ".join(who) if who else "нет"))
    return "\n".join(out) + "\n"


def allowlist_edges(layout: dict) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in layout["allowed_edges"]}


def check(layout: dict, graph: dict[str, set[str]], scanned: Scan, execs: dict[str, str],
          repo: pathlib.Path | None = None, *, map_text: str | None = None,
          map_state: MapState = "present",
          roots: dict[str, dict[str, list[int]]] | None = None) -> list[str]:
    """Все расхождения раскладки с реальностью — строками; пусто = зелёный.
    Каждое множество сверяется в обе стороны. `map_state`: `present` — карта
    сверяется с `map_text` (если он передан; `None` значит «вызывающий о карте не
    спрашивает»); `missing` — карты нет, это расхождение; `skipped` — прогон её
    не писал и судить нечем. Четвёртого состояния нет: оно вело себя как
    `present`, а докстринг обещал обратное, и на этом держался главный гейт
    (Important GLM круга 9). `roots` — то же соглашение: `None` значит
    «вызывающий о выводе корня не спрашивает». Главный тракт спрашивает всегда,
    и это сторожит отдельный тест: правило, которое можно выключить забывчивостью
    вызывающего, — не правило."""
    if map_state not in MAP_STATES:
        raise LayoutError(f"неизвестное состояние карты: {map_state!r}")
    repo = repo or REPO
    problems: list[str] = list(scanned.problems)
    # Имя из таблицы без модуля в дереве — это ЛИБО переезд, ЛИБО удаление, и
    # сторож не знает, что именно. Прежнее сообщение знало только один ответ
    # («убрать из layout.json») — и этот ответ снимал охрану с переехавшего
    # кода (входной круг №328, обе головы; круг 1 по коду, GLM I2).
    кандидаты = move_candidates(graph, layout)
    # Две строки об одном имени — это две РАЗНЫЕ вещи: «в таблице есть имя без
    # модуля» и «модуль без слоя», и каждая требует своего действия. Раньше
    # вторая гасилась для всякого имени, названного кандидатом, — и в третьем
    # исходе `move_candidates` (одно удалили, другое завели с тем же хвостом)
    # гейт молчал о новом модуле, потому что «похоже на переезд». Докстринг
    # там объявляет исход неуверенным — гейт не вправе быть увереннее своего
    # источника (круг 2 по коду №328, DS I4; правило проекта №282).
    for m in sorted(unassigned(graph, layout)):
        problems.append(f"модуль {m} не отнесён ни к одному слою в {LAYOUT.name}")
    # Факт, подсказка и действие — отдельными строками: при переезде пакета имён
    # десяток, и ворох придаточных в каждой строке хоронит остальные красные
    # (круг 2 по коду №328, DS M7).
    for m, куда in sorted(кандидаты.items()):
        problems.append(f"в таблице слоёв есть {m}, а модуля с таким именем в дереве нет")
        if куда:
            problems.append(f"  похоже на переезд: {', '.join(куда)} (слой не наследуется — решить заново)")
        problems.append(f"  удалён — снять строку из {LAYOUT.name}; переехал — перенести ключ"
                        f" и записать решение о слое")
    allow = allowlist_edges(layout)
    viol = set(violations(graph, layout))
    lay = layer_of(layout)
    for a, b in sorted(viol - allow):
        problems.append(f"новое ребро против стрелок: {a} ({lay[a]}) → {b} ({lay[b]}) — "
                        f"развязать или внести в allowed_edges с карточкой")
    for a, b in sorted(allow - viol):
        problems.append(f"allowed_edges содержит {a} → {b}, но такого ребра против стрелок больше нет — снять")
    manual = layout["manual_entry_points"]
    named = set(scanned.mentions)
    # точки входа = исполняемые файлы; каждая названа кодом или объявлена ручной — и наоборот
    for path in sorted(set(execs) - named - set(manual)):
        problems.append(f"исполняемый файл {path} никто не зовёт из кода — объявить в manual_entry_points "
                        f"с обоснованием или найти вызывающего")
    for path in sorted(set(manual) - set(execs)):
        problems.append(f"manual_entry_points объявляет {path}, но это не исполняемый файл — снять")
    for path in sorted(set(manual) & named):
        problems.append(f"{path} объявлен ручным, но его зовёт код ({', '.join(sorted(scanned.mentions[path]))}) — снять из manual")
    # названные пути обязаны существовать — в коде и в прозе
    for path, who in sorted(scanned.mentions.items()):
        if not (repo / path).is_file():
            problems.append(f"путь {path} назван в коде ({', '.join(sorted(who))}), а файла нет — переезд без правки вызывающих")
    for path, who in sorted(scanned.prose.items()):
        if not (repo / path).is_file():
            problems.append(f"путь {path} назван в документации или конфиге ({', '.join(sorted(who))}), а файла нет")
    # корень данных выводит один модуль: копия правил разбора четырежды отдрейфовала
    # от канона, на который сама же ссылалась в комментарии (№321). Инвариант, а не
    # список прощённых имён: прощённых имён нет, есть область, где правило уже в силе.
    # Форм вывода несколько (`ROOT_SHAPES`) — первая редакция правила считала только
    # чтение переменной и пропускала тот самый дефект, ради которого заводилась
    problems += root_problems(roots, layout["root_exemptions"])
    # три состояния карты, а не перегруженный None: свежая / отстала / её нет
    # (Minor GLM круга 7: при пропавшей карте `--check` выходил зелёным)
    if map_state == "missing":
        problems.append(f"{MAP.name} нет — перегенерировать: scripts/layout_map.py")
    elif map_text is not None and map_text != render_map(layout, graph, scanned, execs):
        problems.append(f"{MAP.name} отстал от кода — перегенерировать: scripts/layout_map.py")
    return problems


def regen(layout: dict, graph: dict[str, set[str]]) -> tuple[dict, list[tuple[str, str]]]:
    """Переписать allowlist по факту, сохранив карточки; новые рёбра без
    карточки — вернуть вызывающему, чтобы напечатать (Minor DS круга 2: regen
    писал артефакт, который следующая загрузка отвергала трейсбеком). Штамп
    `generated` меняется только вместе с allowlist (Minor DS круга 3).
    Слои, поправки и ручные точки входа — решения, их regen не трогает.

    Внутри записи ребра то же правило: замер владеет только `from`/`to`
    (`MEASURED_EDGE_FIELDS`), остальные поля — решение человека и переносятся
    как есть. Раньше запись собиралась из трёх полей заново, и любое
    добавленное поле молча исчезало при первом же `--regen`, пока гейт
    оставался зелёным: обещание докстринга выше не выполнялось ровно для
    рёбер (обе головы входного круга №325 независимо, 20.09)."""
    kept = {(e["from"], e["to"]): e for e in layout["allowed_edges"]}
    fresh = violations(graph, layout)
    edges = []
    for a, b in fresh:
        prev = kept.get((a, b), {})
        decided = {k: v for k, v in prev.items() if k not in MEASURED_EDGE_FIELDS and k != "ticket"}
        edges.append({"from": a, "to": b, "ticket": prev.get("ticket", ""), **decided})
    if edges != layout["allowed_edges"]:
        layout["allowed_edges"] = edges
        layout["generated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return layout, [(a, b) for a, b in fresh if not kept.get((a, b), {}).get("ticket")]


def render_map(layout: dict, graph: dict[str, set[str]], scanned: Scan, execs: dict[str, str]) -> str:
    """Карта для людей — из тех же данных, что и гейт; хранится в git и
    проверяется на свежесть. Без полной смежности модулей и счётчиков строк:
    они меняются от любой правки и шумели бы в каждом PR (критика DS круга 2)."""
    lay = layer_of(layout)
    out = ["# Раскладка кода Чароита (генерируется `scripts/layout_map.py`, руками не править)", "",
           f"Источник истины — `docs/design/layout.json`; гейт — `tests/test_import_boundaries.py`. "
           f"Снимок allowlist: {layout.get('generated', '?')}. Модулей {len(graph)}.",
           "", "## Слои и направление стрелок", ""]
    by_layer: dict[str, list[str]] = {layer: [] for layer in layout["order"]}
    for m, layer in lay.items():
        by_layer[layer].append(m)
    for layer in layout["order"]:
        deps = ", ".join(layout["allowed"].get(layer, [])) or "—"
        mods = sorted(by_layer[layer])
        out.append(f"- **{layer}** (зависит от: {deps}; модулей {len(mods)}): " + ", ".join(f"`{m}`" for m in mods))
    out += ["", "## Поправки к таблице брифа (с обоснованием)", ""]
    for m, ov in sorted(layout["layer_overrides"].items()):
        out.append(f"- `{m}` → {ov['layer']}: {ov['why']}")
    out += ["", "## Корень выводит один модуль — объявленные исключения", ""]
    out.append(f"Канон: `{ENV_ROOT_OWNER}`. Область правила: "
               + ", ".join(f"`{p}`" for p in ENV_ROOT_ENFORCED) + ".")
    out.append("")
    for rel, shapes in sorted(layout["root_exemptions"].items()):
        for name, why in sorted(shapes.items()):
            out.append(f"- `{rel}`, форма «{name}»: {why}")
    out += ["", "## Рёбра против стрелок (allowlist с карточками на снятие)", ""]
    viol = violations(graph, layout)
    tickets = {(e["from"], e["to"]): e.get("ticket", "") for e in layout["allowed_edges"]}
    out.append(f"Всего {len(viol)}.")
    out.append("")
    for a, b in viol:
        out.append(f"- `{a}` ({lay[a]}) → `{b}` ({lay[b]}) — {tickets.get((a, b)) or 'без карточки'}")
    out += ["", "## Точки входа — исполняемые файлы (кто зовёт из кода)", ""]
    for path in sorted(execs):
        who = ", ".join(sorted(scanned.mentions.get(path, ())))
        manual = layout["manual_entry_points"].get(path)
        out.append(f"- `{path}` ← {who or ('ручной запуск: ' + manual if manual else 'никто')}")
    out += ["", "## Пути, названные кодом, но не исполняемые (подсказки и сообщения)", ""]
    for path, who in sorted(scanned.mentions.items()):
        if path not in execs:
            out.append(f"- `{path}` ← {', '.join(sorted(who))}")
    out += ["", "## Пути, названные в документации и конфигах", ""]
    for path, who in sorted(scanned.prose.items()):
        out.append(f"- `{path}` ← {', '.join(sorted(who))}")
    out += ["", "## Голые имена без цели в репозитории (чужие или порождаемые скрипты — справка)", ""]
    for name, who in sorted(scanned.loose.items()):
        out.append(f"- `{name}` ← {', '.join(sorted(who))}")
    out += ["", "## Область замера (таблица KINDS сторожа; кандидаты в точки входа — всегда код)", ""]
    for prefix, kind, scope, why in KINDS:
        out.append(f"- `{prefix}` — {kind} ({scope}): {why}")
    return "\n".join(out) + "\n"


#: Что понимает командная строка. Больше ничего она не понимает — и говорит
#: об этом вслух, вместо того чтобы выполнить не тот режим.
РЕЖИМЫ = ("--report", "--regen", "--check")


def main(argv: list[str] | None = None) -> int:
    """Один выходной тракт для всех режимов: расхождения считаются одним
    `check()` и печатаются одним циклом; режим меняет только то, что пишется
    на диск, и код выхода (Important DS круга 3: `--regen` выходил зелёным при
    красном гейте). Ребро без карточки блокирует запись артефакта и карты —
    загрузка такой артефакт отвергнет (Critical DS круга 4), но отчёт о прочих
    расхождениях печатается тем же прогоном (Important DS круга 5)."""
    args = sys.argv[1:] if argv is None else argv
    # режимы — списком: разбора аргументов тут нет, и незнакомое слово молча
    # игнорировалось. «Только посмотреть» с опечаткой в флаге писало карту на
    # диск (круг 6 по коду №329, DS M4)
    чужие = [a for a in args if a not in РЕЖИМЫ]
    if чужие:
        print(f"✗ неизвестные аргументы: {' '.join(чужие)}; режимы: {' '.join(sorted(РЕЖИМЫ))}")
        return 2
    inv = inventory(REPO)
    if "--report" in args and "--check" not in args:
        # замер читает только код: артефакт ему не нужен и не должен его хоронить
        # (Critical GLM и Important DS круга 9 — в середине переделки артефакт
        # правят руками, и битый артефакт убивал замер целиком)
        print(report(inv), end="")
        # замер, которому нельзя верить, — не замер; спорный вид достоверности
        # не отнимает. Что именно её отнимает, знает объявление вида в
        # PROBLEM_KINDS, а не литерал по месту.
        return 1 if any(PROBLEM_KINDS[p.kind].invalidates for p in inv.problems) else 0
    try:
        layout = load_layout(LAYOUT)
    except LayoutError as e:
        print(f"✗ {LAYOUT.relative_to(REPO)}: {e}")
        return 1
    graph = import_graph(inv)
    scanned = scan(inv)
    execs = executables(inv)
    blocked: list[str] = []
    if "--regen" in args:
        # черновик отдельно от загруженного: при блокировке отчёт идёт по тому, что лежит
        # на диске, а не по несохранённой правке (Important DS круга 6)
        fresh, unticketed = regen(json.loads(json.dumps(layout)), graph)
        blocked = [f"ребро {a} → {b} без карточки — вписать ticket в allowed_edges; артефакт и карта не записаны"
                   for a, b in unticketed]
        if not blocked:
            layout = fresh
            LAYOUT.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{LAYOUT.relative_to(REPO)}: allowlist {len(layout['allowed_edges'])} рёбер")
    if "--check" not in args and not blocked:
        MAP.write_text(render_map(layout, graph, scanned, execs), encoding="utf-8")
        print(f"карта: {MAP.relative_to(REPO)}")
    # свежесть карты — отчёт о записанном артефакте; при блокировке карта не писалась,
    # и судить о ней нечем (состояние `skipped`, а не «свежая»)
    if blocked:
        map_text, map_state = None, "skipped"
    elif MAP.exists():
        map_text, map_state = MAP.read_text(encoding="utf-8"), "present"
    else:
        map_text, map_state = None, "missing"
    problems = blocked + check(layout, graph, scanned, execs, map_text=map_text, map_state=map_state,
                               roots=root_derivations(inv))
    for p in problems:
        print("✗", p)
    print("раскладка совпадает с кодом" if not problems else f"расхождений: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
