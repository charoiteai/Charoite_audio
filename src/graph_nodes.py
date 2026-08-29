"""Сверка разговора с узлами графа: кто/что упомянуто и что было раньше.

Живая встреча должна сама находить старые договорённости (ревью 15.08):
узлы графа — Люди/Команды/Системы/Модели/Блокеры/Ядра — это канонические
точки входа в историю, и конвейер после каждой встречи дописывает в них
секцию «## Встречи» (новые записи СВЕРХУ). Здесь — локальный лукап без
brain-сервера и без LLM: стемы имён узлов против стемов текста, дайджест
«что было раньше» прямо из файла узла.

Модуль без демона и сети: индекс и правила тестируются на tmp-графе.
Стеммер — порт таблицы из ArchiveSearch.swift; эквивалентность двух
реализаций держат общие golden-векторы (tests/test_graph_nodes.py и
app/Tests/StemGoldenTests.swift) — иначе «платёжный» находился бы в
приложении и молча терялся в демоне.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
import threading

import frontmatter

# Папки узлов: русские — боевой конвейер, английские — демо-граф продукта.
NODE_FOLDERS = ("Люди", "Команды", "Системы", "Модели", "Блокеры", "Ядра",
                "People", "Teams", "Systems", "Models", "Blockers", "Cores")

# Секции истории узла: «## Встречи» пишет конвейер (новые сверху),
# «## Хроника» ведут ядра, английские — демо-граф.
HISTORY_HEADS = ("## Встречи", "## Хроника", "## Meetings", "## History")

# Русские окончания, от длинных к коротким — как в ArchiveSearch.swift.
_RU_SUFFIXES = (
    "иями", "ями", "ами", "иях", "иям", "ыми", "ими", "ому", "ему",
    "ого", "его", "ует", "уют", "ают", "яют", "ешь", "ете", "лся",
    "лась", "лись", "ться", "ый", "ий", "ах", "ях", "ам", "ям", "ой",
    "ей", "ою", "ею", "ия", "ие", "ии", "ию", "ых", "их", "ым", "им",
    "ая", "яя", "ое", "ее", "ую", "юю", "ые", "ов", "ев", "ом", "ем",
    "ет", "ит", "ат", "ят", "ла", "ло", "ли", "ть", "ы", "и", "а", "я",
    "о", "е", "у", "ю", "ь", "й",
)
_EN_SUFFIXES = ("ing", "ed", "es", "s")


def norm(s: str) -> str:
    return s.lower().replace("ё", "е")


def stem(word: str) -> str:
    """Стем слова — зеркало ArchiveSearch.stem (golden-векторы в тестах)."""
    w = norm(word)
    if len(w) <= 4:
        return w
    first = w[0]
    is_latin = first.isascii() and first.isalpha()
    table = _EN_SUFFIXES if is_latin else _RU_SUFFIXES
    for suf in table:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            cut = w[: -len(suf)]
            return stem(cut) if is_latin else cut
    return w


_WORD = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+(?:-[0-9a-zA-Zа-яА-ЯёЁ]+)*")


def tokens(text: str) -> list[str]:
    """Слова текста в порядке появления (дефисные — одним словом)."""
    return [m.group(0) for m in _WORD.finditer(text)]


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


@dataclasses.dataclass
class Node:
    path: pathlib.Path
    folder: str            # имя папки узла («Люди», «Системы», …)
    name: str              # имя файла без .md
    name_stems: tuple[str, ...] = ()      # стемы имени
    alias_stems: tuple[tuple[str, ...], ...] = ()  # стемы каждого alias
    mtime: float = 0.0
    size: int = -1
    digest_lines: tuple[str, ...] = ()

    @property
    def person(self) -> bool:
        return self.folder in ("Люди", "People")


_LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _human_date(raw: str, this_year: str) -> str:
    """[[Встречи/2026-07-30_1400]] → «30.07» (чужой год — «30.07.25»)."""
    m = _STAMP.search(raw)
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return f"{d}.{mo}" if y == this_year else f"{d}.{mo}.{y[2:]}"


def _strip_links(body: str) -> str:
    """Вики-ссылки → читаемый текст: ссылка на встречу исчезает (дата уже
    вынута), содержательная ссылка оставляет своё имя — «ответственный
    [[Люди/Иван|Иван]]» не должен превращаться в «ответственный» (ревью
    15.08 ×3)."""
    def repl(m: re.Match) -> str:
        target = m.group(1).strip()
        alias = (m.group(0).split("|", 1)[1].rstrip("]") if "|" in m.group(0)
                 else "")
        if _STAMP.search(target) or target.startswith(("Встречи", "Meetings")):
            return ""
        return alias or target.rsplit("/", 1)[-1]
    return _LINK.sub(repl, body)


def _split_frontmatter(text: str) -> tuple[str, str]:
    """(frontmatter, тело): status: из YAML не должен перехватывать видимый
    статус узла (ревью 15.08 ×3)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end + 4], text[end + 4:]
    return "", text


def _digest(text: str, this_year: str, limit: int = 3) -> list[str]:
    """«Что было раньше» из файла узла: статус + НАЧАЛО секции истории.

    Конвейер вставляет новую запись сразу после заголовка секции — порядок
    новые → старые, поэтому берётся начало, а не хвост (ревью 15.08 ×2).
    Строки без содержания после ссылки (link-only) пропускаются: у систем
    конвейер часто пишет голую ссылку. Семантика строк — «из истории узла»,
    не «решение»: у людей там вклад, а не обязательно договорённость.
    Если ни статуса, ни истории — первая содержательная строка описания:
    полезный узел без хроники не должен давать пустой дайджест.
    """
    out: list[str] = []
    _fm, body_text = _split_frontmatter(text)
    lines = body_text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        low = s.lower()
        if low.startswith(("статус:", "status:", "**статус", "## статус",
                           "## status")):
            nxt = s.split(":", 1)[1].strip(" *") if ":" in s else ""
            for j in range(i + 1, min(i + 4, len(lines))):
                if nxt:
                    break
                nxt = lines[j].strip(" -*")
            if nxt:
                out.append(_strip_links(nxt)[:120])
            break
    in_history = False
    for line in lines:
        s = line.strip()
        if any(s.startswith(h) for h in HISTORY_HEADS):
            in_history = True
            continue
        if in_history:
            if s.startswith("#"):
                break
            if not s.startswith("-"):
                continue
            body = s.lstrip("- ").strip()
            date = _human_date(body, this_year)
            body = _strip_links(body).strip(" —–-·")
            if not body:      # голая ссылка без вклада — человеку нечего читать
                continue
            out.append((f"{date}: " if date else "") + body[:120])
            if len(out) >= limit + 1:
                break
    if not out:
        for line in lines:
            s = line.strip()
            if s and not s.startswith(("#", "-", ">", "|")):
                out.append(_strip_links(s)[:120])
                break
    return out[:limit + 1]


class NodeIndex:
    """Индекс узлов графа: тёплый лукап in-memory, обновление по stat.

    Двухуровневый кэш (ревью 15.08 ×2): список файлов перечитывается по
    каждому refresh (дёшево), содержимое файла — только когда его
    mtime+size изменились. Файл, пойманный посреди перезаписи конвейером
    (stat до и после чтения разошёлся), не заменяет последний удачный
    снапшот.
    """

    def __init__(self, graph_dir: pathlib.Path, this_year: str = "2026"):
        self.graph = pathlib.Path(graph_dir)
        self.this_year = this_year
        self._nodes: dict[pathlib.Path, Node] = {}
        # refresh зовут три потока демона (⚡, живой контекст, ручной ⏮):
        # без лока один меняет словарь, пока другой его итерирует (ревью
        # 15.08 ×3). Обновление собирается под локом и подменяет ссылку
        # атомарно; lookup читает снапшот ссылки без лока.
        self._refresh_lock = threading.Lock()

    def refresh(self) -> None:
        with self._refresh_lock:
            fresh: dict[pathlib.Path, Node] = {}
            for folder in NODE_FOLDERS:
                d = self.graph / folder
                if not d.is_dir():
                    continue
                for p in sorted(d.glob("*.md")):
                    if p.name.startswith(("_", ".")):
                        continue   # _ЯДРА.md и служебные агрегаты — не узлы
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    cached = self._nodes.get(p)
                    if cached and cached.mtime == st.st_mtime \
                            and cached.size == st.st_size:
                        fresh[p] = cached
                        continue
                    node = self._load(p, folder, st)
                    if node is not None:
                        fresh[p] = node
            self._nodes = fresh

    def _load(self, p: pathlib.Path, folder: str, st) -> Node | None:
        try:
            text = p.read_text(encoding="utf-8")
            st2 = p.stat()
        except OSError:
            return self._nodes.get(p)   # держим прошлый снапшот
        if (st2.st_mtime, st2.st_size) != (st.st_mtime, st.st_size):
            return self._nodes.get(p)   # файл переписывается прямо сейчас
        name = p.stem
        # один разбор шапки на конвейер и поиск (frontmatter.py, #451)
        aliases = [tuple(stem(t) for t in tokens(a)) for a in frontmatter.aliases(text)]
        return Node(path=p, folder=folder, name=name,
                    name_stems=tuple(stem(t) for t in tokens(name)),
                    alias_stems=tuple(aliases),
                    mtime=st2.st_mtime, size=st2.st_size,
                    digest_lines=tuple(_digest(text, self.this_year)))

    # --- лукап -----------------------------------------------------------

    def lookup(self, text: str, *, strict: bool = True,
               known_names: set[str] | None = None,
               limit: int = 2) -> list[Node]:
        """Узлы, упомянутые в тексте.

        strict=True — автоматический контур (авто-⏮): точность важнее
        полноты (ревью 15.08 ×2): многословное имя должно собраться целиком
        в окне из 8 слов; однословное — прозвучать в двух разных строках,
        кроме токенов с цифрой (коды «1С», «ИС 1494») и людей, чьё имя уже
        известно демону как спикер (known_names, нормализованные).
        strict=False — явные запросы (⚡-вопрос, ручной ⏮): достаточно
        одного полного вхождения имени.

        Имя, ведущее к нескольким узлам, в strict-режиме молчит: показать
        не того человека хуже, чем не показать никого.
        """
        words = tokens(text)
        stems = [stem(w) for w in words]
        norms = [norm(w) for w in words]
        lines = [tuple(stem(w) for w in tokens(ln)) for ln in text.splitlines()]
        known = {norm(k) for k in (known_names or set())}

        found: list[tuple[int, Node, tuple[str, ...]]] = []
        snapshot = self._nodes   # ссылка атомарна: refresh подменяет словарь
        for node in snapshot.values():
            variants = (node.name_stems, *node.alias_stems)
            node_known = node.person and norm(node.name) in known
            best = 0
            best_var: tuple[str, ...] = ()
            for var in variants:
                if not var:
                    continue
                if len(var) > 1:
                    if self._window_match(stems, var):
                        if len(var) > best:
                            best, best_var = len(var), var
                        continue
                    # код с цифрой уникален и без остального имени («1494» →
                    # «ИС 1494»); опознанного спикера хватает и по фамилии
                    digit = next((t for t in var if _has_digit(t)), None)
                    if digit and digit in stems:
                        if best < 1:
                            best, best_var = 1, var
                    elif node_known and any(t in stems for t in var):
                        if best < 1:
                            best, best_var = 1, var
                    continue
                tok = var[0]
                if tok not in stems:
                    continue
                if node.person:
                    # стеммер режет «Иванов» до «иван» — по одному стему
                    # «Иван» цеплял бы «Иванова». Однословный человек
                    # матчится только словом не короче своего имени
                    # (падежные формы длиннее, чужое короткое имя — нет).
                    ok_form = node_known or any(
                        norms[i] == norm(node.name) or
                        (stems[i] == tok and len(norms[i]) >= len(norm(node.name)))
                        for i in range(len(stems)) if stems[i] == tok)
                    if not ok_form:
                        continue
                if not strict or _has_digit(tok) or node_known or \
                        sum(1 for ln in lines if tok in ln) >= 2:
                    if best < 1:
                        best, best_var = 1, var
            if best:
                found.append((best, node, best_var))

        if strict:
            # одно совпавшее имя/alias — несколько узлов: авто-контур молчит
            by_key: dict[tuple[str, ...], int] = {}
            for _b, _n, var in found:
                by_key[var] = by_key.get(var, 0) + 1
            found = [f for f in found if by_key[f[2]] == 1]

        found.sort(key=lambda bn: (-bn[0], bn[1].name))
        return [n for _b, n, _v in found[:limit]]

    @staticmethod
    def _window_match(stems: list[str], var: tuple[str, ...], window: int = 8) -> bool:
        need = set(var)
        for i in range(len(stems)):
            if stems[i] in need:
                got = {s for s in stems[i:i + window] if s in need}
                if got == need:
                    return True
        return False

    def digest(self, node: Node, with_name: bool = True) -> list[str]:
        """Строки «что было раньше» для нити — без символа ⏮ (его рисует
        рендер строки). with_name — авто-вставка идёт в ТЕКУЩУЮ тему, и имя
        узла обязано быть в самой строке (ревью 15.08 ×2); ручной ⏮ кладёт
        строки под тему с именем узла, там префикс был бы дублем."""
        if not with_name:
            return list(node.digest_lines)
        return [f"{node.name} · {line}" for line in node.digest_lines]

    @property
    def size(self) -> int:
        return len(self._nodes)
