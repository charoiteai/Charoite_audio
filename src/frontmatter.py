"""Шапка заметки Obsidian (YAML между `---`) — один разбор на весь конвейер.

До 29.08 у конвейера и у поиска узлов было два самописных регекс-парсера
`aliases:` — один видел блок-списки и не видел кавычки с запятыми, второй
наоборот; `aliases: ["Иванов, Иван"]` рождал фантомный псевдоним «Иван» и
уводил чужие записи в узел (Critical всех трёх голов, круг-1 по #451).
Здесь YAML читает `yaml.safe_load`; шапка обязана закрываться `---`.
"""
from __future__ import annotations

import json
import re

import yaml

_MAX_HEAD = 20_000   # шапка длиннее — не шапка


def split(text: str) -> tuple[str | None, str]:
    """(YAML шапки без разделителей, тело) либо (None, text) — без закрывающего
    `---` шапки нет: иначе `aliases:` из тела читались бы как поле."""
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3, _MAX_HEAD)
    if end == -1:
        return None, text
    nl = text.find("\n", end + 1)
    return text[3:end], (text[nl + 1:] if nl != -1 else "")


def parse(text: str) -> dict:
    fm, _ = split(text)
    if fm is None:
        return {}
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def aliases(text: str) -> list[str]:
    """Псевдонимы узла: список, блок или одиночная строка; пустые и дубли — вон."""
    raw = parse(text).get("aliases")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if item is None or isinstance(item, (dict, list)):
            continue
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


_ALIASES_FIELD_RE = re.compile(r"(?ms)^aliases:[ \t]*(?:\[[^\]]*\]|\n(?:[ \t]+-[^\n]*\n?)+|[^\n]*)\n?")


def with_aliases(text: str, names: list[str]) -> str:
    """Дописать псевдонимы в шапку (шапки нет — завести); порядок прежних
    сохраняется, дубли не плодятся. Список пишется YAML-потоком в кавычках —
    запятая внутри имени остаётся именем."""
    current = aliases(text)
    merged = current + [n.strip() for n in names
                        if n and n.strip() and n.strip() not in current]
    merged = list(dict.fromkeys(merged))
    if merged == current:
        return text
    line = "aliases: " + json.dumps(merged, ensure_ascii=False)
    fm, body = split(text)
    if fm is None:
        return f"---\n{line}\n---\n{text}"
    if re.search(r"(?m)^aliases:", fm):
        fm2 = _ALIASES_FIELD_RE.sub(line + "\n", fm, count=1)
    else:
        fm2 = fm.rstrip("\n") + "\n" + line + "\n"
    if not fm2.startswith("\n"):
        fm2 = "\n" + fm2
    if not fm2.endswith("\n"):
        fm2 += "\n"
    return "---" + fm2 + "---\n" + body
