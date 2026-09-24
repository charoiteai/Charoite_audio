#!/usr/bin/env python3
"""Разовая правка формата поручений в уже накопленных минутках.

Новые встречи нормализуются на выходе демона, но у пользователя за месяцы
работы лежат десятки файлов, где поручения записаны прозой модели и потому
невидимы окну «Задачи». Замер по рабочему графу: 89 файлов минуток, задач
видно 4, после правки — 275.

Правится только раздел поручений и только формат: текст, имена и сроки
остаются как были. Статус, поставленный человеком или контролем задач
(выполнено, снято «[-]», возвращено), не трогается (task_line, №366). По
умолчанию — сухой прогон; запись — под общим замком графа и с корнем данных
из CHAROITE_ROOT, как у остальных пишущих в граф.

    python3 scripts/fix_action_items.py                 # показать, что изменится
    python3 scripts/fix_action_items.py --apply         # применить
    python3 scripts/fix_action_items.py --graph ПУТЬ    # другой граф
"""
from __future__ import annotations

import argparse
import contextlib
import pathlib
import re
import sys


# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py. Вставка —
# только чтобы импортировать сам канон.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import charoite_paths  # noqa: E402
import file_locks  # noqa: E402
import graphs  # noqa: E402
import safe_write  # noqa: E402
from action_items import normalize  # noqa: E402
from charoite_paths import RootNotNamed, require_data_root, resolve_root  # noqa: E402

LOCK_WAIT = 5 * 60      # общий замок пишущих в граф: дольше держит только зависший сосед

# задачи, видимые во вкладке, — открытые и выполненные; снятое контролем «[-]» вкладка не
# показывает, и normalize его не трогает (task_line, №366)
CHECKBOX = re.compile(r"^\s*[-*] \[[ xX]\] ", re.M)


def graph_dir(explicit: str | None) -> pathlib.Path | None:
    """Явный аргумент — как есть (относительный от корня данных); иначе —
    единая точка src/graphs.py (SUFLER_GRAPH_DIR → config.yaml)."""
    if explicit:
        return graphs.resolve(explicit)
    if not (resolve_root(__file__) / "config" / "config.yaml").exists():
        return None
    return graphs.graph_dir()


def _graph_lock(graph: pathlib.Path, root: pathlib.Path):
    """Общий замок пишущих в граф (file_locks): разбор встречи, облачная ревизия,
    ночь и контроль задач пишут те же файлы минуток. Без него запись шла
    «tmp + replace» поверх чужой правки, сделанной между чтением и записью
    (Opus C3 входного круга №366)."""
    try:
        lock_dir = charoite_paths.secure_dir(
            charoite_paths.graph_backups(graph, "cloud_backup", root=root).parent)
    except OSError as e:
        raise SystemExit(f"замок графа не взять ({e}) — не пишу")
    return file_locks.graph_lock(lock_dir, LOCK_WAIT)


def _fixed(text: str) -> tuple[str, int]:
    """Преобразование для safe_write.rewrite_file: текст и признак правки."""
    after = normalize(text)
    return after, int(after != text)


def main() -> int:
    ap = argparse.ArgumentParser(description="Поручения в минутках → формат задач")
    ap.add_argument("--graph", help="путь к графу (по умолчанию sufler.graph_dir)")
    ap.add_argument("--apply", action="store_true", help="записать изменения")
    args = ap.parse_args()

    graph = graph_dir(args.graph)
    if not graph or not graph.is_dir():
        print("граф не найден — пропуск")
        return 0

    lock = contextlib.nullcontext(True)
    if args.apply:
        try:
            root = require_data_root(__file__)
        except (RootNotNamed, ValueError):
            print("для --apply нужен корень данных: CHAROITE_ROOT", file=sys.stderr)
            return 2
        lock = _graph_lock(graph, root)

    files = [p for p in graph.rglob("*.md")
             if "инутк" in p.name or "_minutes" in p.name]
    before = after = changed = 0
    with lock as taken:
        if not taken:
            print(f"замок графа занят дольше {LOCK_WAIT // 60} мин — не пишу", file=sys.stderr)
            return 1
        for p in files:
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            fixed = normalize(text)
            b, a = len(CHECKBOX.findall(text)), len(CHECKBOX.findall(fixed))
            before += b
            after += a
            if fixed == text:
                continue
            changed += 1
            if args.apply:
                # Файл читается заново под снимком и пишется с гейтом expect:
                # отметка, сделанная после сухого чтения, не затирается.
                safe_write.rewrite_file(p, _fixed, "поручения минуток")

    verb = "исправлено" if args.apply else "будет исправлено"
    print(f"файлов минуток: {len(files)}, {verb}: {changed}")
    print(f"задач видно: {before} → {after} (+{after - before})")
    if not args.apply and changed:
        print("это сухой прогон; чтобы применить — добавьте --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
