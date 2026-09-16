"""Шапка заметки Obsidian (YAML между `---`) — один разбор на весь конвейер.

До 29.08 у конвейера и у поиска узлов было два самописных регекс-парсера
`aliases:` — один видел блок-списки и не видел кавычки с запятыми, второй
наоборот; `aliases: ["Иванов, Иван"]` рождал фантомный псевдоним «Иван» и
уводил чужие записи в узел (Critical всех трёх голов, круг-1 по #451).
Здесь YAML читает `yaml.safe_load`; шапка обязана закрываться `---`.
"""
from __future__ import annotations

import functools
import json
import re
import sys

import yaml

_MAX_HEAD = 20_000   # шапка длиннее — не шапка
_CLOSER_RE = re.compile(r"\r?\n---[ \t]*(?:\r?\n|$)")   # ровно `---` (и CRLF), не `----`


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


@functools.lru_cache(maxsize=None)
def _list_res(key: str) -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    """Три формы поля-списка `key:` для шапки, которую YAML не разобрал: поток
    `[...]` (до последней `]` строки: `]` в кавычках не рвёт), блок «- имя» с
    отступом и без, одиночная строка (не пробел после двоеточия: иначе `[ \\t]*`
    отступал и ловил поток). Ключ — параметр: тем же разбором читается след
    машины `auto_aliases:` (№286), не второй копией регэкспов."""
    k = re.escape(key)
    return (re.compile(rf"^{k}:[ \t]*\[(.*)\][ \t]*$", re.M),
            re.compile(rf"^{k}:[ \t]*\r?\n((?:[ \t]*-[^\n]*\n?)+)", re.M),
            re.compile(rf"^{k}:[ \t]*([^\[\s][^\n]*)$", re.M))


def _is_literal(x) -> bool:
    """Число, булево, дата — то, что YAML «понимает» вместо строки."""
    return isinstance(x, (int, float, bool)) or type(x).__name__ in ("date", "datetime")


def yaml_str(value: str) -> str:
    """Строка для шапки: JSON-кавычки (= YAML-поток) плюс экранирование
    U+0085/U+2028/U+2029, которые JSON не трогает, а YAML читает как перевод
    строки (GLM r2, luna r3)."""
    return re.sub(r"[\x85\u2028\u2029]", lambda m: "\\u%04x" % ord(m.group()),
                  json.dumps(value, ensure_ascii=False))


def _list_fallback(fm: str, key: str) -> list:
    """Поле-список `key:` из шапки, которую YAML не разобрал (незакавыченное
    двоеточие в соседнем поле и т. п.): узел не должен терять псевдонимы
    из-за чужой строки (DS r2 #451). Запятая в кавычках — часть имени."""
    inline_re, block_re, scalar_re = _list_res(key)
    m = inline_re.search(fm)
    if m:
        return [x.strip().strip("\"'") for x in re.findall(r'"[^"]*"|\'[^\']*\'|[^,]+', m.group(1))]
    m = block_re.search(fm)
    if m:
        return [ln.strip().lstrip("-").strip().strip("\"'") for ln in m.group(1).splitlines()]
    m = scalar_re.search(fm)
    return [m.group(1).strip().strip("\"'")] if m else []


def list_field(text: str, key: str, where: str = "") -> list[str]:
    """Поле-список шапки (`aliases:`, `auto_aliases:`): список, блок или
    одиночная строка; пустые и дубли — вон."""
    fm, _ = split(text)
    if fm is None:
        return []
    data = parse(text, where)
    raw = data.get(key) if data else _list_fallback(fm, key)
    if isinstance(raw, str):
        raw = [raw]
    if raw is None or isinstance(raw, (dict,)):
        return []                       # `aliases: null`/`~`/mapping — псевдонимов нет (luna r3)
    if not isinstance(raw, list):
        raw = [raw]
    if any(_is_literal(x) for x in raw):
        # число/дата/булево YAML уже «понял» по-своему (`01` → 1, `on` → True):
        # псевдоним берём из текста поля, как записан (GLM r2); если текст
        # поля fallback не разобрал — строки из YAML не выбрасываем (DS r3)
        fb = [a for a in _list_fallback(fm, key) if a not in ("null", "~")]
        raw = fb if fb else [str(x) for x in raw if isinstance(x, str) or _is_literal(x)]
    else:
        raw = [x for x in raw if isinstance(x, str)]   # null/mapping/список внутри — вон
    out: list[str] = []
    for item in raw:
        if item is None or isinstance(item, (dict, list)):
            continue
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out


def aliases(text: str, where: str = "") -> list[str]:
    """Псевдонимы узла — поле `aliases:`."""
    return list_field(text, "aliases", where)


def _node_end(node) -> int:
    """Конец ПОСЛЕДНЕГО скаляра узла: у блочного списка end_mark стоит уже на
    следующем ключе, и по нему срезалось бы соседнее поле."""
    if isinstance(node, (yaml.SequenceNode, yaml.MappingNode)) and node.value \
            and not getattr(node, "flow_style", False):     # поток `[...]` кончается на `]`
        last = node.value[-1]
        if isinstance(node, yaml.MappingNode):
            last = last[1]
        return _node_end(last)
    return node.end_mark.index


def _field_span(fm: str, key: str) -> tuple[int, int] | None:
    """Границы поля `key:` в шапке вместе со значением — по позициям узлов
    самого YAML (`yaml.compose`, start/end_mark), а не рукописным сканером:
    три заплатки на сканер за два круга #451 (кавычки в элементе, апостроф,
    экранирование) закрывали по одному расхождению с грамматикой (GLM r3).
    Шапка не разобралась — поле считается своей строкой плюс строками
    блока «- имя» под ней."""
    try:
        node = yaml.compose(fm)
    except yaml.YAMLError:
        node = None
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            if getattr(k, "value", None) == key:
                end = max(_node_end(v), k.end_mark.index)
                nl = fm.find("\n", end)
                return k.start_mark.index, (len(fm) if nl == -1 else nl + 1)
        return None
    m = re.search(rf"(?m)^{re.escape(key)}:[^\n]*\n?(?:[ \t]*-[^\n]*\n?)*", fm)
    return (m.start(), m.end()) if m else None


def with_list_field(text: str, key: str, names: list[str]) -> str:
    """Дописать имена в поле-список `key:` шапки (шапки нет — завести); порядок
    прежних сохраняется, дубли не плодятся. Список пишется YAML-потоком в
    кавычках — запятая внутри имени остаётся именем."""
    current = list_field(text, key)
    merged = current + [n.strip() for n in names
                        if n and n.strip() and n.strip() not in current]
    merged = list(dict.fromkeys(merged))
    if merged == current:
        return text
    line = f"{key}: [" + ", ".join(yaml_str(m) for m in merged) + "]"
    fm, body = split(text)
    if fm is None:
        if text.startswith("---"):
            return text     # незакрытая шапка: новую поверх не заводим (DS r2)
        return f"---\n{line}\n---\n{text}"
    nl = "\r\n" if "\r\n" in fm else "\n"          # окончания строк — как в файле (DS r3)
    span = _field_span(fm, key)
    if span:
        fm2 = fm[:span[0]] + line + nl + fm[span[1]:]
    else:
        fm2 = fm.rstrip("\r\n") + nl + line + nl
    if not fm2.startswith(nl):
        fm2 = nl + fm2.lstrip("\r\n")
    if not fm2.endswith(nl):
        fm2 = fm2.rstrip("\r\n") + nl
    return "---" + fm2 + "---" + nl + body


def with_aliases(text: str, names: list[str]) -> str:
    """Дописать псевдонимы (`aliases:`) в шапку."""
    return with_list_field(text, "aliases", names)
