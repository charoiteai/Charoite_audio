#!/usr/bin/env python3
"""Здоровье графа как памяти: детерминированный линт без модели.

    .venv/bin/python scripts/graph_doctor.py                 # текущий граф, сводка
    .venv/bin/python scripts/graph_doctor.py --all-graphs    # все графы vault (ночь)
    .venv/bin/python scripts/graph_doctor.py --examples 10   # с примерами путей
    .venv/bin/python scripts/graph_doctor.py --strict        # код 1, если есть предупреждения

Что считает: битые [[ссылки]] (экранированный `\\|` в таблицах — валиден,
псевдоним из `aliases:` шапки — живая цель, как у Obsidian), отдельно по
активным папкам и по архиву (`Встречи-архив` конвейер не перечитывает —
там лежит легаси, а не гниение живой памяти; порог предупреждения — только
по активным), ссылки с переносом строки внутри (для Obsidian мертвы), метки
диаризации среди Люди («Собеседник N» — узел, склеивающий разных людей),
сироты (узел без входящих), одноимённые узлы в разных папках (пары
Досье/Ядра и заглушки-редиректы tier3 — по замыслу, считаются отдельно),
почти-дубли по ключу имени (graph_names.name_key) и без порядка слов
(«Иван Петров» / «Петров Иван» — bag_key), покрытие _MOC.md, свежесть.

Ничего не меняет. Отчёт — JSON для утреннего брифа (logs/graph_doctor.json
в корне данных) и сводка в stdout. Пороги предупреждений: битых > 2 %
активных ссылок, метки > 0, сироты > 5 % узлов, настоящие дубли > 0,
почти-дубли > 0. Аудит графа 28.08: 626 битых из 68 514 ссылок, 17
узлов-меток, 5 дублей, 7 почти-дублей — цифры, которых до этого скрипта
никто не видел; аудит памяти 07.09: 1316 битых, из них 73 % в архиве.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graph_links  # noqa: E402
import graph_names  # noqa: E402
import graph_updater  # noqa: E402
import graphs  # noqa: E402
import redirects  # noqa: E402

CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
LINK = graph_links.LINK
HUB_DIRS = ("Люди", "Системы", "Команды", "Ядра", "Блокеры", "Модели", "Досье")
DESIGN_PAIRS = {frozenset(("Досье", "Ядра"))}   # досье на ядро — одноимённо по замыслу
THRESHOLDS = {"broken_share": 0.02, "orphan_share": 0.05}

_norm = graph_links.norm
_disk_candidates = graph_links.disk_candidates   # прежнее имя — для тестов и скриптов


def _is_stub(text: str) -> bool:
    # тот же детектор, что у tier3/досье/облака: «## Статус → в работе» — не
    # заглушка (DS, круг-1 по #448 I2)
    return "дубль-слит" in text[:400] or redirects.is_merged(text)


def inspect(root: pathlib.Path, examples: int = 0) -> dict:
    """Метрики одного графа. `examples` > 0 — добавить примеры путей."""
    notes = graph_links.read_notes(root)
    rel = {p: p.relative_to(root).as_posix() for p in notes}
    resolver = graph_links.LinkResolver(root, notes)
    by_stem = resolver.by_stem

    inbound: collections.Counter = collections.Counter()
    outbound: dict[pathlib.Path, int] = {}
    broken: list[tuple[str, str]] = []
    wrapped = 0
    links_active = 0
    for p, text in notes.items():
        n = 0
        archived = graph_links.is_archive(rel[p])
        for m in LINK.finditer(text):
            n += 1
            if "\n" in m.group(1):
                wrapped += 1
            tgt = resolver.resolve(m.group(1))
            if tgt is None:
                broken.append((rel[p], " ".join(m.group(1).split())))
            elif tgt != p and not p.name.startswith("_"):
                inbound[tgt] += 1                    # ссылка из указателя — не связь
        outbound[p] = n
        if not archived:
            links_active += n
    broken_archive = [b for b in broken if graph_links.is_archive(b[0])]
    broken_active = len(broken) - len(broken_archive)
    # цель — файл, а не написание: `[[Имя]]` и `[[Люди/Имя]]` — одна цель (GLM M5)
    targets = {graph_names.name_key(pathlib.PurePosixPath(t).name) for _, t in broken}

    nodes = [p for p in notes if rel[p].split("/", 1)[0] in HUB_DIRS and "/" in rel[p]
             and not p.name.startswith("_")]          # _ЛЮДИ.md, _ЯДРА.md — указатели
    node_set = set(nodes)
    orphans = [p for p in nodes if inbound[p] == 0]
    placeholders = [p for p in nodes if rel[p].startswith("Люди/")
                    and graph_names.is_placeholder_node(p.stem)]
    groups = {k: v for k, v in by_stem.items() if len([x for x in v if x in node_set]) > 1}
    stubs = 0
    dup_real: list[list[str]] = []
    for _, ps in groups.items():
        ps = [x for x in ps if x in node_set]
        st = [x for x in ps if _is_stub(notes[x])]
        stubs += len(st)
        live = [x for x in ps if x not in st]
        folders = {rel[x].split("/", 1)[0] for x in live}
        for pair in DESIGN_PAIRS:            # досье на ядро — не дубль ядра
            if pair <= folders:
                live = [x for x in live if not rel[x].startswith("Досье/")]
        if len(live) < 2:
            continue
        dup_real.append(sorted(rel[x] for x in live))
    # почти-дубли: ключ без пунктуации и ключ без порядка слов — в одной папке
    near: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for p in nodes:
        folder = rel[p].split("/", 1)[0]
        for k in {graph_names.name_key(p.stem), graph_names.bag_key(p.stem)}:
            if k:
                near[(folder, k)].add(p.stem)
    seen_groups: set[frozenset[str]] = set()
    near_dups: list[list[str]] = []
    for v in near.values():
        if len(v) > 1 and frozenset(v) not in seen_groups:
            seen_groups.add(frozenset(v))
            near_dups.append(sorted(v))

    moc = root / "_MOC.md"
    moc_linked: set[pathlib.Path] = set()
    if moc.exists():
        for m in LINK.finditer(moc.read_text(encoding="utf-8", errors="replace")):
            t = resolver.resolve(m.group(1))
            if t is not None and t in notes:   # вложение — не заметка, не покрытие (GLM M3)
                moc_linked.add(t)
    week = dt.datetime.now().timestamp() - 7 * 86400
    fresh = 0
    for p in notes:
        try:                       # файл мог исчезнуть между rglob и stat (забыть встречу,
            fresh += p.stat().st_mtime >= week   # миграция, iCloud) — не ронять doctor целиком
        except OSError:
            continue

    links_total = sum(outbound.values())
    rep = {
        "graph": root.name, "root": str(root), "notes": len(notes), "nodes": len(nodes), "links": links_total,
        "links_active": links_active, "broken": len(broken), "broken_active": broken_active,
        "broken_archive": len(broken_archive), "broken_targets": len(targets),
        "wrapped_links": wrapped, "orphans": len(orphans), "placeholders": len(placeholders),
        "dup_groups": len(groups), "dup_stubs": stubs, "dup_real": len(dup_real),
        "near_dups": len(near_dups), "moc": moc.exists(), "moc_linked": len(moc_linked),
        "moc_missing": len([p for p in nodes if p not in moc_linked]), "fresh_7d": fresh,
        "orphans_by_dir": dict(collections.Counter(rel[p].split("/", 1)[0] for p in orphans)),
    }
    warnings: list[str] = []
    if links_active and broken_active / links_active > THRESHOLDS["broken_share"]:
        warnings.append(f"битых ссылок в активных папках {broken_active} "
                        f"({100 * broken_active / links_active:.1f} % от {links_active}; в архиве ещё {len(broken_archive)})")
    if placeholders:
        warnings.append(f"меток диаризации среди Люди: {len(placeholders)} — узлы склеивают разных людей")
    if nodes and len(orphans) / len(nodes) > THRESHOLDS["orphan_share"]:
        warnings.append(f"сирот {len(orphans)} ({100 * len(orphans) / len(nodes):.0f} % узлов)")
    if dup_real:
        warnings.append(f"одноимённых узлов в разных папках: {len(dup_real)} групп")
    if near_dups:
        warnings.append(f"почти-дублей по имени: {len(near_dups)} (пунктуация/скобки/дефис/порядок слов)")
    rep["warnings"] = warnings
    if examples:
        rep["examples"] = {
            "broken": [f"{s} -> [[{t}]]" for s, t in broken if not graph_links.is_archive(s)][:examples],
            "broken_archive": [f"{s} -> [[{t}]]" for s, t in broken_archive[:examples]],
            "placeholders": [rel[p] for p in placeholders[:examples]],
            "orphans": [rel[p] for p in orphans[:examples]],
            "dup_real": [" | ".join(g) for g in dup_real[:examples]],
            "near_dups": [" | ".join(g) for g in near_dups[:examples]],
        }
    return rep


def summary(rep: dict) -> str:
    lines = [f"{rep['graph']}: заметок {rep['notes']}, узлов {rep['nodes']}, ссылок {rep['links']}; "
             f"битых {rep['broken']} (актив {rep.get('broken_active', '?')} / архив {rep.get('broken_archive', '?')}; "
             f"целей {rep['broken_targets']}, с переносом {rep['wrapped_links']}); "
             f"сирот {rep['orphans']}; меток {rep['placeholders']}; "
             f"дублей {rep['dup_real']} (+{rep['dup_stubs']} заглушек); почти-дублей {rep['near_dups']}; "
             f"вне MOC {rep['moc_missing']}; изменено за 7 дн. {rep['fresh_7d']}"]
    lines += [f"  ⚠️ {w}" for w in rep["warnings"]]
    for kind, items in (rep.get("examples") or {}).items():
        if items:
            lines.append(f"  {kind}:")
            lines += [f"    {x}" for x in items]
    return "\n".join(lines)


def report_path() -> pathlib.Path:
    return ROOT / "logs" / "graph_doctor.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", type=pathlib.Path, default=None, help="один граф")
    ap.add_argument("--all-graphs", action="store_true", help="все графы vault с папкой «Ядра»")
    ap.add_argument("--examples", type=int, default=0, help="сколько примеров путей печатать")
    ap.add_argument("--json", type=pathlib.Path, default=None, help="куда класть отчёт")
    ap.add_argument("--strict", action="store_true", help="код 1 при предупреждениях")
    a = ap.parse_args()
    found = ([a.graph] if a.graph else graphs.all_graphs("Ядра") if a.all_graphs
             else [graphs.graph_dir(graph_updater.load_cfg()) or pathlib.Path.cwd()])
    found = [g for g in found if g and g.is_dir()]
    if not found:
        print("графов не найдено — нечего проверять")
        return 0
    reps = {str(g): inspect(g, a.examples) for g in found}   # ключ — путь: две «Работа» не затрут друг друга (GLM M11)
    for rep in reps.values():
        print(summary(rep))
    out = a.json or report_path()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"generated": dt.datetime.now().isoformat(timespec="seconds"),
                                   "graphs": reps}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        print(f"отчёт не записан ({e})")
    return 1 if a.strict and any(r["warnings"] for r in reps.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
