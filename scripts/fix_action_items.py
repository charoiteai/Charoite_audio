#!/usr/bin/env python3
"""Разовая правка формата поручений в уже накопленных минутках.

Новые встречи нормализуются на выходе демона, но у пользователя за месяцы
работы лежат десятки файлов, где поручения записаны прозой модели и потому
невидимы окну «Задачи». Замер по рабочему графу: 89 файлов минуток, задач
видно 4, после правки — 275.

Правится только раздел поручений и только формат: текст, имена и сроки
остаются как были. По умолчанию — сухой прогон.

    python3 scripts/fix_action_items.py                 # показать, что изменится
    python3 scripts/fix_action_items.py --apply         # применить
    python3 scripts/fix_action_items.py --graph ПУТЬ    # другой граф
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys


# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import graphs  # noqa: E402
from action_items import normalize  # noqa: E402

CHECKBOX = re.compile(r"^\s*[-*] \[[ xX]\] ", re.M)


def graph_dir(explicit: str | None) -> pathlib.Path | None:
    """Явный аргумент — как есть (относительный от корня данных); иначе —
    единая точка src/graphs.py (SUFLER_GRAPH_DIR → config.yaml)."""
    if explicit:
        return graphs.resolve(explicit)
    if not (ROOT / "config" / "config.yaml").exists():
        return None
    return graphs.graph_dir()


def main() -> int:
    ap = argparse.ArgumentParser(description="Поручения в минутках → формат задач")
    ap.add_argument("--graph", help="путь к графу (по умолчанию sufler.graph_dir)")
    ap.add_argument("--apply", action="store_true", help="записать изменения")
    args = ap.parse_args()

    graph = graph_dir(args.graph)
    if not graph or not graph.is_dir():
        print("граф не найден — пропуск")
        return 0

    files = [p for p in graph.rglob("*.md")
             if "инутк" in p.name or "_minutes" in p.name]
    before = after = changed = 0
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
            # Атомарно: файл лежит в синхронизируемой папке, и половина
            # записи там — это конфликтная копия у соседнего устройства.
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(fixed, encoding="utf-8")
            tmp.replace(p)

    verb = "исправлено" if args.apply else "будет исправлено"
    print(f"файлов минуток: {len(files)}, {verb}: {changed}")
    print(f"задач видно: {before} → {after} (+{after - before})")
    if not args.apply and changed:
        print("это сухой прогон; чтобы применить — добавьте --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
