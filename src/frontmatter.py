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
import sys

import yaml

_MAX_HEAD = 20_000   # шапка длиннее — не шапка
_CLOSER_RE = re.compile(r"\n---[ \t]*(?:\n|$)")   # ровно `---`, не `----` и не разделитель в тексте


def split(text: str) -> tuple[str | None, str]:
    """(YAML шапки без разделителей, тело) либо (None, text) — без закрывающего
    `---` шапки нет: иначе `aliases:` из тела читались бы как поле."""
    if not text.startswith("---"):
        return None, text
    m = _CLOSER_RE.search(text, 3, _MAX_HEAD)
    if m is None:
        return None, text
    return text[3:m.start()], text[m.end():]


def parse(text: str, where: str = "") -> dict:
    """Шапка как dict; YAML-ошибка — {} и строка в stderr (не молча: DS r2 #451)."""
    fm, _ = split(text)
    if fm is None:
        return {}
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError as e:
        print(f"frontmatter: шапка не разобрана{f' ({where})' if where else ''}: "
              f"{str(e).splitlines()[0][:120]}", file=sys.stderr, flush=True)
        return {}
    return data if isinstance(data, dict) else {}


_ALIASES_INLINE_RE = re.compile(r"^aliases:[ \t]*\[([^\]]*)\][ \t]*$", re.M)
_ALIASES_BLOCK_RE = re.compile(r"^aliases:[ \t]*\n((?:[ \t]+-[^\n]*\n?)+)", re.M)
_ALIASES_SCALAR_RE = re.compile(r"^aliases:[ \t]*([^\[\n][^\n]*)$", re.M)


def _aliases_fallback(fm: str) -> list:
    """Поле `aliases:` из шапки, которую YAML не разобрал (незакавыченное
    двоеточие в соседнем поле и т. п.): узел не должен терять псевдонимы
    из-за чужой строки (DS r2 #451). Запятая в кавычках — часть имени."""
    m = _ALIASES_INLINE_RE.search(fm)
    if m:
        return [x.strip().strip("\"'") for x in re.findall(r'"[^"]*"|\'[^\']*\'|[^,]+', m.group(1))]
    m = _ALIASES_BLOCK_RE.search(fm)
    if m:
        return [ln.strip().lstrip("-").strip().strip("\"'") for ln in m.group(1).splitlines()]
    m = _ALIASES_SCALAR_RE.search(fm)
    return [m.group(1).strip().strip("\"'")] if m else []


def aliases(text: str, where: str = "") -> list[str]:
    """Псевдонимы узла: список, блок или одиночная строка; пустые и дубли — вон."""
    fm, _ = split(text)
    if fm is None:
        return []
    data = parse(text, where)
    raw = data.get("aliases") if data else _aliases_fallback(fm)
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


def _field_span(fm: str, key: str) -> tuple[int, int] | None:
    """Границы поля `key:` в шапке вместе со значением: поток `[...]` с учётом
    кавычек и вложенных скобок (и через переносы), блок «- имя» с отступом
    или без, скаляр с продолжениями. Регекс `[^\]]*` рвался на `]` в
    кавычках и на блоке без отступа (luna, круг-2 #451)."""
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*", fm)
    if not m:
        return None
    start, i = m.start(), m.end()
    rest = fm[i:]
    if rest.startswith("["):
        depth, quote, j = 0, "", 0
        while j < len(rest):
            ch = rest[j]
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "\"'":
                quote = ch
            elif ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
            j += 1
        nl = rest.find("\n", j)
        return start, i + (len(rest) if nl == -1 else nl + 1)
    nl = rest.find("\n")
    end = i + (len(rest) if nl == -1 else nl + 1)
    if rest[:nl if nl != -1 else None].strip() == "":
        cont = re.compile(r"^(?:[ \t]+\S|-[ \t])")     # блок: с отступом или «- имя» без него
    else:
        cont = re.compile(r"^[ \t]+\S")                 # скаляр: только продолжения
    while end < len(fm):
        nl2 = fm.find("\n", end)
        line = fm[end:nl2 if nl2 != -1 else None]
        if not cont.match(line):
            break
        end = len(fm) if nl2 == -1 else nl2 + 1
    return start, end


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
        if text.startswith("---"):
            return text     # незакрытая шапка: новую поверх не заводим (DS r2)
        return f"---\n{line}\n---\n{text}"
    span = _field_span(fm, "aliases")
    if span:
        fm2 = fm[:span[0]] + line + "\n" + fm[span[1]:]
    else:
        fm2 = fm.rstrip("\n") + "\n" + line + "\n"
    if not fm2.startswith("\n"):
        fm2 = "\n" + fm2
    if not fm2.endswith("\n"):
        fm2 += "\n"
    return "---" + fm2 + "---\n" + body
