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
import pathlib
import sys
import time
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import dossier  # noqa: E402
import yaml  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
VAULT = pathlib.Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
# Потолок на ночь: если тем изменилось много, лучше растянуть на две ночи,
# чем занять машину до утра.
MAX_PER_NIGHT = 12


def cfg() -> dict:
    p = ROOT / "config" / "config.yaml"
    if not p.exists():
        p = ROOT / "config" / "config.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def default_graph(c: dict) -> pathlib.Path:
    raw = str((c.get("sufler") or {}).get("graph_dir", "")).strip()
    return pathlib.Path(raw).expanduser() if raw else pathlib.Path.cwd()


def all_graphs() -> list[pathlib.Path]:
    if not VAULT.is_dir():
        return []
    return [p for p in sorted(VAULT.iterdir())
            if p.is_dir() and not p.name.startswith(".") and (p / "Ядра").is_dir()]


def generate(theme: str, members: list[str], files: dict, c: dict,
             temperature: float = 0.2) -> str:
    """Сводка локальной моделью. Думать не просим: на структурной задаче
    рассуждение съедает бюджет генерации и документ выходит беднее."""
    sys.path.insert(0, str(ROOT / "src"))
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
        return {"граф": graph.name, "тем": 0, "собрано": 0, "пропущено": 0}

    cl = dossier.clusters(files, backlinks)
    today = date.today().isoformat()
    entries, built, skipped = [], 0, 0

    for theme, members in sorted(cl.items(), key=lambda kv: -len(kv[1])):
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
            continue

        if dry:
            print(f"  [план] {theme}: {len(members)} источников"
                  f" ({'новое' if not old_fp else 'изменилось'})")
            built += 1
            continue

        t0 = time.time()
        body = ""
        for attempt in (1, 2):          # вторая попытка чуть холоднее
            try:
                body = dossier.trim_to_format(
                    generate(theme, members, files, c, temperature=0.2 if attempt == 1 else 0.05))
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠️ {theme}: модель не ответила ({type(e).__name__}: {e})")
                body = ""
                break
            if dossier.looks_valid(body):
                break
            print(f"  … {theme}: попытка {attempt} — ответ не по формату, повтор")
            body = ""
        if not body:
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
        dossier.write_index(folder, entries)

    return {"граф": graph.name, "тем": len(cl), "собрано": built, "пропущено": skipped}


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
    for g in graphs:
        if not g.is_dir():
            print(f"{g}: нет такой папки")
            continue
        print(f"=== {g.name}")
        r = run(g, c, full=args.full, dry=args.dry, limit=args.limit)
        print(f"    тем: {r['тем']}, собрано: {r['собрано']}, без изменений: {r['пропущено']}")
        total += r["собрано"]

    print(f"итого собрано досье: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
