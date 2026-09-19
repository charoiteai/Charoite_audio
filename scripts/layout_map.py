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
  в README не должны врать после переезда;
- область: один обход репозитория с одним списком исключений; файл вне
  «кода», называющий точку входа, — расхождение, не пропуск.

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

# Исполняемые файлы репозитория — по этим шаблонам (модули src/ — только с гвардом __main__).
ENTRY_TARGETS = ("src/*.py", "scripts/*.py", "scripts/*.sh", "app/*.sh", "*.sh")
# Один список исключений на все обходы: чужой код, сборки, окружения, тесты.
EXCLUDE = (".git", ".venv", "build", ".build", "app/build", "app/.build", "app/Tests", "tests", "node_modules")
# Код — источники запуска: упоминание пути здесь = связь «кто зовёт».
CODE_ROOTS = ("app", "scripts", "src", ".github", ".pre-commit-config.yaml")
CODE_SUFFIXES = (".swift", ".sh", ".py", ".yml", ".yaml", ".plist")
# Проза — документация и конфиги: названный путь обязан существовать, но связью не считается.
PROSE_SUFFIXES = (".md", ".toml", ".in", ".txt", ".yml", ".yaml", ".cfg", ".ini")
# путь к исполняемому файлу: с каталогом (src/x.py, scripts/x.sh, app/x.sh) или голый скрипт
# (./make_app.sh, make_app.sh — так его зовёт CI с working-directory), резолв по имени
# путь к исполняемому файлу: с каталогом (src/x.py, scripts/x.sh, app/x.sh) — или голое имя
# скрипта .sh (./make_app.sh, make_app.sh: так его зовёт CI с working-directory), резолв по имени;
# голые имена .py не считаются — «audio.py» в прозе означает модуль, а не путь запуска
_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(?:(?:src|scripts|app)/[A-Za-z_][A-Za-z0-9_]*\.(?:py|sh)"
                   r"|(?:\./)?[A-Za-z_][A-Za-z0-9_]*\.sh)(?![A-Za-z0-9_.])")


class LayoutError(ValueError):
    """Артефакт раскладки невалиден: правится руками, инвариант — при загрузке."""


class Scan(NamedTuple):
    """Замер: связи «кто зовёт» из кода, названные пути из прозы, ошибки сканера."""
    mentions: dict[str, set[str]]       # путь → файлы кода, которые его называют
    prose: dict[str, set[str]]          # путь → документы/конфиги, которые его называют
    problems: list[str]


def load_layout(path: pathlib.Path = LAYOUT) -> dict:
    """Строгая загрузка: слои попарно не пересекаются, `order`/`allowed`
    согласованы, стрелки вниз, у поправок слоя, ручных точек входа и рёбер
    allowlist есть обоснование или карточка (Critical GLM и Important DS
    круга 1; критика DS/GLM круга 2: manual без why)."""
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
        for d in deps:
            if d not in order:
                raise LayoutError(f"{layer}: в allowed неизвестный слой {d!r}")
            if order.index(d) >= order.index(layer):
                raise LayoutError(f"{layer} зависит не вниз: {deps}")
    for e in layout["allowed_edges"]:
        if not e.get("ticket"):
            raise LayoutError(f"ребро {e.get('from')} → {e.get('to')} без карточки")
    for path_, why in layout["manual_entry_points"].items():
        if not _is_target_path(path_) or not why:
            raise LayoutError(f"ручная точка входа {path_}: не путь к исполняемому файлу или пустое why")
    return layout


def _is_target_path(rel: str) -> bool:
    return any(pathlib.PurePosixPath(rel).match(p) and rel.count("/") == p.count("/") for p in ENTRY_TARGETS)


def modules(src: pathlib.Path = SRC) -> set[str]:
    return {p.stem for p in src.glob("*.py")}


def import_graph(src: pathlib.Path = SRC) -> dict[str, set[str]]:
    """Модуль → модули репо, которые он импортирует. Обход всех узлов Import
    (и внутри функций: `llm.py` импортирует `llm_health` лениво)."""
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


def _has_main_guard(tree: ast.AST) -> bool:
    """Настоящий `if __name__ == "__main__"` по AST — кавычки и порядок
    операндов не важны, подстрока в докстринге не считается (Important GLM,
    Minor DS круга 2)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        parts = [node.test.left, *node.test.comparators]
        names = {p.id for p in parts if isinstance(p, ast.Name)}
        consts = {p.value for p in parts if isinstance(p, ast.Constant)}
        if "__name__" in names and "__main__" in consts:
            return True
    return False


def executables(repo: pathlib.Path = REPO) -> dict[str, str]:
    """Исполняемые файлы репозитория по ENTRY_TARGETS: путь → почему исполняемый
    (скрипт по расположению / модуль с гвардом __main__). Библиотека `src/`
    без гварда исполняемой не является, что бы про неё ни говорили подсказки."""
    out: dict[str, str] = {}
    for pattern in ENTRY_TARGETS:
        for f in sorted(repo.glob(pattern)):
            if not f.is_file():
                continue
            rel = str(f.relative_to(repo))
            if any(rel == ex or rel.startswith(ex + "/") for ex in EXCLUDE):
                continue
            if pattern.startswith("src/"):
                try:
                    tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"), filename=rel)
                except SyntaxError:
                    continue
                if not _has_main_guard(tree):
                    continue
                out[rel] = "модуль с гвардом __main__"
            else:
                out[rel] = "скрипт"
    return out


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


def _python_literals(text: str, filename: str) -> list[str]:
    """Строковые литералы python вне докстрингов: константы, части f-строк и
    склейки путей через «/» (CODE / "src" / "x.py"). Докстринг — первый
    Expr-Constant модуля/класса/функции (Critical GLM круга 1)."""
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
            parts: list[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div) \
                    and isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                parts.append(cur.right.value)
                cur = cur.left
            if len(parts) >= 2:
                out.append("/".join(reversed(parts)))
    return out


def _walk(repo: pathlib.Path):
    """Один обход для всех сканов — файлы под git (`git ls-files`): данные
    владельца (бэкапы графа, стенограммы, боевой конфиг) не в репозитории и
    не читаются; скрытые каталоги не отсекаются (Important GLM круга 2:
    `.github` спасало только имя в корнях). Без git (синтетическое дерево в
    тестах) — обход диска с тем же списком исключений."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True)
        files = [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]
    except (OSError, subprocess.CalledProcessError):
        files = []
        for dirpath, dirnames, filenames in os.walk(repo):
            rel_dir = os.path.relpath(dirpath, repo)
            rel_dir = "" if rel_dir == "." else rel_dir
            dirnames[:] = sorted(d for d in dirnames if (f"{rel_dir}/{d}" if rel_dir else d) not in EXCLUDE)
            files.extend(f"{rel_dir}/{n}" if rel_dir else n for n in sorted(filenames))
    for rel in sorted(files):
        if any(rel == ex or rel.startswith(ex + "/") for ex in EXCLUDE):
            continue
        if (repo / rel).is_file():
            yield rel


def _is_code(rel: str) -> bool:
    return any(rel == r or rel.startswith(r + "/") for r in CODE_ROOTS) and rel.endswith(CODE_SUFFIXES)


def _resolve(hits: set[str], targets: set[str]) -> set[str]:
    """Найденные токены → пути целей: полный путь как есть, голое имя скрипта
    — по basename (make_app.sh из CI с working-directory: app)."""
    by_name: dict[str, list[str]] = {}
    for t in targets:
        by_name.setdefault(pathlib.PurePosixPath(t).name, []).append(t)
    out = set()
    for h in hits:
        if "/" in h:
            out.add(h)
        elif len(by_name.get(h, [])) == 1:
            out.update(by_name[h])          # голое имя чужого скрипта (хелперы вне репо) — не путь репо
    return out


def _tokens(text: str) -> set[str]:
    out = set()
    for m in _PATH.finditer(text):
        tok = m.group(0)
        out.add(tok if tok.startswith(("src/", "scripts/", "app/")) else tok.removeprefix("./"))
    return out


def scan(repo: pathlib.Path = REPO) -> Scan:
    """Замер названных путей: код — упоминания как связи; проза — только
    существование. Ошибки разбора — в problems, не молча (Important DS круга 2)."""
    mentions: dict[str, set[str]] = {}
    prose: dict[str, set[str]] = {}
    problems: list[str] = []
    targets = set(executables(repo))
    for rel in _walk(repo):
        f = repo / rel
        if _is_code(rel):
            text = f.read_text(encoding="utf-8", errors="replace")
            if rel.endswith(".py"):
                try:
                    haystacks = _python_literals(text, rel)
                except SyntaxError as e:
                    problems.append(f"{rel} не разбирается ({e.msg}, строка {e.lineno}) — упоминания из него не собраны")
                    continue
            elif rel.endswith(".swift"):
                haystacks = [_strip_comments(text, "//")]
            else:
                haystacks = [_strip_comments(text, "#")]
            hits = _resolve({t for hay in haystacks for t in _tokens(hay)}, targets)
            for h in hits:
                if h != rel:
                    mentions.setdefault(h, set()).add(rel)
        elif rel == str(MAP.relative_to(REPO)):
            continue                        # карта — производная, не источник
        elif rel.endswith(PROSE_SUFFIXES) or rel.endswith(CODE_SUFFIXES):
            text = f.read_text(encoding="utf-8", errors="replace")
            for h in _resolve(_tokens(text), targets):
                if h != rel:
                    prose.setdefault(h, set()).add(rel)
    return Scan(mentions, prose, problems)


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
    # названные пути обязаны существовать — и в коде, и в прозе
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
    писал артефакт, который следующая загрузка отвергала трейсбеком).
    Слои, поправки и ручные точки входа — решения, их regen не трогает."""
    tickets = {(e["from"], e["to"]): e.get("ticket", "") for e in layout["allowed_edges"]}
    fresh = violations(graph, layout)
    layout["allowed_edges"] = [{"from": a, "to": b, "ticket": tickets.get((a, b), "")} for a, b in fresh]
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
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        layout = load_layout()
    except LayoutError as e:
        print(f"✗ {LAYOUT.relative_to(REPO)}: {e}")
        return 1
    graph = import_graph()
    scanned = scan()
    execs = executables()
    if "--regen" in args:
        layout, unticketed = regen(layout, graph)
        LAYOUT.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        MAP.write_text(render_map(layout, graph, scanned, execs), encoding="utf-8")
        print(f"{LAYOUT.relative_to(REPO)} переписан: allowlist {len(layout['allowed_edges'])} рёбер; карта обновлена")
        for a, b in unticketed:
            print(f"✗ ребро {a} → {b} без карточки — вписать ticket в allowed_edges, иначе загрузка откажет")
        return 1 if unticketed else 0
    map_text = MAP.read_text(encoding="utf-8") if MAP.exists() else None
    problems = check(layout, graph, scanned, execs, map_text=map_text)
    if "--check" in args:
        for p in problems:
            print("✗", p)
        print("раскладка совпадает с кодом" if not problems else f"расхождений: {len(problems)}")
        return 1 if problems else 0
    MAP.write_text(render_map(layout, graph, scanned, execs), encoding="utf-8")
    problems = [p for p in problems if not p.startswith(MAP.name)]
    print(f"карта: {MAP.relative_to(REPO)}; расхождений с раскладкой: {len(problems)}")
    for p in problems:
        print("✗", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
