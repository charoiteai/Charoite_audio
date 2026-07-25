#!/usr/bin/env python3
"""CLI ревизии ядер (ядро — src/tier3.py, там же вся логика и пороги).

    .venv/bin/python scripts/tier3_cores.py                # текущий граф, только отчёт
    .venv/bin/python scripts/tier3_cores.py --mark         # обратимые пометки в графе
    .venv/bin/python scripts/tier3_cores.py --apply        # + слить уверенные дубли
    .venv/bin/python scripts/tier3_cores.py --all-graphs --apply   # все графы vault (ночной режим)
    .venv/bin/python scripts/tier3_cores.py --graph /путь  # конкретный граф

Инкрементальная ревизия после каждой встречи уже встроена в graph_updater —
этот CLI нужен для полного O(n²) прогона (ночная джоба) и ручной проверки.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graphs  # noqa: E402
import tier3  # noqa: E402


def run(graph: pathlib.Path, apply: bool, mark: bool = False) -> None:
    r = tier3.revise(graph, apply=apply, mark=mark)
    n = sum(len(r[k]) for k in ("dups", "nests", "border"))
    if not n and not r["log"]:
        print(f"{graph.name}: чисто")
        return
    print(f"=== {graph.name}")
    for k, title in (("dups", "ДУБЛИ"), ("nests", "ВЛОЖЕНИЯ"), ("border", "ГРАНИЦА")):
        for line in r[k]:
            print(f"  [{title}] {line}")
    for line in r["log"]:
        print(f"  {line}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", type=pathlib.Path, default=None)
    ap.add_argument("--all-graphs", action="store_true",
                    help="все папки vault с подпапкой Ядра")
    ap.add_argument("--apply", action="store_true",
                    help="слить уверенные дубли (включает --mark)")
    ap.add_argument("--mark", action="store_true",
                    help="обратимые правки: пометки «возможный дубль» и ссылки вложений")
    args = ap.parse_args()

    if args.all_graphs:
        found = graphs.all_graphs("Ядра")
        if not found:
            # НЕ sys.exit: этим ходит ночная джоба, а «ревизовать нечего» —
            # не авария. Раньше отсутствие ровно iCloud-папки красило launchd
            # каждую ночь у любого, кто держит граф в другом месте.
            print(f"нет графов с папкой «Ядра» — искал в {graphs.where()}")
            return
        for g in found:
            run(g, args.apply, args.mark)
        return
    run(args.graph or graphs.configured_graph() or pathlib.Path.cwd(),
        args.apply, args.mark)


if __name__ == "__main__":
    main()
