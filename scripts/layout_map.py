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

#: Кандидаты в точки входа по расположению; исполняемым кандидата делает гвард
#: `__main__` (python) или сам факт shell-скрипта (Minor DS круга 5: «цель» читалась
#: как «исполняемый», а `src/graph_search.py` — кандидат, но не исполняемый).
ENTRY_CANDIDATES = ("src/*.py", "scripts/*.py", "scripts/*.sh", "app/*.sh", "*.sh")

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
_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(?:(?:src|scripts|app)/[A-Za-z_][A-Za-z0-9_]*\.(?:py|sh)"
                   r"|(?:\./)?[A-Za-z_][A-Za-z0-9_]*\.sh)(?![A-Za-z0-9_]|\.[A-Za-z0-9_])")


class LayoutError(ValueError):
    """Артефакт раскладки невалиден: правится руками, инвариант — при загрузке."""


class FileInfo(NamedTuple):
    """Один файл инвентаря: вид, что читает токенизатор, дерево python, исполняемость."""
    kind: str                       # code | prose | history | out
    haystacks: tuple[str, ...]      # литералы python / текст без комментариев / сырая проза
    tree: ast.Module | None         # только у разобранного .py вида code
    executable: str | None          # почему исполняемый, иначе None


class Problem(NamedTuple):
    """Проблема инвентаря как значение, а не голая строка: потребители должны
    отличать «корпус неполон» от «политика конфликтует» (Critical GLM круга 9 —
    одна и та же строка печаталась как «не вошло в замер» у файла, который в
    замер вошёл). `kind`: `parse` / `read` — файла в замере нет; `conflict` —
    файл в замере, но его вид спорный."""
    kind: str
    text: str

    def __str__(self) -> str:
        return self.text


class Inventory(NamedTuple):
    files: dict[str, FileInfo]
    problems: list[Problem]         # файл не читается / не разбирается / спорный вид — факт, не исключение


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
           "allowed_edges": list, "manual_entry_points": dict, "generated": str}
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
    for path_, why in layout["manual_entry_points"].items():
        if not _is_candidate(path_) or not isinstance(why, str) or not why:
            raise LayoutError(f"ручная точка входа {path_}: не путь к исполняемому файлу или пустое why")
    return layout


def _is_candidate(rel: str) -> bool:
    return any(pathlib.PurePosixPath(rel).match(p) and rel.count("/") == p.count("/") for p in ENTRY_CANDIDATES)


# ---------------------------------------------------------------- инвентарь

class Decision(NamedTuple):
    kind: str               # code | prose | history | out
    by: str                 # "candidate" | "rule" | "default"
    rule: int | None        # индекс правила KINDS, накрывающего путь (и у кандидата тоже)
    conflict: str | None    # правило таблицы, желающее кандидату не-code — красный гейт


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
    if _is_candidate(rel):
        conflict = KINDS[i][0] if i is not None and KINDS[i][1] != "code" else None
        return Decision("code", "candidate", i, conflict)
    if i is None:
        return Decision(_by_suffix("", rel), "default", None, None)
    kind = KINDS[i][1]
    return Decision(kind if kind in ("out", "history") else _by_suffix(kind, rel), "rule", i, None)


def kind_of(rel: str) -> str:
    return decide(rel).kind


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
    return Inventory(files, problems)


# ---------------------------------------------------------------- замеры от инвентаря

def modules(inv: Inventory) -> set[str]:
    return {pathlib.PurePosixPath(rel).stem for rel in inv.files
            if rel.startswith("src/") and rel.endswith(".py") and rel.count("/") == 1}


def import_graph(inv: Inventory) -> dict[str, set[str]]:
    """Модуль → модули репо, которые он импортирует. Обход всех узлов Import
    (и внутри функций: `llm.py` импортирует `llm_health` лениво). Модуль без
    дерева (не разобрался) — уже проблема инвентаря, здесь просто без рёбер."""
    mods = modules(inv)
    graph: dict[str, set[str]] = {m: set() for m in mods}
    for rel, info in inv.files.items():
        stem = pathlib.PurePosixPath(rel).stem
        if stem not in mods or not rel.startswith("src/") or rel.count("/") != 1 or info.tree is None:
            continue
        for node in ast.walk(info.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in mods and top != stem:
                        graph[stem].add(top)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                top = node.module.split(".")[0]
                if top in mods and top != stem:
                    graph[stem].add(top)
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
    """Имена в таблице слоёв, которых в `src/` больше нет."""
    return sorted(m for m in layer_of(layout) if m not in graph)


def _tokens(text: str) -> set[str]:
    out = set()
    for m in _PATH.finditer(text):
        tok = m.group(0)
        out.add(tok if tok.startswith(("src/", "scripts/", "app/")) else tok.removeprefix("./"))
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


def _file_roots(tree: ast.Module) -> list[int]:
    """Строки с цепочкой `__file__ … .parent.parent` — корень, выведенный из
    положения файла. Одна ступень (`sys.path`-шим) не считается."""
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr == "parent"
                and isinstance(node.value, ast.Attribute) and node.value.attr == "parent"
                and "__file__" in ast.unparse(node)):
            out.append(node.lineno)
    return sorted(set(out))


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
            # правила не дотянулись (будущий `packages/…`), — не политика, а дыра
            # в таблице, и о нём надо сказать (Important DS круга 9)
            if decide(rel).by == "rule":
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
    unread = [p for p in inv.problems if p.kind in ("parse", "read")]
    disputed = [p for p in inv.problems if p.kind == "conflict"]
    out = [f"# Замер швов (`scripts/layout_map.py --report`): python-модулей в области {parsed}, "
           f"вне области по правилу {outside}, без правила {len(unruled)}, "
           f"всего файлов под git {len(inv.files)}", ""]
    # непрочитанное — первым разделом: замер, построенный на неполном корпусе, врёт
    # ровно тем, ради чего он заведён (Critical DS и GLM круга 8, независимо).
    # Спорный вид — отдельный раздел: такой файл в замер ВОШЁЛ (Critical GLM круга 9).
    out += [f"## Не прочитано — этих файлов в замере нет ({len(unread)})", ""]
    out += [f"- {p.text}" for p in unread] or ["- нет"]
    if unruled:
        out += ["", f"## Python вне области, но и без правила — таблица их не знает ({len(unruled)})", ""]
        out += [f"- `{rel}`" for rel in unruled]
    if disputed:
        out += ["", f"## Спорный вид — файлы в замере есть, но политика конфликтует ({len(disputed)})", ""]
        out += [f"- {p.text}" for p in disputed]
    out += ["", f"## Читатели переменной {env_var} (формы: {', '.join(ENV_READ_FORMS)}) — {len(env)}", ""]
    out += env or ["- нет"]
    out += ["", f"## Корень из положения файла — цепочка `__file__ … .parent.parent` ({len(roots)})", ""]
    out += roots or ["- нет"]
    out += ["", "## Точки сборки швов", ""]
    for name in seams:
        who = seam_hits.get(name, [])
        out.append(f"- **{name}** ({len(who)}): " + (", ".join(who) if who else "нет"))
    return "\n".join(out) + "\n"


def allowlist_edges(layout: dict) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in layout["allowed_edges"]}


def check(layout: dict, graph: dict[str, set[str]], scanned: Scan, execs: dict[str, str],
          repo: pathlib.Path | None = None, *, map_text: str | None = None,
          map_state: MapState = "present") -> list[str]:
    """Все расхождения раскладки с реальностью — строками; пусто = зелёный.
    Каждое множество сверяется в обе стороны. `map_state`: `present` — карта
    сверяется с `map_text` (если он передан; `None` значит «вызывающий о карте не
    спрашивает»); `missing` — карты нет, это расхождение; `skipped` — прогон её
    не писал и судить нечем. Четвёртого состояния нет: оно вело себя как
    `present`, а докстринг обещал обратное, и на этом держался главный гейт
    (Important GLM круга 9)."""
    if map_state not in MAP_STATES:
        raise LayoutError(f"неизвестное состояние карты: {map_state!r}")
    repo = repo or REPO
    problems: list[str] = list(scanned.problems)
    for m in unassigned(graph, layout):
        problems.append(f"модуль src/{m}.py не отнесён ни к одному слою в {LAYOUT.name}")
    for m in stale_layers(graph, layout):
        problems.append(f"в таблице слоёв есть {m}, а src/{m}.py нет — убрать из {LAYOUT.name}")
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
    Слои, поправки и ручные точки входа — решения, их regen не трогает."""
    tickets = {(e["from"], e["to"]): e.get("ticket", "") for e in layout["allowed_edges"]}
    fresh = violations(graph, layout)
    edges = [{"from": a, "to": b, "ticket": tickets.get((a, b), "")} for a, b in fresh]
    if edges != layout["allowed_edges"]:
        layout["allowed_edges"] = edges
        layout["generated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return layout, [(a, b) for a, b in fresh if not tickets.get((a, b))]


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


def main(argv: list[str] | None = None) -> int:
    """Один выходной тракт для всех режимов: расхождения считаются одним
    `check()` и печатаются одним циклом; режим меняет только то, что пишется
    на диск, и код выхода (Important DS круга 3: `--regen` выходил зелёным при
    красном гейте). Ребро без карточки блокирует запись артефакта и карты —
    загрузка такой артефакт отвергнет (Critical DS круга 4), но отчёт о прочих
    расхождениях печатается тем же прогоном (Important DS круга 5)."""
    args = sys.argv[1:] if argv is None else argv
    inv = inventory(REPO)
    if "--report" in args and "--check" not in args:
        # замер читает только код: артефакт ему не нужен и не должен его хоронить
        # (Critical GLM и Important DS круга 9 — в середине переделки артефакт
        # правят руками, и битый артефакт убивал замер целиком)
        print(report(inv), end="")
        # замер на неполном корпусе — не замер; спорный вид корпус не сокращает
        return 1 if any(p.kind in ("parse", "read") for p in inv.problems) else 0
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
    problems = blocked + check(layout, graph, scanned, execs, map_text=map_text, map_state=map_state)
    for p in problems:
        print("✗", p)
    print("раскладка совпадает с кодом" if not problems else f"расхождений: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
