#!/usr/bin/env python3
"""Слияние графов вольта: граф-донор вливается в граф-приёмник.

Зачем. Раскол графа — реальная авария: 03.08 рабочая встреча уехала в
новый граф «Linux 1.8», который модель честно придумала по содержанию.
Конвейер с тех пор держится списка известных графов, но уже расколотое
надо уметь сшивать: встречи одного проекта в двух папках — это порванные
обратные ссылки и враньё поиска умолчанием.

Что делает:
- каждый файл донора, которого нет в приёмнике, переносится (с подпапками);
- коллизия имён — содержимое донора ДОПИСЫВАЕТСЯ в файл приёмника секцией
  «Перенесено из графа …» (frontmatter донора при этом срезается: две
  YAML-шапки в одном файле ломают Obsidian). Узлы графа аддитивны — у
  людей и систем это истории упоминаний, терять их нельзя, а выбирать
  «чей файл главнее» без человека нельзя тем более;
- строки встреч из _MOC донора (`- [[Встречи/…]]`) переезжают в секцию
  «## 🗓 Встречи» приёмника — остальной _MOC авторский, его не трогаем;
- сам _MOC донора после слияния заменяется пометкой «слит в …» — папка
  перестаёт выглядеть живым графом (known_graphs требует _MOC.md, поэтому
  пометка остаётся, а не удаляется вместе с папкой: пустую папку человек
  удалит сам, убедившись, что всё доехало).

Внутриграфовые ссылки ([[Люди/Имя]]) относительные — переезд их не рвёт.

Запуск:
    python3 scripts/merge_graphs.py <донор> <приёмник> [--apply]

Донор и приёмник — имена папок в vault (или полные пути). Без --apply
печатается план и ничего не трогается.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import re
import shutil
import sys
import uuid

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))

MOC = "_MOC.md"


class MergeError(RuntimeError):
    """План небезопасен или применение не удалось без потери исходников."""


def resolve_graph(raw: str) -> pathlib.Path:
    """Имя папки графа → путь: как есть, или рядом с настроенным графом."""
    p = pathlib.Path(raw).expanduser()
    if p.is_dir():
        return p
    import graphs  # локальный src/graphs.py — знает configured_graph
    base = graphs.configured_graph()
    if base is not None and (base.parent / raw).is_dir():
        return base.parent / raw
    sys.exit(f"граф не найден: {raw}")


def validate_roots(src: pathlib.Path, dst: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Канонические корни двух отдельных, невложенных графов."""
    try:
        src = src.resolve(strict=True)
        dst = dst.resolve(strict=True)
    except OSError as exc:
        raise MergeError(f"не удалось открыть граф: {exc}") from exc
    if not src.is_dir() or not dst.is_dir():
        raise MergeError("донор и приёмник должны быть папками")
    if src == dst:
        raise MergeError("донор и приёмник — одна папка")
    if src.is_relative_to(dst) or dst.is_relative_to(src):
        raise MergeError("папки графов вложены друг в друга")
    for graph in (src, dst):
        moc = graph / MOC
        if not moc.is_file() or moc.is_symlink():
            raise MergeError(f"это не граф Charoite: нет обычного файла {moc}")
        read_utf8(moc, "оглавление графа")
    return src, dst


def read_utf8(path: pathlib.Path, role: str) -> str:
    """Текстовая проверка до первой записи; бинарные коллизии не склеиваем."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise MergeError(f"{role} не UTF-8, автоматически склеить нельзя: {path}") from exc
    except OSError as exc:
        raise MergeError(f"не удалось прочитать {role}: {path}: {exc}") from exc


def ensure_confined(path: pathlib.Path, root: pathlib.Path, role: str) -> None:
    """Существующие symlink-родители не должны выводить операцию из графа."""
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise MergeError(f"не удалось проверить путь {role}: {path}: {exc}") from exc
    if not resolved.is_relative_to(root):
        raise MergeError(f"{role} указывает за пределы графа: {path} → {resolved}")


def strip_frontmatter(text: str) -> str:
    """YAML-шапка донора срезается: две шапки в одном файле ломают Obsidian."""
    m = re.match(r"^---\n.*?\n---\n", text, flags=re.DOTALL)
    return text[m.end():] if m else text


def moc_meeting_lines(text: str) -> list[str]:
    """Строки встреч из _MOC: их пишет конвейер, формат один и тот же."""
    return [ln for ln in text.splitlines() if ln.startswith("- [[Встречи/")]


def plan(src: pathlib.Path, dst: pathlib.Path) -> tuple[list, list, list[str]]:
    """(переносы, коллизии, строки _MOC) — считается без единой записи."""
    src, dst = validate_roots(src, dst)
    moves: list[tuple[pathlib.Path, pathlib.Path]] = []
    appends: list[tuple[pathlib.Path, pathlib.Path]] = []
    for f in sorted(src.rglob("*")):
        if f.is_symlink():
            raise MergeError(f"символическая ссылка в доноре требует ручной проверки: {f}")
        if not f.is_file() or f.name == MOC or f.name.startswith("."):
            continue
        rel = f.relative_to(src)
        target = dst / rel
        ensure_confined(target, dst, "цель переноса")
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise MergeError(f"коллизия файла с не-файлом: {rel}")
            if f.read_bytes() != target.read_bytes():
                # Аддитивны только узлы Markdown. Даже текстовый JSON нельзя
                # дописать секцией: он станет синтаксически битым индексом.
                if f.suffix.lower() != ".md" or target.suffix.lower() != ".md":
                    raise MergeError(f"коллизия не Markdown требует ручного решения: {rel}")
                # Проверяем ВСЕ коллизии сейчас, до первого переноса.
                read_utf8(f, "файл донора при коллизии")
                read_utf8(target, "файл приёмника при коллизии")
                appends.append((f, target))
            # побайтовая копия: переносить нечего, донорский экземпляр
            # просто останется в папке до ручной уборки
        else:
            moves.append((f, target))
    moc_lines: list[str] = []
    src_moc = src / MOC
    dst_text = read_utf8(dst / MOC, "оглавление приёмника")
    for ln in moc_meeting_lines(read_utf8(src_moc, "оглавление донора")):
        link = ln.split("|")[0].removeprefix("- [[")
        if link not in dst_text:
            moc_lines.append(ln)
    return moves, appends, moc_lines


def validate_plan(src: pathlib.Path, dst: pathlib.Path,
                  moves: list, appends: list, moc_lines: list[str]) -> None:
    """Повторная полная проверка непосредственно перед резервной копией."""
    src, dst = validate_roots(src, dst)
    seen: set[pathlib.Path] = set()
    for f, target in moves:
        ensure_confined(f, src, "источник переноса")
        ensure_confined(target, dst, "цель переноса")
        if not f.is_file() or f.is_symlink() or not f.is_relative_to(src):
            raise MergeError(f"источник переноса изменился после плана: {f}")
        if target.exists() or not target.is_relative_to(dst):
            raise MergeError(f"цель переноса изменилась после плана: {target}")
        if target in seen:
            raise MergeError(f"повтор цели в плане: {target}")
        seen.add(target)
    for f, target in appends:
        ensure_confined(f, src, "источник коллизии")
        ensure_confined(target, dst, "цель коллизии")
        if (not f.is_file() or f.is_symlink() or not f.is_relative_to(src)
                or not target.is_file() or target.is_symlink()
                or not target.is_relative_to(dst)):
            raise MergeError(f"коллизия изменилась после плана: {f} → {target}")
        if f.suffix.lower() != ".md" or target.suffix.lower() != ".md":
            raise MergeError(f"коллизия не Markdown требует ручного решения: {f.name}")
        read_utf8(f, "файл донора при коллизии")
        read_utf8(target, "файл приёмника при коллизии")
        if target in seen:
            raise MergeError(f"повтор цели в плане: {target}")
        seen.add(target)
    if any(not line.startswith("- [[Встречи/") for line in moc_lines):
        raise MergeError("в плане _MOC есть строка неизвестного формата")


def create_backup(src: pathlib.Path, dst: pathlib.Path,
                  moves: list, appends: list) -> pathlib.Path:
    """Копия каждого файла, которого коснётся операция; остаётся после успеха."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = dst.parent / f".charoite-merge-backup-{src.name}-to-{dst.name}-{stamp}-{uuid.uuid4().hex[:8]}"
    try:
        for side, root, files in (
            ("donor", src, [f for f, _ in moves] + [f for f, _ in appends] + [src / MOC]),
            ("receiver", dst, [target for _, target in appends] + [dst / MOC]),
        ):
            for file in dict.fromkeys(files):
                rel = file.relative_to(root)
                target = backup / side / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, target)
        (backup / "RECOVERY.txt").write_text(
            f"Charoite merge backup\nDonor: {src}\nReceiver: {dst}\n"
            "The merge tool keeps this folder for manual recovery.\n",
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 — исходные графы ещё не менялись
        shutil.rmtree(backup, ignore_errors=True)
        raise MergeError(f"не удалось создать резервную копию: {exc}") from exc
    return backup


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Замена текстового файла без окна с наполовину записанным содержимым."""
    temp = path.with_name(f".{path.name}.merge-{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def restore_backup(src: pathlib.Path, dst: pathlib.Path, backup: pathlib.Path,
                   moves: list, appends: list) -> None:
    """Откатить только файлы плана; сам backup оставить пользователю."""
    for f, target in moves:
        target.unlink(missing_ok=True)
        f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / "donor" / f.relative_to(src), f)
    for f, target in appends:
        f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / "donor" / f.relative_to(src), f)
        shutil.copy2(backup / "receiver" / target.relative_to(dst), target)
    shutil.copy2(backup / "donor" / MOC, src / MOC)
    shutil.copy2(backup / "receiver" / MOC, dst / MOC)


def apply(src: pathlib.Path, dst: pathlib.Path,
          moves: list, appends: list, moc_lines: list[str]) -> pathlib.Path:
    src, dst = validate_roots(src, dst)
    validate_plan(src, dst, moves, appends, moc_lines)
    backup = create_backup(src, dst, moves, appends)
    stamp = f"{dt.date.today():%Y-%m-%d}"
    try:
        for f, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            # shutil.move, не Path.rename: донор бывает на другом томе.
            shutil.move(str(f), str(target))
        for f, target in appends:
            body = strip_frontmatter(read_utf8(f, "файл донора")).strip()
            merged = (read_utf8(target, "файл приёмника").rstrip()
                      + f"\n\n---\n## Перенесено из графа {src.name} ({stamp})\n\n{body}\n")
            atomic_write_text(target, merged)
            f.unlink()
        dst_moc = dst / MOC
        if moc_lines:
            text = read_utf8(dst_moc, "оглавление приёмника")
            block = "\n".join(moc_lines)
            if "## 🗓 Встречи" in text:
                text = text.replace("## 🗓 Встречи", f"## 🗓 Встречи\n{block}", 1)
            else:
                text += f"\n## 🗓 Встречи\n{block}\n"
            atomic_write_text(dst_moc, text)
        atomic_write_text(
            src / MOC,
            f"# {src.name} — слит в {dst.name} ({stamp})\n\n"
            f"Содержимое перенесено: [[{dst.name}/{MOC[:-3]}|{dst.name}]]. "
            f"Папку можно удалить, убедившись, что всё доехало.\n")
    except Exception as exc:  # noqa: BLE001 — обязаны откатить любой частичный сбой
        try:
            restore_backup(src, dst, backup, moves, appends)
        except Exception as restore_exc:  # noqa: BLE001 — путь backup нужен человеку
            raise MergeError(
                f"сбой слияния ({exc}); автоматический откат не удался ({restore_exc}); "
                f"резервная копия: {backup}") from exc
        raise MergeError(
            f"сбой слияния ({exc}); изменения откачены; резервная копия: {backup}") from exc
    return backup


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="граф-донор (имя папки в vault или путь)")
    ap.add_argument("dst", help="граф-приёмник")
    ap.add_argument("--apply", action="store_true",
                    help="выполнить; без флага — только план")
    args = ap.parse_args()

    try:
        src, dst = validate_roots(resolve_graph(args.src), resolve_graph(args.dst))
        moves, appends, moc_lines = plan(src, dst)
    except MergeError as exc:
        sys.exit(f"слияние отменено до записи: {exc}")
    if not moves and not appends and not moc_lines:
        print("переносить нечего: всё уже в приёмнике")
        return
    for f, target in moves:
        print(f"перенос:  {f.relative_to(src)}")
    for f, target in appends:
        print(f"дописать: {f.relative_to(src)} → в конец {target.relative_to(dst)}")
    for ln in moc_lines:
        print(f"_MOC:     {ln}")
    print(f"итого: перенос {len(moves)}, дописываний {len(appends)}, "
          f"строк _MOC {len(moc_lines)}")
    if not args.apply:
        print("план. Выполнить: добавь --apply")
        return
    try:
        backup = apply(src, dst, moves, appends, moc_lines)
    except MergeError as exc:
        sys.exit(str(exc))
    print(f"готово: {src.name} слит в {dst.name}; "
          f"_MOC донора заменён пометкой, папку можно удалить руками")
    print(f"резервная копия: {backup}")


if __name__ == "__main__":
    main()
