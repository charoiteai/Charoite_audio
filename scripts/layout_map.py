#!/usr/bin/env python3
"""Раскладка кода: слои, рёбра импортов, точки входа — один машинный артефакт.

Фаза 0 разбиения `src/` на пакеты (№320; архитектурный круг 19.09, DS и GLM):
границы слоёв в проекте держались памятью автора и разовым замером. Здесь —
единственный источник истины `docs/design/layout.json` (таблица слоёв из брифа
владельца + поправки с обоснованием, направление стрелок, allowlist
существующих рёбер против стрелок, объявленные точки входа) и генератор карты
`docs/design/layout.md` из него и кода. Гейт — `tests/test_import_boundaries.py`:
читает тот же файл и сверяет с реальностью.

Не import-linter: он требует импортируемый пакет, а плоский `src/` из модулей,
импортирующих друг друга короткими именами, пакетом не является; и в проекте
уже есть механизм этого класса — AST-сторожа тестом (`test_cloud_call_sites`,
`test_charoite_paths`).

Точки входа — исполняемые файлы репозитория (`src/*.py`, `scripts/*.py`,
`scripts/*.sh`), которые кто-то упоминает по пути: Swift, shell, workflow CI,
python. Переезд файла без правки упоминающих ломал бы запуск молча (Critical DS
круга по пакетам). Упоминания собираются из КОДА, не из прозы (выходной круг по
#594): python — строковые литералы через AST без докстрингов (путь может быть
подстрокой: подсказка человеку «python3 scripts/doctor.py» тоже не должна
врать после переезда), Swift/shell/yml — текст без комментариев. Ложная запись
от `echo` в shell — объявленная цена; фантом из докстринга — нет: он держал бы
запись в инвентаре после удаления настоящего вызова.

Запуск:
    .venv/bin/python scripts/layout_map.py            # карта в docs/design/layout.md
    .venv/bin/python scripts/layout_map.py --check    # то же, что тест, кодом выхода
    .venv/bin/python scripts/layout_map.py --regen    # allowlist и точки входа по факту + карта
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import os
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
LAYOUT = REPO / "docs" / "design" / "layout.json"
MAP = REPO / "docs" / "design" / "layout.md"

# Что считается точкой входа: исполняемые файлы репозитория по этим шаблонам.
ENTRY_TARGETS = ("src/*.py", "scripts/*.py", "scripts/*.sh")
# Где ищем упоминания по пути (источники запуска). Каталоги обходятся рекурсивно;
# `EXCLUDE` — тесты, документация, сборка: там пути упоминаются, но не запускаются.
SOURCE_ROOTS = ("app", "scripts", "src", ".github", ".pre-commit-config.yaml")
SOURCE_SUFFIXES = (".swift", ".sh", ".py", ".yml", ".yaml", ".plist")
# конфиги и логи упоминают модули в комментариях и не запускают их
EXCLUDE = ("app/Tests", "app/build", "app/.build", "tests", "docs", "config", "logs",
           "build", ".build", "node_modules", ".venv")
_PATH = re.compile(r"(?<![A-Za-z0-9_.-])((?:src|scripts)/[A-Za-z_][A-Za-z0-9_]*\.(?:py|sh))(?![A-Za-z0-9_.])")


class LayoutError(ValueError):
    """Артефакт раскладки невалиден: правится руками, инвариант — при загрузке."""


def load_layout(path: pathlib.Path = LAYOUT) -> dict:
    """Загрузка со строгой проверкой: слои попарно не пересекаются, `order` и
    `allowed` согласованы, стрелки только вниз, у каждого allowlist-ребра и у
    каждой поправки к брифу есть обоснование (Critical GLM и Important DS по
    #594: дубль модуля в двух слоях или перенос слоя одной строкой без
    причины легализовал бы ребро против стрелок молча)."""
    layout = json.loads(path.read_text(encoding="utf-8"))
    order = layout["order"]
    if set(order) != set(layout["brief_layers"]) or set(order) != set(layout["allowed"]):
        raise LayoutError("order, brief_layers и allowed называют разные слои")
    seen: dict[str, str] = {}
    for layer, mods in layout["brief_layers"].items():
        for m in mods:
            if m in seen:
                raise LayoutError(f"модуль {m} в двух слоях: {seen[m]} и {layer}")
            seen[m] = layer
    for m, ov in layout["layer_overrides"].items():
        if ov.get("layer") not in order or not ov.get("why"):
            raise LayoutError(f"поправка слоя {m}: нужен layer из order и непустое why")
    for layer, deps in layout["allowed"].items():
        if any(order.index(d) >= order.index(layer) for d in deps):
            raise LayoutError(f"{layer} зависит не вниз: {deps}")
    for e in layout["allowed_edges"]:
        if not e.get("ticket"):
            raise LayoutError(f"ребро {e.get('from')} → {e.get('to')} без карточки")
    for path_, meta in layout["entry_points"].items():
        if not _PATH.fullmatch(path_) or meta.get("manual") not in (True, False, None):
            raise LayoutError(f"точка входа {path_}: не путь к исполняемому файлу или кривое поле manual")
    return layout


def modules(src: pathlib.Path = SRC) -> set[str]:
    return {p.stem for p in src.glob("*.py")}


def import_graph(src: pathlib.Path = SRC) -> dict[str, set[str]]:
    """Модуль → модули репо, которые он импортирует. Обход всех узлов Import
    (и внутри функций тоже: `llm.py` импортирует `llm_health` лениво — верхний
    уровень этого не видит)."""
    mods = modules(src)
    graph: dict[str, set[str]] = {m: set() for m in mods}
    for p in src.glob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in mods and top != p.stem:
                        graph[p.stem].add(top)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                top = node.module.split(".")[0]
                if top in mods and top != p.stem:
                    graph[p.stem].add(top)
    return graph


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


def _strip_comments(text: str, marker: str) -> str:
    """Снять комментарии до конца строки (`//` у Swift, `#` у shell и yml) —
    маркер внутри строкового литерала не считается: перед ним чётное число
    кавычек. Блочные `/* … */` у Swift — тоже."""
    if marker == "//":
        text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    out = []
    for line in text.splitlines():
        pos = 0
        while True:
            i = line.find(marker, pos)
            if i < 0:
                break
            if line[:i].count('"') % 2 == 0:
                line = line[:i]
                break
            pos = i + len(marker)
        out.append(line)
    return "\n".join(out)


def _python_literals(text: str, filename: str) -> list[str]:
    """Строковые литералы python вне докстрингов: константы и части f-строк.
    Докстринг — первый Expr-Constant модуля/класса/функции: пример трейсбека
    в нём цитировал `scripts/memory_bench.py` и держал точку входа в инвентаре
    после удаления настоящего вызова (Critical GLM по #594)."""
    tree = ast.parse(text, filename=filename)
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
            # склейка пути через «/»: CODE / "src" / "x.py" — литералы в разных узлах,
            # путь виден только по цепочке деления (у старого текстового сканера — _JOINED)
            parts: list[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div) \
                    and isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                parts.append(cur.right.value)
                cur = cur.left
            if len(parts) >= 2:
                out.append("/".join(reversed(parts)))
    return out


def entry_points_found(repo: pathlib.Path = REPO) -> dict[str, set[str]]:
    """Путь исполняемого файла → кто его упоминает в коде (файлы)."""
    found: dict[str, set[str]] = {}
    for root in SOURCE_ROOTS:
        base = repo / root
        if not base.exists():
            continue
        files = [base] if base.is_file() else sorted(base.rglob("*"))
        for f in files:
            if not f.is_file() or f.suffix not in SOURCE_SUFFIXES:
                continue
            rel = str(f.relative_to(repo))
            if any(rel == ex or rel.startswith(ex + "/") for ex in EXCLUDE):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if f.suffix == ".py":
                try:
                    haystacks = _python_literals(text, rel)
                except SyntaxError:
                    haystacks = [text]
            elif f.suffix == ".swift":
                haystacks = [_strip_comments(text, "//")]
            else:
                haystacks = [_strip_comments(text, "#")]
            hits = {h for hay in haystacks for h in _PATH.findall(hay)}
            for h in hits:
                if h == rel:
                    continue        # файл упоминает сам себя (шапка «запуск: …»)
                found.setdefault(h, set()).add(rel)
    return found


def out_of_scope_mentions(repo: pathlib.Path = REPO) -> dict[str, set[str]]:
    """Самопроверка полноты области: файлы кода вне SOURCE_ROOTS, которые
    упоминают точку входа. Такой файл переехавший путь не заметит (Critical DS
    по #594: CI-workflow были вне области). Документация и тесты исключены —
    там пути упоминают, но не запускают."""
    out: dict[str, set[str]] = {}
    skip = set(EXCLUDE) | {".git"}
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = str(pathlib.Path(dirpath).relative_to(repo))
        rel_dir = "" if rel_dir == "." else rel_dir
        # прореживание на входе: .venv и сборки — тысячи файлов, читать их незачем
        dirnames[:] = sorted(d for d in dirnames
                             if (f"{rel_dir}/{d}" if rel_dir else d) not in skip and not d.startswith("."))
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if pathlib.Path(name).suffix not in SOURCE_SUFFIXES:
                continue
            if any(rel == r or rel.startswith(r + "/") for r in SOURCE_ROOTS):
                continue
            text = (repo / rel).read_text(encoding="utf-8", errors="replace")
            for h in set(_PATH.findall(text)):
                out.setdefault(h, set()).add(rel)
    return out


def executables(repo: pathlib.Path = REPO) -> set[str]:
    """Исполняемые файлы репозитория по ENTRY_TARGETS: все скрипты и те модули
    `src/`, у которых есть `__main__`. Каждый обязан быть в инвентаре — либо его
    упоминает код, либо он объявлен ручным (Critical DS по #594: инвентарь был
    неполон на треть)."""
    out: set[str] = set()
    for pattern in ENTRY_TARGETS:
        for f in repo.glob(pattern):
            rel = str(f.relative_to(repo))
            if pattern.startswith("src/") and '__name__ == "__main__"' not in f.read_text(encoding="utf-8", errors="replace"):
                continue
            out.add(rel)
    return out


def allowlist_edges(layout: dict) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in layout["allowed_edges"]}


def check(layout: dict, graph: dict[str, set[str]], entries: dict[str, set[str]],
          repo: pathlib.Path = REPO, *, map_text: str | None = None,
          outside: dict[str, set[str]] | None = None) -> list[str]:
    """Все расхождения раскладки с реальностью — строками; пусто = зелёный."""
    problems: list[str] = []
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
    declared = layout["entry_points"]
    for path in sorted(set(entries) - set(declared)):
        problems.append(f"точка входа {path} упоминается в {', '.join(sorted(entries[path]))}, "
                        f"но не объявлена в entry_points")
    for path, meta in sorted(declared.items()):
        if path not in entries and not meta.get("manual"):
            problems.append(f"entry_points объявляет {path}, но в коде его никто не упоминает — "
                            f"снять или пометить manual (ручной запуск)")
    for path in sorted(executables(repo) - set(declared) - set(entries)):
        problems.append(f"исполняемый файл {path} не в инвентаре — объявить в entry_points "
                        f"(manual: true, если запускается только руками)")
    for path in sorted(set(declared) | set(entries)):
        if not (repo / path).is_file():
            who = sorted(entries.get(path, ()))
            where = f"упоминают {', '.join(who)}" if who else "объявлен в entry_points как ручной"
            problems.append(f"точка входа {path} не существует — файл переехал, а {where} не поправлены")
    for path, files in sorted((outside or {}).items()):
        problems.append(f"{', '.join(sorted(files))} вне области скана упоминает точку входа {path} — "
                        f"расширить SOURCE_ROOTS или EXCLUDE в layout_map.py")
    if map_text is not None and map_text != render_map(layout, graph, entries):
        problems.append(f"{MAP.name} отстал от кода — перегенерировать: scripts/layout_map.py")
    return problems


def regen(layout: dict, graph: dict[str, set[str]], entries: dict[str, set[str]]) -> dict:
    """Переписать allowlist и точки входа по факту, сохранив карточки у прежних
    записей allowlist и пометки manual у точек входа. Таблицу слоёв и поправки
    не трогает — это решение, не замер."""
    tickets = {(e["from"], e["to"]): e.get("ticket", "") for e in layout["allowed_edges"]}
    layout["allowed_edges"] = [
        {"from": a, "to": b, "ticket": tickets.get((a, b), "")} for a, b in violations(graph, layout)]
    manual = {p: meta for p, meta in layout["entry_points"].items() if meta.get("manual")}
    points = {p: {"manual": False} for p in entries}
    points.update(manual)
    layout["entry_points"] = dict(sorted(points.items()))
    layout["generated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    return layout


def render_map(layout: dict, graph: dict[str, set[str]], entries: dict[str, set[str]]) -> str:
    """Карта для людей — из тех же данных, что и гейт; хранится в git и
    проверяется на свежесть (критика DS и GLM по #594). Без счётчиков строк:
    они менялись бы от любой правки и шумели бы в каждом PR."""
    lay = layer_of(layout)
    rev: dict[str, set[str]] = {m: set() for m in graph}
    for a, deps in graph.items():
        for b in deps:
            rev.setdefault(b, set()).add(a)
    out = ["# Раскладка кода Чароита (генерируется `scripts/layout_map.py`, руками не править)", "",
           f"Источник истины — `docs/design/layout.json`; гейт — `tests/test_import_boundaries.py`. "
           f"Снимок allowlist и точек входа: {layout.get('generated', '?')}. "
           f"Модулей {len(graph)}, рёбер импорта {sum(len(v) for v in graph.values())}.",
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
    out += ["", "## Точки входа (исполняемые файлы, которые упоминает код)", ""]
    for path, meta in sorted(layout["entry_points"].items()):
        who = ", ".join(sorted(entries.get(path, ()))) or "ручной запуск"
        out.append(f"- `{path}` ← {who}")
    out += ["", "## Модули: импортирует → / кем импортируется ←", ""]
    for m in sorted(graph):
        out.append(f"- `{m}` [{lay.get(m, '?')}] → "
                   f"{', '.join(sorted(graph[m])) or '—'} ← {', '.join(sorted(rev.get(m, ()))) or '—'}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    layout = load_layout()
    graph = import_graph()
    entries = entry_points_found()
    outside = out_of_scope_mentions()
    if "--regen" in args:
        layout = regen(layout, graph, entries)
        LAYOUT.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        MAP.write_text(render_map(layout, graph, entries), encoding="utf-8")
        print(f"{LAYOUT.relative_to(REPO)} переписан: allowlist {len(layout['allowed_edges'])} рёбер, "
              f"точек входа {len(layout['entry_points'])}; карта обновлена")
    map_text = MAP.read_text(encoding="utf-8") if MAP.exists() else None
    problems = check(layout, graph, entries, map_text=map_text, outside=outside)
    if "--check" in args:
        for p in problems:
            print("✗", p)
        print("раскладка совпадает с кодом" if not problems else f"расхождений: {len(problems)}")
        return 1 if problems else 0
    if "--regen" not in args:
        MAP.write_text(render_map(layout, graph, entries), encoding="utf-8")
        problems = [p for p in problems if not p.startswith(MAP.name)]
        print(f"карта: {MAP.relative_to(REPO)}; расхождений с раскладкой: {len(problems)}")
    for p in problems:
        print("✗", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
