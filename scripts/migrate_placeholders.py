#!/usr/bin/env python3
"""Миграция накопленных узлов-меток («Собеседник N») в графе встреч (№125).

Метка диаризации — не человек: узел «Собеседник 3» склеивал разных людей из
разных встреч в одного (аудит 28.08: 13 узлов, у трёх по 130–140 входящих).
С PR #448 конвейер таких узлов не создаёт; этот скрипт разбирает накопленное:

  * каждая ссылка на узел-метку — `[[Люди/Собеседник 3]]`, `[[Собеседник 3]]`,
    `[[Люди/Собеседник 3|Собеседник 3]]`, табличная `\|` — становится текстом
    (подпись, как её видел читатель: alias или имя узла);
  * сам узел переезжает в каталог резервной копии вместе с манифестом
    (что и где заменено), из графа исчезает;
  * указатель `Люди/_ЛЮДИ.md` пересобирается.

По умолчанию — только план (dry-run). `--apply --backup DIR` меняет граф;
каталог копии обязан лежать вне графа. При живой встрече (лок демона) —
отказ: облако и конвейер пишут в те же файлы.

    .venv/bin/python scripts/migrate_placeholders.py --graph "<путь к графу>"
    .venv/bin/python scripts/migrate_placeholders.py --graph "<путь>" --apply --backup ~/charoite-backup/125
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graph_updater  # noqa: E402
import live_gate  # noqa: E402
import safe_write  # noqa: E402

LINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)(#[^\]|]*)?(?:\\?\|([^\]]*))?\]\]")


def placeholder_nodes(graph: pathlib.Path) -> list[pathlib.Path]:
    people = graph / "Люди"
    if not people.is_dir():
        return []
    return sorted(p for p in people.glob("*.md")
                  if not p.name.startswith("_") and graph_updater.is_placeholder_node(p.stem))


def _target_stem(target: str) -> tuple[str | None, str]:
    """(папка или None, стем) из цели ссылки; `.md` снимается."""
    t = target.strip().rstrip("\\").strip()      # `[[Цель\|Текст]]` в таблицах: слэш — не имя
    if t.casefold().endswith(".md"):
        t = t[:-3]
    if "/" in t:
        folder, stem = t.rsplit("/", 1)
        return folder.strip() or None, stem.strip()
    return None, t


def unlink_placeholders(text: str, stems: set[str]) -> tuple[str, int]:
    """Ссылки на узлы-метки → подпись текстом. Возвращает (текст, сколько)."""
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        folder, stem = _target_stem(m.group(1))
        if stem not in stems or folder not in (None, "Люди"):
            return m.group(0)
        n += 1
        alias = (m.group(3) or "").strip()
        return alias or stem

    return LINK_RE.sub(repl, text), n


def plan(graph: pathlib.Path) -> dict:
    nodes = placeholder_nodes(graph)
    stems = {p.stem for p in nodes}
    files: dict[str, int] = {}
    for p in graph.rglob("*.md"):
        rel = p.relative_to(graph)
        if any(part.startswith(".") for part in rel.parts) or p in nodes:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        _new, n = unlink_placeholders(text, stems)
        if n:
            files[rel.as_posix()] = n
    return {"graph": str(graph), "nodes": [p.stem for p in nodes],
            "files": files, "links": sum(files.values())}


def apply(graph: pathlib.Path, backup: pathlib.Path, log=print) -> dict:
    backup = backup.resolve()
    if backup == graph.resolve() or graph.resolve() in backup.parents:
        raise SystemExit("каталог копии должен лежать вне графа")
    p = plan(graph)
    if not p["nodes"]:
        log("узлов-меток нет — делать нечего")
        return p
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = backup / stamp
    (dest / "Люди").mkdir(parents=True, exist_ok=True)
    stems = set(p["nodes"])
    for rel, _n in p["files"].items():
        path = graph / rel
        text = path.read_text(encoding="utf-8")
        new, n = unlink_placeholders(text, stems)
        if n:
            copy = dest / "files" / rel
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_text(text, encoding="utf-8")       # как было — на случай отката
            safe_write.write_text(path, new)
    for stem in p["nodes"]:
        src = graph / "Люди" / f"{stem}.md"
        shutil.move(str(src), str(dest / "Люди" / f"{stem}.md"))
    (dest / "manifest.json").write_text(
        json.dumps({**p, "applied": stamp}, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        graph_updater.rebuild_folder_index(graph, "Люди")
    except OSError as e:
        log(f"указатель Люди/_ЛЮДИ.md не пересобран: {e}")
    log(f"перенесено узлов: {len(p['nodes'])}, ссылок → текст: {p['links']} "
        f"в {len(p['files'])} файлах; копия: {dest}")
    return {**p, "backup": str(dest)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--graph", required=True, type=pathlib.Path, help="корень графа (папка с Люди/, Встречи/)")
    ap.add_argument("--apply", action="store_true", help="менять граф (по умолчанию — только план)")
    ap.add_argument("--backup", type=pathlib.Path, help="куда сложить копии (обязательно с --apply)")
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent.parent,
                    help="корень данных Чароита — где лок демона")
    a = ap.parse_args(argv)
    graph = a.graph.expanduser()
    if not (graph / "Люди").is_dir():
        print(f"нет папки Люди в {graph}", file=sys.stderr)
        return 2
    if a.apply:
        if a.backup is None:
            print("--apply требует --backup DIR", file=sys.stderr)
            return 2
        if live_gate.daemon_alive(a.root):
            print("идёт живая встреча (лок демона) — миграцию отложить", file=sys.stderr)
            return 3
        apply(graph, a.backup.expanduser())
        return 0
    p = plan(graph)
    print(f"узлов-меток: {len(p['nodes'])}: " + ", ".join(p["nodes"]))
    for rel, n in sorted(p["files"].items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {n:4d}  {rel}")
    if len(p["files"]) > 15:
        print(f"  … всего файлов {len(p['files'])}")
    print(f"ссылок → текст: {p['links']}. Применить: --apply --backup DIR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
