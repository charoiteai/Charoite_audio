#!/usr/bin/env python3
"""CLI ревизии ядер (ядро — src/tier3.py, там же вся логика и пороги).

    .venv/bin/python scripts/tier3_cores.py                # текущий граф, отчёт
    .venv/bin/python scripts/tier3_cores.py --apply        # применить
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
import tier3  # noqa: E402

VAULT = pathlib.Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"


def default_graph() -> pathlib.Path:
    cfg = pathlib.Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    try:
        import yaml
        gd = yaml.safe_load(cfg.read_text(encoding="utf-8"))["sufler"]["graph_dir"]
        return pathlib.Path(gd).expanduser()
    except Exception:
        return pathlib.Path.cwd()


def run(graph: pathlib.Path, apply: bool) -> None:
    r = tier3.revise(graph, apply=apply)
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
                    help="применить (без флага — только отчёт)")
    args = ap.parse_args()

    if args.all_graphs:
        graphs = [d for d in sorted(VAULT.iterdir())
                  if d.is_dir() and (d / "Ядра").is_dir()]
        if not graphs:
            sys.exit(f"в vault нет графов с папкой Ядра: {VAULT}")
        for g in graphs:
            run(g, args.apply)
        return
    run(args.graph or default_graph(), args.apply)


if __name__ == "__main__":
    main()
