#!/usr/bin/env python3
"""Векторы памяти подсказок (src/graph_search.py): блоки изменившихся файлов
графа → bge-m3 через Ollama, кэш в data/graph_search/. Гонять ВНЕ живой
записи: ночью (scripts/nightly.sh) и коротко после встречи (graph_updater).
На встрече демон считает только вектор запроса, файлы не индексирует.

    .venv/bin/python scripts/graph_search_index.py                 # граф из конфига
    .venv/bin/python scripts/graph_search_index.py --budget-s 300  # не дольше пяти минут
    .venv/bin/python scripts/graph_search_index.py --graph demo/graph --stats
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import deps  # noqa: E402

deps.explain_missing()

import graphs  # noqa: E402
import llm  # noqa: E402
import live_gate  # noqa: E402
from charoite_paths import harden_umask  # noqa: E402


def main() -> int:
    harden_umask()   # кэш векторов хранит блоки текста графа — только владельцу (№385)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", help="папка графа (по умолчанию — sufler.graph_dir из конфига)")
    ap.add_argument("--budget-s", type=float, default=None, help="потолок времени на индексацию")
    ap.add_argument("--stats", action="store_true", help="только показать состояние индекса")
    ap.add_argument("--force", action="store_true", help="индексировать и при живой записи (не на встрече!)")
    args = ap.parse_args()
    cfg = graphs.load_config()
    graph = graphs.resolve(args.graph) if args.graph else graphs.graph_dir(cfg)
    if graph is None or not graph.is_dir():
        print(f"граф не найден: {graph}")
        return 2
    root = graphs.data_root()      # корень данных — одна точка (resolve_root), не своя копия (DS M4)
    if not args.stats and not args.force and live_gate.daemon_alive(root):
        # индексация занимает модель эмбеддингов минутами — на встрече слот
        # принадлежит подсказкам; ночь и пауза между встречами дособерут
        print("идёт запись — векторы памяти не собираем (--force, если это не встреча)")
        return 0
    mem = graphs.open_search(graph, llm.embedder(cfg))
    t = time.time()
    mem.refresh(force=True)
    cached = mem.load_vectors()
    pending = len(mem.pending_vectors())
    print(f"{graph.name}: файлов {mem.size} (обход {time.time() - t:.1f} с), с векторами {cached}, ожидают {pending}")
    if args.stats or not pending:
        return 0
    t = time.time()
    stop = (lambda: False) if args.force else (lambda: live_gate.daemon_alive(root))
    done = mem.embed_pending(budget_s=args.budget_s, should_stop=stop)
    left = len(mem.pending_vectors())
    print(f"векторы: {done} файлов за {time.time() - t:.0f} с" + (f", ожидают ещё {left}" if left else ", всё собрано")
          + (f" — {mem.note}" if mem.note else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
