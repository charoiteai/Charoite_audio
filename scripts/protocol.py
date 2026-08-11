#!/usr/bin/env python3
"""Протокол встречи одной командой: решения, поручения, открытые вопросы.

После встречи в папке архива лежат Саммари и Минутки. Отдать их участникам
было нечем: человек открывал Obsidian, выделял текст руками, вычищал
вики-ссылки и вставлял в письмо — после каждой встречи. Здесь то же самое
делается одной командой и в виде, пригодном для отправки.

    .venv/bin/python scripts/protocol.py                  # последняя встреча
    .venv/bin/python scripts/protocol.py 2026-07-15       # конкретная
    .venv/bin/python scripts/protocol.py --copy           # сразу в буфер обмена
    .venv/bin/python scripts/protocol.py --out ~/prot.md  # в файл
    .venv/bin/python scripts/protocol.py --style plain    # для мессенджера

Что в протокол НЕ попадает — стенограмма. Ни при каких флагах: разослать
участникам сырую расшифровку разговора куда опаснее, чем не разослать ничего.
В протоколе только то, что уже прошло через сводку: суть, решения, поручения,
открытые вопросы и риски.

Сети здесь нет: текст уходит в stdout, в буфер обмена или в файл. Письмо
отправляет человек своим клиентом — и видит, что отправляет.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import graphs  # noqa: E402

ARCHIVE_DIR = "Встречи-архив"

# Секции в том порядке, в каком их читает человек: сначала решения, потом кто
# что делает, потом что осталось. Заголовки в Саммари и Минутках разные —
# «Решили» против «Решения», — поэтому у каждой секции список синонимов.
SECTIONS = (
    ("Решили", ("Решили", "Решения")),
    ("Поручения", ("Поручения",)),
    ("Открытые вопросы", ("Открытые вопросы", "Открыто")),
    ("Риски", ("Риски",)),
)

_WIKI = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
# Голова пункта: дефис/звёздочка, чекбокс, значки автотезисов — всё в одном
# проходе, потому что в графе они идут подряд: «- 📌 берём ЮPay».
_MARKS = re.compile(r"^(?:[-*]\s*)?(?:\[[ xX]\]\s*)?(?:[📌💎💭⚠️⏮☁️]\s*)*")


def _clean(line: str) -> str:
    """Строка графа → строка для человека: без ссылок, чекбоксов и значков.

    Значки полезны в графе (глазами видно, где решение, где идея), а в письме
    участникам это шум: получатель не знает нашей системы обозначений.
    """
    text = _WIKI.sub(lambda m: (m.group(2) or m.group(1)).split("/")[-1], line)
    text = _MARKS.sub("", text.strip())
    return re.sub(r"\s{2,}", " ", text).strip(" ·").strip()


def _section(text: str, titles: tuple[str, ...]) -> list[str]:
    """Пункты списка из секции «## Заголовок» — по первому найденному имени."""
    for title in titles:
        m = re.search(rf"^##\s*{re.escape(title)}\s*$\n(.*?)(?=^##\s|\Z)",
                      text, re.S | re.M)
        if not m:
            continue
        items = [_clean(ln) for ln in m.group(1).splitlines()
                 if ln.strip().startswith(("-", "*"))]
        items = [i for i in items if i]
        if items:
            return items
    return []


def _gist(text: str) -> str:
    m = re.search(r"\*\*Суть одной строкой:\*\*\s*(.+)", text)
    return _clean(m.group(1)) if m else ""


def _title_and_date(folder: pathlib.Path) -> tuple[str, str]:
    """«2026-07-15 14-00 — Платёжный провайдер» → тема и дата в виде 15.07.2026."""
    name = folder.name
    title = name.split(" — ", 1)[1].strip() if " — " in name else name
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", name)
    date = f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else ""
    return title, date


def _strip_markdown(text: str) -> str:
    """plain-стиль обещает текст «для письма и мессенджера»: пункты Саммари
    приходят с markdown-жирностью («**Ирина** — …»), и в чате звёздочки —
    мусор, а не акцент."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    return re.sub(r"`([^`]+)`", r"\1", text)


def build(folder: pathlib.Path, style: str = "md") -> str:
    """Протокол встречи из её папки архива. Стенограмму не читает вовсе."""
    title, date = _title_and_date(folder)
    source = ""
    for name in ("Саммари.md", "Минутки.md"):
        p = folder / name
        if p.exists():
            source += p.read_text(encoding="utf-8") + "\n"
    if not source.strip():
        return ""

    plain = style != "md"
    head = f"Протокол встречи — {title}" + (f" ({date})" if date else "")
    out: list[str] = [head.upper() if plain else f"# {head}", ""]
    gist = _gist(source)
    if gist:
        out += [_strip_markdown(gist) if plain else gist, ""]

    for human, titles in SECTIONS:
        items = _section(source, titles)
        if not items:
            continue        # пустой заголовок читается как потерянные данные
        if plain:
            items = [_strip_markdown(i) for i in items]
        out.append(f"{human}:" if plain else f"## {human}")
        out += [f"— {i}" if plain else f"- {i}" for i in items]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _archive(graph: pathlib.Path) -> pathlib.Path:
    return graph / ARCHIVE_DIR


def _meetings(graph: pathlib.Path) -> list[pathlib.Path]:
    arch = _archive(graph)
    if not arch.is_dir():
        return []
    return sorted((d for d in arch.iterdir()
                   if d.is_dir() and not d.name.startswith((".", "_"))),
                  key=lambda d: d.name)


def latest(graph: pathlib.Path) -> pathlib.Path | None:
    """Самая свежая встреча архива. None — архива нет или он пуст."""
    found = _meetings(graph)
    return found[-1] if found else None


def find(graph: pathlib.Path, target: str) -> pathlib.Path | None:
    """Встреча по дате ГГГГ-ММ-ДД или по куску имени папки."""
    for d in _meetings(graph):
        if d.name.startswith(target) or target in d.name:
            return d
    return None


def _graphs(graph: pathlib.Path | None) -> list[pathlib.Path]:
    return [graph] if graph else graphs.all_graphs(ARCHIVE_DIR)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default=None,
                    help="дата ГГГГ-ММ-ДД или часть имени папки (по умолчанию — последняя)")
    ap.add_argument("--graph", type=pathlib.Path, default=None, help="конкретный граф")
    ap.add_argument("--style", choices=("md", "plain"), default="md",
                    help="md — для файла и Obsidian, plain — для письма и мессенджера")
    ap.add_argument("--copy", action="store_true", help="положить в буфер обмена")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="записать в файл")
    args = ap.parse_args()

    folder = None
    for g in _graphs(args.graph.expanduser() if args.graph else None):
        folder = find(g, args.target) if args.target else latest(g)
        if folder:
            break
    if folder is None:
        where = args.graph or "графах vault"
        print(f"встреча не найдена в {where}. Проверьте sufler.graph_dir и папку "
              f"«{ARCHIVE_DIR}»", file=sys.stderr)
        return 1

    text = build(folder, style=args.style)
    if not text:
        print(f"в папке {folder.name} нет ни Саммари, ни Минуток — протокол собирать "
              f"не из чего", file=sys.stderr)
        return 1

    if args.out:
        args.out.expanduser().write_text(text, encoding="utf-8")
        print(f"протокол записан: {args.out}")
    if args.copy:
        try:
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            print("протокол в буфере обмена — вставьте в письмо")
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"буфер обмена недоступен ({e}) — текст ниже", file=sys.stderr)
            print(text)
    if not args.out and not args.copy:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
