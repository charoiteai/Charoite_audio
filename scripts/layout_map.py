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
- точки входа = исполняемые файлы репозитория (скрипты `scripts/*`, `app/*.sh`,
  `*.sh` в корне, модули `src/*.py` с настоящим гвардом `__main__` по AST);
  каждая либо названа кодом, либо объявлена ручной с обоснованием; названные
  кодом библиотеки точками входа НЕ являются (Critical DS и GLM круга 2);
- названные пути: всё, что код (Swift, shell, yml, python-литералы без
  докстрингов) и проза (документация, конфиги, toml) называют как путь к
  исполняемому файлу, обязано существовать — подсказка человеку и инструкция
  в README не должны врать после переезда; голое имя `.sh` — путь, только
  если оно резолвится в единственный свой скрипт, иначе это справка на карте.

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
from typing import NamedTuple

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
LAYOUT = REPO / "docs" / "design" / "layout.json"
MAP = REPO / "docs" / "design" / "layout.md"

#: Что считается исполняемым файлом: скрипты по расположению, модули `src/` — по гварду.
ENTRY_TARGETS = ("src/*.py", "scripts/*.py", "scripts/*.sh", "app/*.sh", "*.sh")

#: Классификация файла — одно решение в одном месте. Исполняемый файл по
#: ENTRY_TARGETS — всегда `code` (Critical DS круга 4: корневой `x.sh` был целью
#: по одной таблице и `out` по другой — невидим обеим); дальше первый совпавший
#: префикс (каталог с «/» или точное имя). Виды: `code` — упоминание пути = связь
#: «кто зовёт»; `prose` — названный путь обязан существовать; `history` —
#: датированные снимки (ревью, релизные заметки, посты) описывают код своего дня
#: и после переезда не правятся; `out` — не читается. Внутри `code` решает
#: суффикс: код читается без комментариев, документация рядом с кодом — как
#: проза, остальное — `out`. Без совпадения: проза по суффиксу, иначе `out`
#: (Important DS круга 3: код вне `app/` читался как проза сырым текстом,
#: .kts/.json не читались вовсе). Каждое правило обязано действовать хотя бы на
#: один файл дерева — это проверяет гейт.
KINDS: tuple[tuple[str, str, str], ...] = (
    ("docs/design/layout.md", "out", "карта — производная замера, не источник"),
    ("tests/", "out", "тесты строят синтетические деревья: пути в них — не факты о репозитории"),
    ("app/Tests/", "out", "Swift-тесты приложения: те же выдуманные пути"),
    ("docs/reviews/", "history", "датированные ревью описывают код своего дня — после переезда не правятся"),
    ("devlog/_posts/", "history", "датированные посты — снимок своего дня"),
    ("CHANGELOG.md", "history", "релизные заметки — снимок своего дня, записи о вышедших версиях не правятся"),
    ("app-ios/", "out", "телефон python и shell не запускает — пути там только в тексте"),
    ("app-android/", "out", "телефон python и shell не запускает — пути там только в тексте"),
    ("app/build/", "out", "сборка"),
    ("app/.build/", "out", "сборка"),
    (".build/", "out", "сборка"),
    ("build/", "out", "сборка"),
    (".venv/", "out", "окружение"),
    ("node_modules/", "out", "чужой код"),
    (".git/", "out", "служебный каталог git"),
    ("app/", "code", "приложение зовёт python и shell"),
    ("scripts/", "code", "скрипты зовут друг друга и модули"),
    ("src/", "code", "модули зовут скрипты и подсказывают пути человеку"),
    (".github/", "code", "workflow CI — источник запуска"),
    (".pre-commit-config.yaml", "code", "хуки — источник запуска"),
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


class Inventory(NamedTuple):
    files: dict[str, FileInfo]
    problems: list[str]             # файл не читается / не разбирается — факт, не исключение


class Scan(NamedTuple):
    """Замер: связи «кто зовёт» из кода, названные пути из прозы, голые имена
    без цели (справка на карте: чужой или порождаемый скрипт — не гейт),
    проблемы инвентаря и сканера."""
    mentions: dict[str, set[str]]       # путь → файлы кода, которые его называют
    prose: dict[str, set[str]]          # путь → документы/конфиги, которые его называют
    loose: dict[str, set[str]]          # голое имя без цели в репозитории → кто его называет
    problems: list[str]


_SCHEMA = {"order": list, "brief_layers": dict, "allowed": dict, "layer_overrides": dict,
           "allowed_edges": list, "manual_entry_points": dict, "generated": str}
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")


def load_layout(path: pathlib.Path = LAYOUT) -> dict:
    """Строгая загрузка: любой дефект артефакта — `LayoutError`, единственный
    тип, который ловит `main`. Сначала паспорт схемы (файл читается, ключи на
    месте и нужного типа — Critical DS круга 3: битый JSON и пропавший ключ
    падали трейсбеком), потом инварианты: слои попарно не пересекаются,
    `order`/`allowed` согласованы, стрелки вниз, у поправок слоя, ручных точек
    входа и рёбер allowlist есть обоснование или карточка."""
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
        if not _is_target_path(path_) or not isinstance(why, str) or not why:
            raise LayoutError(f"ручная точка входа {path_}: не путь к исполняемому файлу или пустое why")
    return layout


def _is_target_path(rel: str) -> bool:
    return any(pathlib.PurePosixPath(rel).match(p) and rel.count("/") == p.count("/") for p in ENTRY_TARGETS)


# ---------------------------------------------------------------- инвентарь

def kind_of(rel: str) -> str:
    """Вид файла — единственное место, где это решается: исполняемый по
    ENTRY_TARGETS — код, остальное по таблице KINDS."""
    if _is_target_path(rel):
        return "code"
    hit = None
    for prefix, kind, _why in KINDS:
        if rel == prefix or (prefix.endswith("/") and rel.startswith(prefix)):
            hit = kind
            break
    if hit in ("out", "history"):
        return hit
    if hit == "code":
        if rel.endswith(CODE_SUFFIXES):
            return hit
        return "prose" if rel.endswith(PROSE_SUFFIXES) else "out"
    return "prose" if rel.endswith(PROSE_SUFFIXES) else "out"


def _pruned(rel_dir: str) -> bool:
    """Обход без git: каталоги вида `out` не открываются вовсе."""
    return any(kind == "out" and prefix.endswith("/") and rel_dir + "/" == prefix for prefix, kind, _ in KINDS)


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


def inventory(repo: pathlib.Path = REPO) -> Inventory:
    """Один обход, одна классификация, одно чтение и один разбор на файл.
    Всё дальнейшее (`import_graph`, `executables`, `scan`) — чистые функции
    от инвентаря."""
    files: dict[str, FileInfo] = {}
    problems: list[str] = []
    for rel in _files(repo):
        kind = kind_of(rel)
        if kind in ("out", "history"):
            files[rel] = FileInfo(kind, (), None, None)
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            problems.append(f"{rel} не читается ({e.strerror or e}) — упоминания из него не собраны")
            files[rel] = FileInfo(kind, (), None, None)
            continue
        tree = None
        if kind == "prose":
            hays: tuple[str, ...] = (text,)
        elif rel.endswith(".py"):
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError as e:
                problems.append(f"{rel} не разбирается ({e.msg}, строка {e.lineno}) — "
                                f"упоминания и импорты из него не собраны")
                files[rel] = FileInfo(kind, (), None, None)
                continue
            hays = tuple(_python_literals(tree))
        elif rel.endswith(".swift"):
            hays = (_strip_comments(text, "//"),)
        else:
            hays = (_strip_comments(text, "#"),)
        executable = None
        if kind == "code" and _is_target_path(rel):
            if rel.startswith("src/"):
                executable = "модуль с гвардом __main__" if tree is not None and _has_main_guard(tree) else None
            else:
                executable = "скрипт"
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
    """Исполняемые файлы: путь → почему исполняемый. Библиотека `src/` без
    гварда исполняемой не является, что бы про неё ни говорили подсказки."""
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
    problems = list(inv.problems)
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


def allowlist_edges(layout: dict) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in layout["allowed_edges"]}


def check(layout: dict, graph: dict[str, set[str]], scanned: Scan, execs: dict[str, str],
          repo: pathlib.Path = REPO, *, map_text: str | None = None) -> list[str]:
    """Все расхождения раскладки с реальностью — строками; пусто = зелёный.
    Каждое множество сверяется в обе стороны."""
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
    if map_text is not None and map_text != render_map(layout, graph, scanned, execs):
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
    out += ["", "## Голые имена без цели в репозитории (чужие или порождаемые скрипты; не гейт)", ""]
    for name, who in sorted(scanned.loose.items()):
        out.append(f"- `{name}` ← {', '.join(sorted(who))}")
    out += ["", "## Область замера (таблица KINDS сторожа)", ""]
    for prefix, kind, why in KINDS:
        out.append(f"- `{prefix}` — {kind}: {why}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Один выходной тракт для всех режимов: расхождения считаются одним
    `check()` и печатаются одним циклом; режим меняет только то, что пишется
    на диск, и код выхода (Important DS круга 3: `--regen` выходил зелёным при
    красном гейте)."""
    args = sys.argv[1:] if argv is None else argv
    try:
        layout = load_layout()
    except LayoutError as e:
        print(f"✗ {LAYOUT.relative_to(REPO)}: {e}")
        return 1
    inv = inventory()
    graph = import_graph(inv)
    scanned = scan(inv)
    execs = executables(inv)
    if "--regen" in args:
        layout, unticketed = regen(layout, graph)
        if unticketed:
            # гейт блокирующий: артефакт с пустой карточкой загрузка отвергнет, поэтому он не
            # пишется вовсе (Critical DS круга 4: «напечатать и продолжить — не проверка»)
            for a, b in unticketed:
                print(f"✗ ребро {a} → {b} без карточки — вписать ticket в allowed_edges; артефакт не записан")
            return 1
        LAYOUT.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{LAYOUT.relative_to(REPO)}: allowlist {len(layout['allowed_edges'])} рёбер")
    if "--check" not in args:
        MAP.write_text(render_map(layout, graph, scanned, execs), encoding="utf-8")
        print(f"карта: {MAP.relative_to(REPO)}")
    map_text = MAP.read_text(encoding="utf-8") if MAP.exists() else None
    problems = check(layout, graph, scanned, execs, map_text=map_text)
    for p in problems:
        print("✗", p)
    print("раскладка совпадает с кодом" if not problems else f"расхождений: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
