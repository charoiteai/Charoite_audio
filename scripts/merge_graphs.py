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
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

MOC = "_MOC.md"


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


def strip_frontmatter(text: str) -> str:
    """YAML-шапка донора срезается: две шапки в одном файле ломают Obsidian."""
    m = re.match(r"^---\n.*?\n---\n", text, flags=re.DOTALL)
    return text[m.end():] if m else text


def moc_meeting_lines(text: str) -> list[str]:
    """Строки встреч из _MOC: их пишет конвейер, формат один и тот же."""
    return [ln for ln in text.splitlines() if ln.startswith("- [[Встречи/")]


def plan(src: pathlib.Path, dst: pathlib.Path) -> tuple[list, list, list[str]]:
    """(переносы, коллизии, строки _MOC) — считается без единой записи."""
    moves: list[tuple[pathlib.Path, pathlib.Path]] = []
    appends: list[tuple[pathlib.Path, pathlib.Path]] = []
    for f in sorted(src.rglob("*")):
        if not f.is_file() or f.name == MOC or f.name.startswith("."):
            continue
        rel = f.relative_to(src)
        target = dst / rel
        if target.exists():
            if f.read_bytes() != target.read_bytes():
                appends.append((f, target))
            # побайтовая копия: переносить нечего, донорский экземпляр
            # просто останется в папке до ручной уборки
        else:
            moves.append((f, target))
    moc_lines: list[str] = []
    src_moc = src / MOC
    if src_moc.exists():
        dst_text = (dst / MOC).read_text(encoding="utf-8") if (dst / MOC).exists() else ""
        for ln in moc_meeting_lines(src_moc.read_text(encoding="utf-8")):
            link = ln.split("|")[0].removeprefix("- [[")
            if link not in dst_text:
                moc_lines.append(ln)
    return moves, appends, moc_lines


def apply(src: pathlib.Path, dst: pathlib.Path,
          moves: list, appends: list, moc_lines: list[str]) -> None:
    stamp = f"{dt.date.today():%Y-%m-%d}"
    for f, target in moves:
        target.parent.mkdir(parents=True, exist_ok=True)
        f.rename(target)
    for f, target in appends:
        body = strip_frontmatter(f.read_text(encoding="utf-8")).strip()
        target.write_text(
            target.read_text(encoding="utf-8").rstrip() +
            f"\n\n---\n## Перенесено из графа {src.name} ({stamp})\n\n{body}\n",
            encoding="utf-8")
        f.unlink()
    dst_moc = dst / MOC
    if moc_lines and dst_moc.exists():
        text = dst_moc.read_text(encoding="utf-8")
        block = "\n".join(moc_lines)
        if "## 🗓 Встречи" in text:
            text = text.replace("## 🗓 Встречи", f"## 🗓 Встречи\n{block}", 1)
        else:
            text += f"\n## 🗓 Встречи\n{block}\n"
        dst_moc.write_text(text, encoding="utf-8")
    src_moc = src / MOC
    if src_moc.exists():
        src_moc.write_text(
            f"# {src.name} — слит в {dst.name} ({stamp})\n\n"
            f"Содержимое перенесено: [[{dst.name}/{MOC[:-3]}|{dst.name}]]. "
            f"Папку можно удалить, убедившись, что всё доехало.\n",
            encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", help="граф-донор (имя папки в vault или путь)")
    ap.add_argument("dst", help="граф-приёмник")
    ap.add_argument("--apply", action="store_true",
                    help="выполнить; без флага — только план")
    args = ap.parse_args()

    src, dst = resolve_graph(args.src), resolve_graph(args.dst)
    if src == dst:
        sys.exit("донор и приёмник — одна папка")
    if src == dst.parent or dst == src.parent or dst.is_relative_to(src):
        sys.exit("папки вложены друг в друга — это не два графа")

    moves, appends, moc_lines = plan(src, dst)
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
    apply(src, dst, moves, appends, moc_lines)
    print(f"готово: {src.name} слит в {dst.name}; "
          f"_MOC донора заменён пометкой, папку можно удалить руками")


if __name__ == "__main__":
    main()
