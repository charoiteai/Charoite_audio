#!/usr/bin/env python3
"""Раскладка кода: слои, рёбра импортов, точки входа — один машинный артефакт.

Фаза 0 разбиения `src/` на пакеты (№320; архитектурный круг 19.09, DS и GLM):
границы слоёв в проекте держались памятью автора и разовым замером. Здесь —
единственный источник истины `docs/design/layout.json` (слои модуль → слой,
направление стрелок, allowlist существующих рёбер против стрелок, объявленные
точки входа) и генератор карты `docs/design/layout.md` из него и кода. Гейт —
`tests/test_import_boundaries.py`: читает тот же файл и сверяет с реальностью.

Не import-linter: он требует импортируемый пакет, а плоский `src/` из модулей,
импортирующих друг друга короткими именами, пакетом не является; и в проекте
уже есть механизм этого класса — AST-сторожа тестом (`test_cloud_call_sites`,
`test_charoite_paths`). Точки входа — пути `src/*.py` и `scripts/*.py`, по
которым код зовут Swift, shell и подпроцессы python: переезд файла без правки
вызывающего ломал бы запуск молча (Critical DS круга по пакетам).

Запуск:
    .venv/bin/python scripts/layout_map.py            # карта в docs/design/layout.md
    .venv/bin/python scripts/layout_map.py --check    # то же, что тест, кодом выхода
    .venv/bin/python scripts/layout_map.py --regen    # переписать allowlist и точки входа по факту
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO / "src"
LAYOUT = REPO / "docs" / "design" / "layout.json"
MAP = REPO / "docs" / "design" / "layout.md"

# Где код зовут по пути: Swift-приложение, сборка бандла, shell, python-подпроцессы.
# Тесты и документация — не точки входа.
ENTRY_SCAN = (
    ("app/Sources", "*.swift"),
    ("app", "make_app.sh"),
    ("scripts", "*.sh"),
    ("scripts", "*.py"),
    ("src", "*.py"),
)
# путь как строковый литерал целиком либо склейкой двух литералов через «/» (форма CODE / каталог / файл)
_LITERAL = re.compile(r'["\']((?:src|scripts)/[A-Za-z_][A-Za-z0-9_]*\.py)["\']')
_JOINED = re.compile(r'"(src|scripts)"\s*/\s*"([A-Za-z_][A-Za-z0-9_]*\.py)"')
_SHELL = re.compile(r'(?<![A-Za-z0-9_./])((?:src|scripts)/[A-Za-z_][A-Za-z0-9_]*\.py)(?![A-Za-z0-9_.])')


def load_layout(path: pathlib.Path = LAYOUT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
    return {m: layer for layer, ms in layout["layers"].items() for m in ms}


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


def entry_points_found(repo: pathlib.Path = REPO) -> dict[str, set[str]]:
    """Путь → кто его зовёт (файлы), по всем местам запуска."""
    found: dict[str, set[str]] = {}
    for folder, pattern in ENTRY_SCAN:
        for f in sorted((repo / folder).rglob(pattern) if folder == "app/Sources" else (repo / folder).glob(pattern)):
            if not f.is_file():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            rel = str(f.relative_to(repo))
            hits: set[str] = set()
            if f.suffix == ".py":
                hits |= set(_LITERAL.findall(text))
                hits |= {f"{a}/{b}" for a, b in _JOINED.findall(text)}
            else:
                hits |= set(_SHELL.findall(text))
            for h in hits:
                if h == rel:
                    continue        # файл упоминает сам себя (шапка «запуск: …»)
                found.setdefault(h, set()).add(rel)
    return found


def allowlist_edges(layout: dict) -> set[tuple[str, str]]:
    return {(e["from"], e["to"]) for e in layout["allowed_edges"]}


def check(layout: dict, graph: dict[str, set[str]], entries: dict[str, set[str]],
          repo: pathlib.Path = REPO) -> list[str]:
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
    declared = set(layout["entry_points"])
    for path in sorted(set(entries) - declared):
        problems.append(f"точка входа {path} зовётся из {', '.join(sorted(entries[path]))}, но не объявлена в entry_points")
    for path in sorted(declared - set(entries)):
        problems.append(f"entry_points объявляет {path}, но по этому пути никто не зовёт — снять")
    for path in sorted(declared | set(entries)):
        if not (repo / path).is_file():
            problems.append(f"точка входа {path} не существует — файл переехал, а вызывающие "
                            f"({', '.join(sorted(entries.get(path, {'layout.json'})))}) не поправлены")
    return problems


def _head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def regen(layout: dict, graph: dict[str, set[str]], entries: dict[str, set[str]]) -> dict:
    """Переписать allowlist и точки входа по факту, сохранив карточки у прежних
    записей allowlist. Таблицу слоёв не трогает — это решение, не замер."""
    tickets = {(e["from"], e["to"]): e.get("ticket", "") for e in layout["allowed_edges"]}
    layout["allowed_edges"] = [
        {"from": a, "to": b, "ticket": tickets.get((a, b), "")} for a, b in violations(graph, layout)]
    layout["entry_points"] = sorted(entries)
    layout["generated"] = {"at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "head": _head()}
    return layout


def render_map(layout: dict, graph: dict[str, set[str]], entries: dict[str, set[str]]) -> str:
    lay = layer_of(layout)
    rev: dict[str, set[str]] = {m: set() for m in graph}
    for a, deps in graph.items():
        for b in deps:
            rev.setdefault(b, set()).add(a)
    lines = SRC.glob("*.py")
    sizes = {p.stem: p.read_text(encoding="utf-8").count("\n") for p in lines}
    out = ["# Раскладка кода Чароита (генерируется `scripts/layout_map.py`, руками не править)", "",
           f"Источник истины — `docs/design/layout.json`; гейт — `tests/test_import_boundaries.py`. "
           f"Снимок: {layout.get('generated', {}).get('at', '?')}, HEAD {layout.get('generated', {}).get('head', '?')}. "
           f"Модулей {len(graph)}, строк {sum(sizes.values())}, рёбер импорта {sum(len(v) for v in graph.values())}.",
           "", "## Слои и направление стрелок", ""]
    for layer in layout["order"]:
        deps = ", ".join(layout["allowed"].get(layer, [])) or "—"
        mods = sorted(layout["layers"][layer])
        out.append(f"- **{layer}** (зависит от: {deps}; модулей {len(mods)}): " + ", ".join(f"`{m}`" for m in mods))
    out += ["", "## Рёбра против стрелок (allowlist с карточками на снятие)", ""]
    viol = violations(graph, layout)
    tickets = {(e["from"], e["to"]): e.get("ticket", "") for e in layout["allowed_edges"]}
    out.append(f"Всего {len(viol)}.")
    out.append("")
    for a, b in viol:
        out.append(f"- `{a}` ({lay[a]}) → `{b}` ({lay[b]}) — {tickets.get((a, b)) or 'без карточки'}")
    out += ["", "## Точки входа (кто зовёт код по пути)", ""]
    for path in sorted(entries):
        out.append(f"- `{path}` ← {', '.join(sorted(entries[path]))}")
    out += ["", "## Модули: импортирует → / кем импортируется ←", ""]
    for m in sorted(graph):
        out.append(f"- `{m}` [{lay.get(m, '?')}, {sizes.get(m, 0)} строк] → "
                   f"{', '.join(sorted(graph[m])) or '—'} ← {', '.join(sorted(rev.get(m, ()))) or '—'}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    layout = load_layout()
    graph = import_graph()
    entries = entry_points_found()
    if "--regen" in args:
        layout = regen(layout, graph, entries)
        LAYOUT.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{LAYOUT.relative_to(REPO)} переписан: allowlist {len(layout['allowed_edges'])} рёбер, "
              f"точек входа {len(layout['entry_points'])}")
    problems = check(layout, graph, entries)
    if "--check" in args:
        for p in problems:
            print("✗", p)
        print("раскладка совпадает с кодом" if not problems else f"расхождений: {len(problems)}")
        return 1 if problems else 0
    MAP.write_text(render_map(layout, graph, entries), encoding="utf-8")
    print(f"карта: {MAP.relative_to(REPO)}; расхождений с раскладкой: {len(problems)}")
    for p in problems:
        print("✗", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
