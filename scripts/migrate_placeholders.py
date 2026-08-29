#!/usr/bin/env python3
r"""Миграция накопленных узлов-меток («Собеседник N») в графе встреч (№125).

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
import contextlib
import datetime as dt
import json
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import charoite_paths  # noqa: E402
import file_locks  # noqa: E402
import graph_updater  # noqa: E402
import live_gate  # noqa: E402
import safe_write  # noqa: E402
from charoite_paths import resolve_root  # noqa: E402

LOCK_WAIT = 5 * 60      # общий замок пишущих в граф (cloud_review, ночь): дольше держит только зависший сосед

# цель ссылки — до `#раздел`, `^блок`, `|alias`; `![[…]]` тоже ловим (узла не будет)
LINK_RE = re.compile(r"!?\[\[([^\]|#^]+)([#^][^\]|]*)?(?:\\?\|([^\]]*))?\]\]")
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)")


def placeholder_nodes(graph: pathlib.Path) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """(узлы-метки, узлы на ручное решение). Метка в начале имени —
    «Собеседник 3», «Собеседник 1 (Саша)» — склейка, её разбираем. Имя в
    начале и метка в скобках — «Таня (Собеседник 4)» — у узла есть человек:
    переименовать в «Таня» или слить с существующей «Таней» решает владелец,
    скрипт такие только перечисляет (dry-run на проде 29.08: 4 из 17)."""
    people = graph / "Люди"
    if not people.is_dir():
        return [], []
    labels, manual = [], []
    for p in sorted(people.rglob("*.md")):      # и подпапки Люди/… (DS M7)
        if p.name.startswith("_") or not graph_updater.is_placeholder_node(p.stem):
            continue
        bare = re.sub(r"\s*[(（].*?[)）]\s*$", "", p.stem)
        (labels if graph_updater.is_speaker_placeholder(bare) else manual).append(p)
    return labels, manual


def _target_stem(target: str) -> tuple[str | None, str]:
    """(папка или None, стем) из цели ссылки; `.md`/`.markdown` снимается."""
    t = target.strip().rstrip("\\").strip()      # `[[Цель\|Текст]]` в таблицах: слэш — не имя
    low = t.casefold()
    for ext in (".markdown", ".md"):
        if low.endswith(ext):
            t = t[:-len(ext)]
            break
    if "/" in t:
        folder, stem = t.rsplit("/", 1)
        return folder.strip() or None, stem.strip()
    return None, t


def _in_people(folder: str | None) -> bool:
    """Папка ссылки — Люди или её подпапка; регистр как у Obsidian — любой."""
    if folder is None:
        return True
    f = folder.casefold().strip("/")
    return f == "люди" or f.startswith("люди/")


def unlink_placeholders(text: str, keys: set[str], keep_bare: set[str] = frozenset()) -> tuple[str, int]:
    """Ссылки на узлы-метки → подпись текстом. Возвращает (текст, сколько).

    Сравнение — по ключу имени (`name_key`): `[[собеседник 3]]` и
    `[[Люди/Собеседник 3]]` для Obsidian одно и то же (DS, Critical).
    `![[…]]` тоже становится текстом — узла не будет, вложение рендерилось
    бы ошибкой. Голая ссылка без папки, у которой есть живой тёзка в другой
    папке (keep_bare), остаётся: после переноса узла Obsidian сам поведёт её
    к тёзке. Внутри огороженных блоков кода (``` / ~~~) ничего не меняется.
    """
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        folder, stem = _target_stem(m.group(1))
        key = graph_updater.name_key(stem)
        if key not in keys or not _in_people(folder):
            return m.group(0)
        if folder is None and key in keep_bare:
            return m.group(0)
        n += 1
        alias = (m.group(3) or "").strip()
        return alias or stem

    out, fenced = [], False
    for line in text.split("\n"):
        if _FENCE_RE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        out.append(line if fenced else LINK_RE.sub(repl, line))
    return "\n".join(out), n


def _namesakes_elsewhere(graph: pathlib.Path, keys: set[str]) -> set[str]:
    """Ключи меток, у которых есть живой узел с тем же именем вне Люди."""
    found: set[str] = set()
    for d in graph.iterdir():
        if not d.is_dir() or d.name.startswith(".") or d.name.casefold() == "люди":
            continue
        for p in d.rglob("*.md"):
            k = graph_updater.name_key(p.stem)
            if k in keys:
                found.add(k)
    return found


def _read(path: pathlib.Path) -> str:
    """Текст как есть: байты → utf-8, без подмены CRLF (копия для отката — побайтная)."""
    return path.read_bytes().decode("utf-8")


def plan(graph: pathlib.Path) -> dict:
    nodes, manual = placeholder_nodes(graph)
    keys = {graph_updater.name_key(p.stem) for p in nodes}
    keep_bare = _namesakes_elsewhere(graph, keys)
    files: dict[str, int] = {}
    unreadable: list[str] = []
    symlinks: list[str] = [p.relative_to(graph).as_posix() for p in nodes if p.is_symlink()]
    for p in graph.rglob("*.md"):
        rel = p.relative_to(graph)
        if any(part.startswith(".") for part in rel.parts) or p in nodes:
            continue
        try:
            text = _read(p)
        except (OSError, ValueError):
            unreadable.append(rel.as_posix())   # ссылку в нём не увидим — узел снимать нельзя (luna C3)
            continue
        _new, n = unlink_placeholders(text, keys, keep_bare)
        if n:
            files[rel.as_posix()] = n
            if p.is_symlink():
                symlinks.append(rel.as_posix())      # safe_write пишет в цель ссылки, копия не та (luna C4)
    return {"graph": str(graph), "nodes": [p.relative_to(graph / "Люди").with_suffix("").as_posix() for p in nodes],
            "manual": [p.stem for p in manual], "keys": sorted(keys),
            "namesakes_elsewhere": sorted(keep_bare), "unreadable": unreadable, "symlinks": symlinks,
            "files": files, "links": sum(files.values())}


def leftovers(graph: pathlib.Path, keys: set[str]) -> list[str]:
    """Ссылки на снятые узлы, пережившие миграцию (регистр, подпапки, формы)."""
    out: list[str] = []
    for p in graph.rglob("*.md"):
        rel = p.relative_to(graph)
        if any(part.startswith(".") for part in rel.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, ValueError):
            continue
        fenced = False
        for line in text.split("\n"):
            if _FENCE_RE.match(line):
                fenced = not fenced
                continue
            if fenced:
                continue        # код — не ссылка, как и при замене
            for m in LINK_RE.finditer(line):
                folder, stem = _target_stem(m.group(1))
                if graph_updater.name_key(stem) in keys and _in_people(folder) and folder is not None:
                    out.append(f"{rel.as_posix()}: {m.group(0)}")
    return out


def _graph_lock(graph: pathlib.Path, data_root: pathlib.Path | None):
    """Общий замок пишущих в граф (правило file_locks: делят ВСЕ контуры —
    разбор встречи, облачная ревизия, ночь; GLM по #454). Без корня данных
    (тесты) — замка нет, работаем как есть."""
    if data_root is None:
        return contextlib.nullcontext(True)
    try:
        lock_dir = charoite_paths.secure_dir(
            charoite_paths.graph_backups(graph, "cloud_backup", root=data_root).parent)
    except OSError as e:
        raise SystemExit(f"замок графа не взять ({e}) — не пишу")
    return file_locks.graph_lock(lock_dir, LOCK_WAIT)


def apply(graph: pathlib.Path, backup: pathlib.Path, log=print,
          data_root: pathlib.Path | None = None) -> dict:
    with _graph_lock(graph, data_root) as taken:
        if not taken:
            raise SystemExit("граф занят соседом (cloud.lock) дольше 5 минут — не пишу")
        return _apply_locked(graph, backup, log)


def _apply_locked(graph: pathlib.Path, backup: pathlib.Path, log=print) -> dict:
    backup = backup.resolve()
    if backup == graph.resolve() or graph.resolve() in backup.parents:
        raise SystemExit("каталог копии должен лежать вне графа")
    p = plan(graph)
    if not p["nodes"]:
        log("узлов-меток нет — делать нечего")
        return p
    if p["unreadable"] or p["symlinks"]:
        raise SystemExit("миграция не начата: " + "; ".join(
            (["нечитаемые файлы: " + ", ".join(p["unreadable"])] if p["unreadable"] else [])
            + (["симлинки: " + ", ".join(p["symlinks"])] if p["symlinks"] else [])))
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    dest = backup / stamp
    (dest / "Люди").mkdir(parents=True, exist_ok=False)   # второй запуск в ту же секунду — свой каталог (luna I6)
    manifest = dest / "manifest.json"

    rollback = "скопировать files/<путь> обратно в граф и Люди/<узел>.md обратно в Люди/"

    def _write_manifest(status: str, **extra) -> None:
        manifest.write_text(json.dumps({**p, "status": status, "stamp": stamp, "rollback": rollback, **extra},
                                       ensure_ascii=False, indent=1), encoding="utf-8")

    # Манифест — ДО первого изменения (DS I2): упавшая посередине миграция
    # оставляет опись и копии, а не «граф наполовину и ни строчки о том, что было».
    _write_manifest("started")
    keys = set(p["keys"])
    keep_bare = set(p["namesakes_elsewhere"])
    done_files: list[str] = []
    moved: list[str] = []
    index = graph / "Люди" / "_ЛЮДИ.md"
    # всё читаем ДО первой записи: упасть на чтении можно, посреди записи — нет (GLM I2)
    texts: dict[str, str] = {}
    for rel in p["files"]:
        try:
            texts[rel] = _read(graph / rel)
        except (OSError, ValueError) as e:
            _write_manifest("aborted", error=repr(e))
            raise SystemExit(f"файл {rel} не читается ({e!r}) — ничего не изменено")
    try:
        if index.exists():      # указатель пересобирается в конце — копия всегда (luna I4)
            (dest / "files" / "Люди").mkdir(parents=True, exist_ok=True)
            shutil.copy2(index, dest / "files" / "Люди" / "_ЛЮДИ.md")
        for rel, _n in p["files"].items():
            path = graph / rel
            text = texts[rel]
            new, n = unlink_placeholders(text, keys, keep_bare)
            if n:
                copy = dest / "files" / rel
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(text.encode("utf-8"))         # как было, побайтно — на случай отката
                safe_write.write_text(path, new)
                done_files.append(rel)
        for node in p["nodes"]:
            src = graph / "Люди" / f"{node}.md"
            target = dest / "Люди" / f"{node}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)        # сначала копия и её проверка, потом удаление:
            if target.read_bytes() != src.read_bytes():     # через iCloud/другой том move не атомарен (luna C2)
                raise OSError(f"копия узла не совпала: {target}")
            src.unlink()
            moved.append(node)
    except Exception as e:
        _write_manifest("partial", error=repr(e), files_done=done_files, nodes_moved=moved)
        log(f"миграция прервана: {e!r}; сделано файлов {len(done_files)}, перенесено узлов {len(moved)}; "
            f"опись и копии — {dest} (откат: files/* обратно в граф, Люди/* обратно в Люди)")
        raise
    index_rebuilt = True
    try:
        graph_updater.rebuild_folder_index(graph, "Люди")
    except OSError as e:
        index_rebuilt = False
        log(f"указатель Люди/_ЛЮДИ.md не пересобран: {e}")
    left = leftovers(graph, keys)
    _write_manifest("applied", files_done=done_files, nodes_moved=moved, leftovers=left,
                    index_rebuilt=index_rebuilt)
    log(f"перенесено узлов: {len(moved)}, ссылок → текст: {p['links']} "
        f"в {len(done_files)} файлах; копия: {dest}")
    if left:
        log(f"ВНИМАНИЕ: остались ссылки на снятые узлы ({len(left)}): " + "; ".join(left[:10]))
    return {**p, "backup": str(dest), "leftovers": left, "index_rebuilt": index_rebuilt}


def _daemon_process_running() -> bool:
    """Второй сторож: процесс демона на этой машине — независимо от корня."""
    try:
        import subprocess
        r = subprocess.run(["pgrep", "-f", "src/daemon.py"], capture_output=True, text=True, check=False)
        return bool(r.stdout.strip())
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--graph", required=True, type=pathlib.Path, help="корень графа (папка с Люди/, Встречи/)")
    ap.add_argument("--apply", action="store_true", help="менять граф (по умолчанию — только план)")
    ap.add_argument("--backup", type=pathlib.Path, help="куда сложить копии (обязательно с --apply)")
    ap.add_argument("--root", type=pathlib.Path, default=None,
                    help="корень ДАННЫХ Чароита (где logs/daemon.lock); по умолчанию — CHAROITE_ROOT, как у демона")
    ap.add_argument("--report", type=pathlib.Path, help="записать полный план (JSON) — dry-run показывает только верх списка")
    a = ap.parse_args(argv)
    graph = a.graph.expanduser()
    if not (graph / "Люди").is_dir():
        print(f"нет папки Люди в {graph}", file=sys.stderr)
        return 2
    if a.apply:
        if a.backup is None:
            print("--apply требует --backup DIR", file=sys.stderr)
            return 2
        # Лок демона живёт в корне данных, не в checkout кода (luna C1): без
        # явного --root берём тот же корень, что и демон (CHAROITE_ROOT);
        # корень без logs/ — не корень данных, гадать не будем.
        root = (a.root or resolve_root(__file__)).expanduser()
        if not (root / "logs").is_dir():
            print(f"{root} — не корень данных (нет logs/); укажи --root", file=sys.stderr)
            return 2
        if live_gate.daemon_alive(root) or _daemon_process_running():
            print("идёт живая встреча (лок или процесс демона) — миграцию отложить", file=sys.stderr)
            return 3
        out = apply(graph, a.backup.expanduser(), data_root=root)
        return 0 if out.get("index_rebuilt", True) and not out.get("leftovers") else 1
    p = plan(graph)
    if a.report:
        a.report.expanduser().write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"полный план: {a.report}")
    print(f"узлов-меток: {len(p['nodes'])}: " + ", ".join(p["nodes"]))
    if p["unreadable"]:
        print("НЕЧИТАЕМЫЕ файлы (миграция откажет): " + ", ".join(p["unreadable"]))
    if p["symlinks"]:
        print("СИМЛИНКИ (миграция откажет): " + ", ".join(p["symlinks"]))
    if p["manual"]:
        print("на ручное решение (имя + метка в скобках), не трогаю: " + ", ".join(p["manual"]))
    if p["namesakes_elsewhere"]:
        print("голые ссылки оставлю — есть тёзка в другой папке: " + ", ".join(p["namesakes_elsewhere"]))
    for rel, n in sorted(p["files"].items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {n:4d}  {rel}")
    if len(p["files"]) > 15:
        print(f"  … всего файлов {len(p['files'])}")
    print(f"ссылок → текст: {p['links']}. Применить: --apply --backup DIR")
    return 0


if __name__ == "__main__":
    sys.exit(main())
