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
каталог копии обязан лежать вне графа, корень данных — явный (`--root` или
`CHAROITE_ROOT`). При живой встрече (лок или процесс демона) — отказ, код 3;
на время правок берётся общий замок графа (cloud.lock). Код 1 — применено,
но указатель не пересобран или остались ссылки на снятые узлы (см. манифест).

    .venv/bin/python scripts/migrate_placeholders.py --graph "<путь к графу>"
    .venv/bin/python scripts/migrate_placeholders.py --graph "<путь>" --apply \
        --backup ~/charoite-backup/125 --root "<корень данных Чароита>"   # или CHAROITE_ROOT
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
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
        if any(part.startswith(".") for part in p.relative_to(people).parts):
            continue                            # .trash и прочее скрытое — как в plan()
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


def unlink_placeholders(text: str, keys: set[str], keep_bare: set[str] = frozenset(),
                        kept: list[str] | None = None, fenced_hits: list[int] | None = None) -> tuple[str, int]:
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
            if kept is not None:
                kept.append(m.group(0))      # оставлено намеренно — в опись (GLM r2, критика 2)
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
        if fenced:
            if fenced_hits is not None:
                for m in LINK_RE.finditer(line):
                    folder, stem = _target_stem(m.group(1))
                    if graph_updater.name_key(stem) in keys and _in_people(folder):
                        fenced_hits.append(1)   # ссылка в коде: не трогаем, но считаем (GLM r2)
            out.append(line)
            continue
        out.append(LINK_RE.sub(repl, line))
    return "\n".join(out), n


def _namesakes_elsewhere(graph: pathlib.Path, keys: set[str]) -> set[str]:
    """Ключи меток, у которых есть живой узел с тем же именем вне Люди."""
    found: set[str] = set()
    for p in graph.glob("*.md"):              # заметки в корне графа — тоже тёзки (GLM r2)
        if graph_updater.name_key(p.stem) in keys:
            found.add(graph_updater.name_key(p.stem))
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
    kept_bare: dict[str, list[str]] = {}
    fenced_links: dict[str, int] = {}
    for p in graph.rglob("*.md"):
        rel = p.relative_to(graph)
        if any(part.startswith(".") for part in rel.parts) or p in nodes:
            continue
        try:
            text = _read(p)
        except (OSError, ValueError):
            unreadable.append(rel.as_posix())   # ссылку в нём не увидим — узел снимать нельзя (luna C3)
            continue
        kept: list[str] = []
        fenced: list[int] = []
        _new, n = unlink_placeholders(text, keys, keep_bare, kept=kept, fenced_hits=fenced)
        if kept:
            kept_bare[rel.as_posix()] = kept
        if fenced:
            fenced_links[rel.as_posix()] = len(fenced)
        if n:
            files[rel.as_posix()] = n
            if p.is_symlink():
                symlinks.append(rel.as_posix())      # safe_write пишет в цель ссылки, копия не та (luna C4)
    return {"graph": str(graph), "nodes": [p.relative_to(graph / "Люди").with_suffix("").as_posix() for p in nodes],
            "manual": [p.relative_to(graph / "Люди").with_suffix("").as_posix() for p in manual], "keys": sorted(keys),
            "namesakes_elsewhere": sorted(keep_bare), "kept_bare": kept_bare, "fenced_links": fenced_links,
            "unreadable": unreadable, "symlinks": symlinks,
            "files": files, "links": sum(files.values())}


def leftovers(graph: pathlib.Path, keys: set[str], bare_keys: set[str] = frozenset()) -> list[str]:
    """Ссылки на снятые узлы, пережившие миграцию (регистр, подпапки, формы).
    Голые ссылки считаются только на ключи без тёзки в другой папке (bare_keys):
    такая ссылка могла появиться между планом и применением (DS r2)."""
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
                key = graph_updater.name_key(stem)
                if key in keys and _in_people(folder) and (folder is not None or key in bare_keys):
                    out.append(f"{rel.as_posix()}: {m.group(0)}")
    return out


def manual_hints(graph: pathlib.Path, manual: list[str]) -> dict[str, list[str]]:
    """Для узлов «Имя (Собеседник N)» — кандидаты на слияние: узлы Люди с тем
    же именем без скобок (по ключу имени), чтобы владелец не искал руками."""
    people = graph / "Люди"
    hints: dict[str, list[str]] = {}
    for stem in manual:
        name = pathlib.PurePosixPath(stem).name        # manual — относительный путь с подпапкой
        base = re.sub(r"\s*[(（].*?[)）]\s*$", "", name).strip()
        key = graph_updater.name_key(base)
        if not key:
            continue
        exact, fuzzy = [], []
        for p in people.rglob("*.md"):
            rel = p.relative_to(people).with_suffix("").as_posix()
            if p.name.startswith("_") or rel == stem:
                continue
            k = graph_updater.name_key(p.stem)
            if k == key:
                exact.append(rel)
            elif key in k.split():
                fuzzy.append(rel + " (частично)")     # «Анна» ⊂ «Анна Петрова» — эвристика, не совпадение
        cands = sorted(exact) + sorted(fuzzy)
        if cands:
            hints[stem] = cands[:5]
    return hints


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
    print(f"замок графа: {lock_dir / 'cloud.lock'}")   # расхождение корней видно сразу (GLM r2)
    return file_locks.graph_lock(lock_dir, LOCK_WAIT)


def apply(graph: pathlib.Path, backup: pathlib.Path, log=print,
          data_root: pathlib.Path | None = None, live=None) -> dict:
    """`live` — проверка «идёт встреча», повторяется ПОСЛЕ взятия замка: между
    гейтом в main() и замком встреча могла начаться (DS r2, I1)."""
    with _graph_lock(graph, data_root) as taken:
        if not taken:
            raise SystemExit("граф занят соседом (cloud.lock) дольше 5 минут — не пишу")
        if live is not None and live():
            raise SystemExit("встреча началась, пока брали замок — миграцию отложить")
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

    rollback = ("files/<относительный путь> скопировать обратно в граф по тому же пути; "
                "Люди/<узел>.md (с подпапками, как в nodes_moved) — обратно в Люди/ по тому же пути")

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
    current = {"file": None, "node": None}   # объект в работе: при обрыве — в опись как «неопределённый» (luna r2)
    files_fact: dict[str, int] = {}
    index = graph / "Люди" / "_ЛЮДИ.md"
    # всё читаем ДО первой записи: упасть на чтении можно, посреди записи — нет (GLM I2)
    texts: dict[str, str] = {}
    try:
        for rel in p["files"]:
            texts[rel] = _read(graph / rel)
    except (OSError, ValueError) as e:
        _write_manifest("aborted", error=repr(e))
        raise SystemExit(f"файл {rel} не читается ({e!r}) — ничего не изменено")
    except BaseException as e:      # Ctrl-C на чтении: граф не тронут, но и «started» — не правда (luna r2)
        _write_manifest("aborted", error=repr(e))
        raise
    try:
        if index.exists():      # указатель пересобирается в конце — копия всегда (luna I4)
            (dest / "files" / "Люди").mkdir(parents=True, exist_ok=True)
            shutil.copy2(index, dest / "files" / "Люди" / "_ЛЮДИ.md")
        replaced = 0
        for rel, _n in p["files"].items():
            path = graph / rel
            text = texts[rel]
            new, n = unlink_placeholders(text, keys, keep_bare)
            if n:
                copy = dest / "files" / rel
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_bytes(text.encode("utf-8"))         # как было, побайтно — на случай отката
                current["file"] = rel
                safe_write.write_text(path, new)
                done_files.append(rel)          # считается только сделанное; объект в работе — in_flight
                files_fact[rel] = n
                replaced += n
                current["file"] = None
        for node in p["nodes"]:
            src = graph / "Люди" / f"{node}.md"
            target = dest / "Люди" / f"{node}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)        # сначала копия и её проверка, потом удаление:
            if target.read_bytes() != src.read_bytes():     # через iCloud/другой том move не атомарен (luna C2)
                raise OSError(f"копия узла не совпала: {target}")
            current["node"] = node
            src.unlink()
            moved.append(node)
            current["node"] = None
    except BaseException as e:   # и Ctrl-C: граф уже тронут — опись обязана это сказать (GLM r2)
        _write_manifest("partial", error=repr(e), files_done=done_files, nodes_moved=moved,
                        in_flight=current, files=files_fact or p["files"], links=replaced)
        log(f"миграция прервана: {e!r}; сделано файлов {len(done_files)}, перенесено узлов {len(moved)}; "
            f"опись и копии — {dest} (откат: files/* обратно в граф, Люди/* обратно в Люди)")
        raise
    index_rebuilt = True
    try:
        try:
            graph_updater.rebuild_folder_index(graph, "Люди")
        except OSError as e:
            index_rebuilt = False
            log(f"указатель Люди/_ЛЮДИ.md не пересобран: {e}")
        left = leftovers(graph, keys, bare_keys=keys - keep_bare)
        _write_manifest("applied", files_done=done_files, nodes_moved=moved, leftovers=left,
                        index_rebuilt=index_rebuilt, files=files_fact, links=replaced)   # факт, не план (luna r2)
    except BaseException as e:
        # граф уже изменён — статус не может остаться «started» (DS r2, I2)
        _write_manifest("partial", error=repr(e), files_done=done_files, nodes_moved=moved,
                        index_rebuilt=False)
        raise
    log(f"перенесено узлов: {len(moved)}, ссылок → текст: {replaced} "
        f"в {len(done_files)} файлах; копия: {dest}")
    if left:
        log(f"ВНИМАНИЕ: остались ссылки на снятые узлы ({len(left)}): " + "; ".join(left[:10]))
    return {**p, "files": files_fact, "links": replaced, "backup": str(dest),
            "leftovers": left, "index_rebuilt": index_rebuilt}


def _daemon_process_running() -> str:
    """Второй сторож: процесс демона на этой машине — независимо от корня.
    Приложение стартует демона ровно как `[python, "src/daemon.py"]`: argv
    кончается этим путём. Подстрока ловила мои же сессии ревью с «src/daemon.py»
    в промпте (GLM r2). Возвращает строки совпадений (пусто — демона нет)."""
    try:
        import subprocess
        r = subprocess.run(["pgrep", "-fl", r"src/daemon\.py$"], capture_output=True, text=True, check=False)
        return r.stdout.strip()
    except OSError as e:
        print(f"pgrep недоступен ({e}) — второй сторож не работает", file=sys.stderr)
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--graph", required=True, type=pathlib.Path, help="корень графа (папка с Люди/, Встречи/)")
    ap.add_argument("--apply", action="store_true", help="менять граф (по умолчанию — только план)")
    ap.add_argument("--backup", type=pathlib.Path, help="куда сложить копии (обязательно с --apply)")
    ap.add_argument("--root", type=pathlib.Path, default=None,
                    help="корень ДАННЫХ Чароита (где logs/daemon.lock); по умолчанию — CHAROITE_ROOT, как у демона")
    ap.add_argument("--report", type=pathlib.Path, help="записать полный план (JSON) — dry-run показывает только верх списка")
    a = ap.parse_args(argv)
    if a.report and a.apply:
        ap.error("--report работает только без --apply (опись применения — manifest.json в копии)")
    graph = a.graph.expanduser()
    if not (graph / "Люди").is_dir():
        print(f"нет папки Люди в {graph}", file=sys.stderr)
        return 2
    if a.apply:
        if a.backup is None:
            print("--apply требует --backup DIR", file=sys.stderr)
            return 2
        backup = a.backup.expanduser().resolve()
        if backup == graph.resolve() or graph.resolve() in backup.parents:
            print("каталог копии должен лежать вне графа", file=sys.stderr)
            return 2
        # Лок демона и общий замок живут в корне ДАННЫХ (luna C1): корень берём
        # только явный — --root или CHAROITE_ROOT, как у демона; угадывать по
        # checkout кода нельзя — logs/ есть в любой dev-копии (DS r2).
        env_root = (os.environ.get("CHAROITE_ROOT") or "").strip()
        if a.root is None and not env_root:
            print("для --apply укажи корень данных: --root DIR или CHAROITE_ROOT", file=sys.stderr)
            return 2
        root = (a.root or resolve_root(__file__)).expanduser()
        if not (root / "logs").is_dir():
            print(f"{root} — не корень данных (нет logs/); укажи --root", file=sys.stderr)
            return 2

        def live() -> bool:
            return live_gate.daemon_alive(root) or bool(_daemon_process_running())

        if live_gate.daemon_alive(root):
            print("идёт живая встреча (лок демона) — миграцию отложить", file=sys.stderr)
            return 3
        procs = _daemon_process_running()
        if procs:
            print(f"процесс демона запущен — миграцию отложить:\n{procs}", file=sys.stderr)
            return 3
        out = apply(graph, backup, data_root=root, live=live)
        # код 1: применено, но указатель не пересобран или остались ссылки (см. манифест)
        return 0 if out.get("index_rebuilt", True) and not out.get("leftovers") else 1
    p = plan(graph)
    if a.report:
        a.report = a.report.expanduser()
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"полный план: {a.report}")
    print(f"узлов-меток: {len(p['nodes'])}: " + ", ".join(p["nodes"]))
    for stem, cands in manual_hints(graph, p["manual"]).items():
        print(f"  {stem} — кандидаты на слияние в Люди: {', '.join(cands)}")
    if p["fenced_links"]:
        print(f"ссылки внутри блоков кода (не трогаю, останутся): {sum(p['fenced_links'].values())} "
              f"в {len(p['fenced_links'])} файлах")
    if p["kept_bare"]:
        print(f"голые ссылки при тёзке в другой папке (оставлю): {sum(len(v) for v in p['kept_bare'].values())} "
              f"в {len(p['kept_bare'])} файлах")
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
    print(f"ссылок → текст: {p['links']}. Применить: --apply --backup DIR --root <корень данных>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
