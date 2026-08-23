#!/usr/bin/env python3
"""Одна встреча — одна папка в архиве.

Тема встречи уточняется при повторных разборах, а папка называется по теме.
Пока архивация не умела переименовывать, каждое уточнение заводило вторую
папку на ту же встречу: «2026-07-15 09-00 — Бюджет MVP» и рядом
«2026-07-15 09-00 — Бюджет и ресурсы MVP». К 03.08 таких пар
накопилось на 21 встречу — архив, в который перестаёшь заглядывать.

Причина закрыта в `meeting_archive.archive_meeting` (папка переименовывается,
а не создаётся заново). Здесь — уборка того, что уже накопилось.

Ничего не удаляет: лишние папки уезжают в `Встречи-архив/_дубли/`, а файлы,
которых нет в остающейся папке, сначала переносятся в неё. Проверили — стёрли
папку `_дубли` руками.

    .venv/bin/python scripts/dedup_archive.py            # только показать
    .venv/bin/python scripts/dedup_archive.py --apply    # сделать
"""
from __future__ import annotations

import collections
import os
import pathlib
import re
import shutil
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import graphs  # noqa: E402
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import yaml  # noqa: E402

from meeting_archive import ARCHIVE_DIR  # noqa: E402

# Время целиком: «12-58», «12-58-12», «12-58-12-1» — папки разных встреч
# одной минуты не считаются дублями друг друга (круг-1 по PR #388, Codex).
STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2})((?:-\d{2})?(?:-\d+)?) ")
KEEP_DIR = "_дубли"


def groups(archive: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    """Папки архива по встречам; в значении больше одной — это дубль."""
    by_stamp: dict[str, list[pathlib.Path]] = collections.defaultdict(list)
    for d in archive.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        m = STAMP_RE.match(d.name)
        if m:
            by_stamp[f"{m.group(1)}_{m.group(2)}{m.group(3)}{m.group(4).replace('-', '', 1)}"].append(d)
    return {k: v for k, v in by_stamp.items() if len(v) > 1}


def pick(folders: list[pathlib.Path], titles: dict[str, str], stamp: str) -> pathlib.Path:
    """Какая папка остаётся.

    Сначала та, чьё имя совпадает с нынешней темой встречи: именно её человек
    видит в графе и по ней ищет. Если такой нет — самая свежая по времени
    записи: в неё писали последней.
    """
    want = titles.get(stamp)
    if want:
        for f in folders:
            if f.name.endswith(f"— {want}"):
                return f
    return max(folders, key=lambda p: p.stat().st_mtime)


def current_titles(graph: pathlib.Path) -> dict[str, str]:
    """Нынешние темы встреч — из имён заметок графа."""
    titles: dict[str, str] = {}
    for p in (graph / "Встречи").glob("*.md"):
        m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{4})_(.+)$", p.stem)
        if m:
            titles[m.group(1)] = m.group(2).replace("_", " ")
    return titles


def merge(keep: pathlib.Path, extra: pathlib.Path, apply: bool) -> list[str]:
    """Забрать из лишней папки то, чего нет в остающейся."""
    moved = []
    for f in sorted(extra.glob("*")):
        if f.is_dir():
            continue
        target = keep / f.name
        if target.exists():
            continue
        moved.append(f.name)
        if apply:
            shutil.copy2(f, target)
    return moved


def main() -> None:
    apply = "--apply" in sys.argv
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    graph = graphs.graph_dir(cfg) or sys.exit("sufler.graph_dir не задан")
    archive = graph / ARCHIVE_DIR
    if not archive.exists():
        sys.exit(f"нет архива встреч: {archive}")

    titles = current_titles(graph)
    dups = groups(archive)
    if not dups:
        print("дублей нет: одна встреча — одна папка")
        return

    parked = archive / KEEP_DIR
    print(f"встреч с дублями папок: {len(dups)}\n")
    for stamp, folders in sorted(dups.items()):
        keep = pick(folders, titles, stamp)
        print(f"{stamp}\n  оставляю: {keep.name}")
        for extra in folders:
            if extra == keep:
                continue
            moved = merge(keep, extra, apply)
            note = f", перенесено файлов: {len(moved)}" if moved else ""
            print(f"  убираю:   {extra.name}{note}")
            if apply:
                parked.mkdir(exist_ok=True)
                extra.rename(parked / extra.name)

    if apply:
        print(f"\nЛишние папки лежат в {parked} — проверьте и удалите руками.")
    else:
        print("\nЭто был показ. Чтобы сделать: --apply")


if __name__ == "__main__":
    main()
