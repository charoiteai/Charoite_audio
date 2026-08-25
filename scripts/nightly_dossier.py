#!/usr/bin/env python3
"""Ночная сборка досье по темам графа.

    .venv/bin/python scripts/nightly_dossier.py               # текущий граф, инкрементально
    .venv/bin/python scripts/nightly_dossier.py --full        # пересобрать всё
    .venv/bin/python scripts/nightly_dossier.py --all-graphs  # все графы vault
    .venv/bin/python scripts/nightly_dossier.py --dry         # показать план, не писать
    .venv/bin/python scripts/nightly_dossier.py --find "запрос"  # проверить, что найдётся

Инкрементальность: у каждого досье в frontmatter лежит отпечаток состава
(список источников + их mtime). Совпал — тема не менялась, модель не зовём.
На типичной ночи пересобираются единицы тем из десятков.

Запускается из nightly.sh ПОСЛЕ tier3 — чтобы досье собирались по уже
причёсанным ядрам, без дублей.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import dossier  # noqa: E402
import graphs  # noqa: E402
import live_gate  # noqa: E402
from config_loader import load_user_or_example  # noqa: E402

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
# Потолок на ночь: если тем изменилось много, лучше растянуть на две ночи,
# чем занять машину до утра.
MAX_PER_NIGHT = 12


def cfg() -> dict:
    return load_user_or_example(ROOT) or {}


def default_graph(c: dict) -> pathlib.Path:
    return graphs.graph_dir(c) or pathlib.Path.cwd()


def all_graphs() -> list[pathlib.Path]:
    """Графы всех vault-ов, а не только iCloud: настроенный graph_dir вне
    iCloud (`~/Documents/Charoite/Work` из примера конфига) раньше молча
    оставался без досье — шаг ночи «ok», собрано 0 (аудит 17.08). Единая
    точка — src/graphs.py, как у tier3 и брифа."""
    return graphs.all_graphs("Ядра")


def _index_entry_from_disk(theme: str, members: list[str], path: pathlib.Path,
                           fp: str, today: str) -> dict:
    """Запись индекса для темы, которую этот прогон не пересобирал: досье
    на диске живо — в индексе оно должно остаться (отпечаток — фактический)."""
    return {
        "тема": theme, "файл": f"{dossier.DOSSIER_DIR}/{theme}.md",
        "источников": len(members),
        "собрано": _собрано(path) or today,
        "отпечаток": dossier.read_fingerprint(path) or fp,
        "ключи": dossier.keywords(theme + " " + " ".join(members)),
    }


def generate(theme: str, members: list[str], files: dict, c: dict,
             temperature: float = 0.2) -> str:
    """Сводка локальной моделью. Думать не просим: на структурной задаче
    рассуждение съедает бюджет генерации и документ выходит беднее."""
    sys.path.insert(0, str(CODE / "src"))
    from llm import LLM  # noqa: PLC0415

    llm = LLM(c)
    prompt = dossier.build_prompt(theme, members, files)
    model = (c.get("llm") or {}).get("model")
    out = []
    for chunk in llm.stream(
            prompt, model=model, think=False,
            temperature=temperature, num_predict=1800,
            system=("Ты составляешь фактические справки по документам. "
                    "Отвечаешь только текстом справки в заданном формате, "
                    "без обращений к читателю и без вопросов.")):
        out.append(chunk)
    return "".join(out).strip()


def run(graph: pathlib.Path, c: dict, full: bool, dry: bool, limit: int) -> dict:
    folder = graph / dossier.DOSSIER_DIR
    files, backlinks = dossier.scan(graph)
    if not files:
        return {"граф": graph.name, "тем": 0, "собрано": 0, "пропущено": 0, "отказы": 0}

    cl = dossier.clusters(files, backlinks)
    today = date.today().isoformat()
    entries, built, skipped = [], 0, 0
    отказы = 0   # модель не ответила: тема осталась без разбора

    themes = sorted(cl.items(), key=lambda kv: -len(kv[1]))
    for ti, (theme, members) in enumerate(themes):
        path = folder / f"{theme}.md"
        fp = dossier.fingerprint(members, files)
        old_fp = dossier.read_fingerprint(path)

        if not full and old_fp == fp and path.exists():
            skipped += 1
            # индекс всё равно перечитываем — тема жива
            entries.append({
                "тема": theme, "файл": f"{dossier.DOSSIER_DIR}/{theme}.md",
                "источников": len(members), "собрано": _собрано(path) or today,
                "отпечаток": fp,
                "ключи": dossier.keywords(theme + " " + " ".join(members)),
            })
            continue

        if built >= limit:
            skipped += 1
            if path.exists():   # новую тему сверх лимита в индекс не выдумываем
                entries.append(_index_entry_from_disk(theme, members, path, fp, today))
            continue

        if dry:
            print(f"  [план] {theme}: {len(members)} источников"
                  f" ({'новое' if not old_fp else 'изменилось'})")
            built += 1
            continue

        # Утренняя встреча посреди хвоста ночи: пока суфлёр слушает, модель
        # его — досье подождёт (с потолком, чтобы ночь не стала днём).
        live_gate.wait_while_live(ROOT, what="досье", cap=3600)
        if live_gate.night_is_over():
            print("  ⏹ время ночного прогона вышло — остальные темы завтра")
            # Индекс от потолка не худеет: оставшиеся темы остаются в нём
            # записями с диска — как темы сверх лимита. Голый break отдавал
            # write_index посещённый префикс, и _index/_ИНДЕКС терял темы —
            # регресс бага 17.08 (круг по PR #363, GLM).
            skipped += len(themes) - ti   # сводка не врёт про хвост (круг-2, DS)
            for late_theme, late_members in themes[ti:]:
                late_path = folder / f"{late_theme}.md"
                if late_path.exists():
                    entries.append(_index_entry_from_disk(
                        late_theme, late_members, late_path,
                        dossier.fingerprint(late_members, files), today))
            break
        t0 = time.time()
        body = ""
        for attempt in (1, 2):          # вторая попытка чуть холоднее
            try:
                body = dossier.trim_to_format(
                    generate(theme, members, files, c, temperature=0.2 if attempt == 1 else 0.05))
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠️ {theme}: модель не ответила ({type(e).__name__}: {e})")
                body = ""
                break          # отказ считается ниже, в ветке `if not body`
            if dossier.looks_valid(body):
                break
            print(f"  … {theme}: попытка {attempt} — ответ не по формату, повтор")
            body = ""
        if not body:
            # Брак дважды подряд — тот же отказ, что и исключение: тема
            # осталась без разбора. Раньше он не считался, ночь при
            # «Принято, что дальше?» на всех темах выходила с кодом 0 и
            # статусом «ok» (аудит 17.08). Прежнее досье в индексе оставляем.
            отказы += 1
            if path.exists():
                entries.append(_index_entry_from_disk(theme, members, path, fp, today))
            continue

        manual = dossier.preserve_manual(path.read_text(encoding="utf-8")) if path.exists() else None
        text = dossier.render(theme, body, members, files, fp, today)
        if manual:
            text = text.replace("## Правки автора\n\n—\n", f"## Правки автора\n\n{manual}\n")

        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".md.tmp")   # атомарно: папка синхронизируется iCloud
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        built += 1
        print(f"  ✓ {theme}: {len(members)} источников, {len(body)} зн., {time.time()-t0:.0f}с")

        entries.append({
            "тема": theme, "файл": f"{dossier.DOSSIER_DIR}/{theme}.md",
            "источников": len(members), "собрано": today, "отпечаток": fp,
            "ключи": dossier.keywords(theme + " " + " ".join(members) + " " + body),
        })

    if not dry and entries:
        # Индекс — карта ВСЕХ досье на диске, а не только тех, что посетил
        # этот прогон: темы сверх лимита ночи и темы с отказом раньше
        # выпадали из _index/_ИНДЕКС, поиск деградировал, а --full на
        # большом графе оставлял в индексе 12 записей (аудит 17.08).
        dossier.write_index(folder, entries)

    return {"граф": graph.name, "тем": len(cl), "собрано": built,
            "пропущено": skipped, "отказы": отказы}


def _собрано(path: pathlib.Path) -> str:
    import re
    try:
        m = re.search(r"^собрано:\s*(\S+)", path.read_text(encoding="utf-8")[:400], re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Ночная сборка досье по темам графа")
    ap.add_argument("--graph", help="путь к графу")
    ap.add_argument("--all-graphs", action="store_true", help="все графы vault")
    ap.add_argument("--full", action="store_true", help="пересобрать все темы")
    ap.add_argument("--dry", action="store_true", help="показать план, не писать")
    ap.add_argument("--limit", type=int, default=MAX_PER_NIGHT, help="потолок тем за прогон")
    ap.add_argument("--find", help="проверить, что найдётся по запросу")
    args = ap.parse_args()

    c = cfg()

    if args.find:
        graph = pathlib.Path(args.graph).expanduser() if args.graph else default_graph(c)
        hits = dossier.lookup(graph / dossier.DOSSIER_DIR, args.find)
        if not hits:
            print("досье не найдено — поиск пойдёт по графу")
            return 0
        for h in hits:
            print(f"{h['счёт']:>5}  {h['тема']}  ({h['источников']} источников, {h['собрано']})")
            print(f"       {h['файл']}")
        return 0

    graphs = all_graphs() if args.all_graphs else [
        pathlib.Path(args.graph).expanduser() if args.graph else default_graph(c)]

    total = 0
    тем = 0
    отказов = 0
    for g in graphs:
        if not g.is_dir():
            print(f"{g}: нет такой папки")
            continue
        print(f"=== {g.name}")
        r = run(g, c, full=args.full, dry=args.dry, limit=args.limit)
        print(f"    тем: {r['тем']}, собрано: {r['собрано']}, без изменений: {r['пропущено']}"
              + (f", отказов модели: {r['отказы']}" if r.get("отказы") else ""))
        total += r["собрано"]
        тем += r["тем"]
        отказов += r.get("отказы", 0)

    print(f"итого собрано досье: {total}")
    if отказов:
        print(f"отказов модели: {отказов} из {тем} тем")
    # Молчащая модель — это не успех.
    #
    # 12.08 локальный сервер лёг посреди прогона: 258 тем ушли без разбора,
    # а шаг закончился нулём — и статус ночи получился «ok». Досье не
    # собрались, граф черствел, и заметить это можно было только вручную
    # читая лог. Единичный отказ случается и на живой модели, поэтому порог,
    # а не «любой отказ».
    if отказов >= max(3, тем // 10):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
