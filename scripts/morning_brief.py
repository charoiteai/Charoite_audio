#!/usr/bin/env python3
"""Утренний бриф: готовый контекст дня в графе — ДО первого вопроса.

Sleep-time-подход: ночью (после Tier3-ревизии) собрать из ГОТОВЫХ строк
графа файл `_Сегодня.md` — что решили на последних встречах, какие
поручения и открытые вопросы висят, какие ядра живые, что Tier3 пометил
на сведение. Утром весь рабочий контекст восстанавливается за минуту
чтения, без единого вопроса и без LLM (нечему галлюцинировать, стоимость
нулевая).

Берётся ПОСЛЕДНИЙ день со встречами (после выходных бриф не пустой),
ядра — обновлённые за двое суток от этого дня.

    .venv/bin/python scripts/morning_brief.py                # все графы vault
    .venv/bin/python scripts/morning_brief.py --graph /путь  # один граф
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graphs  # noqa: E402


def sect(text: str, title: str) -> list[str]:
    """Пункты '- ...' из секции '## title' саммари."""
    m = re.search(rf"## {title}\n(.*?)(?=\n## |\n---|\Z)", text, re.S)
    if not m:
        return []
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip().startswith("- ")]


def build_brief(graph: pathlib.Path) -> str | None:
    archive = graph / "Встречи-архив"
    cores_dir = graph / "Ядра"
    if not archive.is_dir():
        return None

    # последний день со встречами: папки «YYYY-MM-DD — Название»
    days: dict[str, list[pathlib.Path]] = {}
    for d in archive.iterdir():
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[_ ]\d{2}-?\d{2})? — ", d.name)
        if d.is_dir() and m:
            days.setdefault(m.group(1), []).append(d)
    if not days:
        return None
    last_day = max(days)
    meetings = sorted(days[last_day], key=lambda p: p.name)

    lines = [
        "---",
        "type: бриф",
        f"дата: {dt.date.today().isoformat()}",
        "tags: [бриф, авто]",
        "---",
        "",
        f"# Сегодня — контекст дня (по встречам {last_day})",
        "",
    ]

    decided, tasks, open_q = [], [], []
    lines.append("## Последние встречи")
    for mdir in meetings:
        title = mdir.name.split(" — ", 1)[1]
        summary = mdir / "Саммари.md"
        gist = ""
        if summary.exists():
            text = summary.read_text(encoding="utf-8")
            gm = re.search(r"\*\*Суть одной строкой:\*\*\s*(.+)", text)
            gist = gm.group(1).strip() if gm else ""
            decided += [f"{ln}  ·  {title}" for ln in sect(text, "Решили")]
            tasks += [f"{ln}  ·  {title}" for ln in sect(text, "Поручения")]
            open_q += [f"{ln}  ·  {title}" for ln in sect(text, "Открытые вопросы")]
        link = f"[[Встречи-архив/{mdir.name}/Саммари|{title}]]"
        lines.append(f"- {link}" + (f" — {gist}" if gist else ""))
    lines.append("")

    for title, items in (("Решили", decided), ("Поручения", tasks),
                         ("Открыто", open_q)):
        if items:
            lines += [f"## {title}"] + items + [""]

    # живые ядра: статус обновлялся в последние 2 суток от дня встреч
    if cores_dir.is_dir():
        cutoff = (dt.date.fromisoformat(last_day) - dt.timedelta(days=1)).isoformat()
        alive, to_merge = [], []
        for p in sorted(cores_dir.glob("*.md")):
            if p.name.startswith("_"):
                continue
            text = p.read_text(encoding="utf-8")
            if "Дубль. Смерджен" in text:
                continue
            sm = re.search(r"## Статус\n(.+)", text)
            status = sm.group(1).strip() if sm else ""
            dm = re.search(r"обновлено (\d{4}-\d{2}-\d{2})", status)
            if dm and dm.group(1) >= cutoff:
                clean = re.sub(r"_\(.*?\)_", "", status).strip()
                alive.append(f"- [[Ядра/{p.stem}|{p.stem}]] — {clean}")
            if "возможный дубль" in text:
                to_merge.append(p.stem)
        if alive:
            lines += ["## Живые ядра"] + alive + [""]
        if to_merge:
            lines += ["## Tier3 просит свести вручную"] + [
                f"- [[Ядра/{n}|{n}]]" for n in sorted(set(to_merge))] + [""]

    # ночная ревизия Opus: три риска — в самый верх брифа, хвосты — в конец.
    # Берём свежий отчёт (сегодня либо вчера), старый молча пропускаем.
    for shift in (0, 1):
        day = (dt.date.today() - dt.timedelta(days=shift)).isoformat()
        rev = graph / f"Служебное_ночная_ревизия_{day}.md"
        if rev.exists():
            rt = rev.read_text(encoding="utf-8")
            def night_sect(name: str) -> list[str]:
                m = re.search(rf"^#+\s*(?:\d+\.\s*)?{name}.*?$\n(.*?)(?=^#+\s|\Z)",
                              rt, re.M | re.S | re.I)
                return [ln for ln in m.group(1).strip().splitlines() if ln.strip()] if m else []
            risks = night_sect("Три (?:главных )?риска")
            tails = night_sect("Потерянные хвосты")
            link = f"[[{rev.stem}|полный отчёт]]"
            if risks:
                # вставляем сразу после «# Сегодня …» и пустой строки
                pos = lines.index("", lines.index("# Сегодня — контекст дня (по встречам " + last_day + ")"))
                lines[pos:pos] = ["", "## Три риска недели (ночная ревизия)"] + risks + [link, ""]
            if tails:
                lines += ["## Потерянные хвосты (ночная ревизия)"] + tails + [link, ""]
            break

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", type=pathlib.Path, default=None,
                    help="один граф (default: все графы vault с Встречи-архив)")
    args = ap.parse_args()

    found = [args.graph] if args.graph else graphs.all_graphs("Встречи-архив")
    if not found:
        # второй шаг ночной джобы: «графов нет» — не авария, а сообщение.
        # Раньше здесь был iterdir() по несуществующей iCloud-папке, то есть
        # traceback и красный прогон у всех, кто держит Obsidian не там
        print(f"нет графов с папкой «Встречи-архив» — искал в {graphs.where()}")
        return
    for g in found:
        brief = build_brief(g)
        if brief is None:
            print(f"{g.name}: встреч нет — пропуск")
            continue
        out = g / "_Сегодня.md"
        out.write_text(brief + "\n", encoding="utf-8")
        print(f"{g.name}: бриф записан ({out})")


if __name__ == "__main__":
    main()
