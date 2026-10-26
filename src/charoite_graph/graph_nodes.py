"""Сверка разговора с узлами графа: кто/что упомянуто и что было раньше.

Живая встреча должна сама находить старые договорённости (ревью 15.08):
узлы графа — файлы папок узлов схемы хранилища — это канонические точки входа
в историю, и конвейер после каждой встречи дописывает в них секцию истории
(новые записи СВЕРХУ). Здесь — локальный лукап без brain-сервера и без LLM:
стемы имён узлов против стемов текста, дайджест «что было раньше» прямо из
файла узла. Какие папки — узлы, где люди, какие заголовки — история и какие
ссылки ведут на встречи, говорит схема (`graph_schema`), а не литералы модуля.

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
import unicodedata
import threading

from charoite_graph import frontmatter
from charoite_graph import graph_names
from charoite_graph import redirects
from charoite_graph import text_norm
from charoite_graph.graph_schema import GraphSchema

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


#: Регистр и ё→е поверх единой формы Unicode — тот же объект, что
#: `text_norm.norm` (одно место нормализаций пакета); имя модуля остаётся для
#: стеммера и его читателей.
norm = text_norm.norm


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
    """Слова текста в порядке появления (дефисные — одним словом).

    Форма Unicode собирается ЗДЕСЬ, до разрезки: в разложенном имени «Ёлка»
    буква ё — это «е» плюс отдельные точки, а класс `_WORD` комбинирующих
    знаков не знает и рвёт слово надвое. Нормализация в `norm` этого не
    спасает — она видит уже обрывки (DS и GLM независимо, круг 3 по №291).
    Swift-стеммер приложения работает по graphemes и не рвёт — здесь тот же
    результат достигается сборкой формы на входе."""
    if not text.isascii() and not unicodedata.is_normalized("NFC", text):
        text = unicodedata.normalize("NFC", text)
    return [m.group(0) for m in _WORD.finditer(text)]


def _has_digit(s: str) -> bool:
    return any(c.isdigit() for c in s)


def _is_code(s: str) -> bool:
    """Код узла — буква И цифра в одном токене («1С», «s3», «м4»). Чисто число
    кодом не считается: в речи это количество, год, номер слайда или время
    (№776: «1» и «8» поднимали «Продукт 1.8», «2026» — что угодно с годом)."""
    return _has_digit(s) and any(c.isalpha() for c in s)


def _code_owners(nodes) -> dict[str, int]:
    """Код → сколько УЗЛОВ его носят (имя и все alias одного узла — один
    владелец). Ключ — стем, как в сверке."""
    owners: dict[str, int] = {}
    for node in nodes:
        for code in {t for var in (node.name_stems, *node.alias_stems) for t in var if _is_code(t)}:
            owners[code] = owners.get(code, 0) + 1
    return owners


@dataclasses.dataclass
class Node:
    path: pathlib.Path
    folder: str            # имя папки узла, как его подаёт обход (роль — у схемы)
    name: str              # имя файла без .md
    name_stems: tuple[str, ...] = ()      # стемы имени
    alias_stems: tuple[tuple[str, ...], ...] = ()  # стемы каждого alias
    mtime: float = 0.0
    size: int = -1
    digest_lines: tuple[str, ...] = ()
    #: aliases строками, как в шапке: маска ПДн ищет их написание, а не стемы (№793)
    aliases: tuple[str, ...] = ()
    _: dataclasses.KW_ONLY
    #: Схема хранилища — только на время конструирования: из неё выводится
    #: `person`, а сам узел её не хранит.
    schema: dataclasses.InitVar[GraphSchema]
    #: Человек ли это — ВЫВОДИТСЯ из папки по схеме, а не передаётся: переданный
    #: флаг позволил бы оснастке подсунуть роль мимо правила (тот же класс, что
    #: закрыл `Doc.role` по №296).
    person: bool = dataclasses.field(init=False)

    def __post_init__(self, schema: GraphSchema) -> None:
        self.person = schema.is_person_folder(self.folder)


_LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _human_date(raw: str, this_year: str) -> str:
    """[[Встречи/2026-07-30_1400]] → «30.07» (чужой год — «30.07.25»)."""
    m = _STAMP.search(raw)
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return f"{d}.{mo}" if y == this_year else f"{d}.{mo}.{y[2:]}"


def _strip_links(body: str, schema: GraphSchema) -> str:
    """Вики-ссылки → читаемый текст: ссылка на встречу (правило схемы — дата в
    цели или префикс папки встреч) исчезает, дата уже вынута; содержательная
    ссылка оставляет своё имя — «ответственный [[Люди/Иван|Иван]]» не должен
    превращаться в «ответственный» (ревью 15.08 ×3)."""
    def repl(m: re.Match) -> str:
        target = m.group(1).strip()
        alias = (m.group(0).split("|", 1)[1].rstrip("]") if "|" in m.group(0)
                 else "")
        if schema.is_meeting_link(target):
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


def _digest(text: str, this_year: str, limit: int = 3, *, schema: GraphSchema) -> list[str]:
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
                out.append(_strip_links(nxt, schema)[:120])
            break
    in_history = False
    for line in lines:
        s = line.strip()
        if schema.is_history_head(s):
            in_history = True
            continue
        if in_history:
            if s.startswith("#"):
                break
            if not s.startswith("-"):
                continue
            body = s.lstrip("- ").strip()
            date = _human_date(body, this_year)
            body = _strip_links(body, schema).strip(" —–-·")
            if not body:      # голая ссылка без вклада — человеку нечего читать
                continue
            out.append((f"{date}: " if date else "") + body[:120])
            if len(out) >= limit + 1:
                break
    if not out:
        for line in lines:
            s = line.strip()
            if s and not s.startswith(("#", "-", ">", "|")):
                out.append(_strip_links(s, schema)[:120])
                break
    return out[:limit + 1]


@dataclasses.dataclass(frozen=True)
class Stub:
    """Заглушка слияния: не узел для подсказок, но её имя — старое написание
    человека или системы, и оно живёт в стенограммах (маска ПДн, №793)."""
    path: pathlib.Path
    folder: str
    name: str
    target: str | None      # «Папка/Канон.md» по `redirects.stub_target`; None — развилка
    mtime: float
    size: int


#: Исход `_load` для файла, который не прочитался: не узел и не заглушка, а дыра,
#: которую маска обязана увидеть (n793-in3 S-I5).
_UNREADABLE = object()


@dataclasses.dataclass(frozen=True)
class MaskSnapshot:
    """Один снимок для маски: узлы, заглушки и нечитаемые файлы одного `refresh`."""
    nodes: tuple[Node, ...]
    stubs: tuple[Stub, ...]
    unreadable: frozenset[pathlib.Path]


class NodeIndex:
    """Индекс узлов графа: тёплый лукап in-memory, обновление по stat.

    Двухуровневый кэш (ревью 15.08 ×2): список файлов перечитывается по
    каждому refresh (дёшево), содержимое файла — только когда его
    mtime+size изменились. Файл, пойманный посреди перезаписи конвейером
    (stat до и после чтения разошёлся), не заменяет последний удачный
    снапшот.
    """

    def __init__(self, graph_dir: pathlib.Path, this_year: str = "2026", *,
                 schema: GraphSchema):
        self.graph = pathlib.Path(graph_dir)
        self.this_year = this_year
        # Схема обязательна и без умолчания: индекс узлов строит только Чароит, и
        # забытая схема — `TypeError` здесь, а не молча пустой индекс на встрече.
        self.schema = schema
        # refresh зовут три потока демона (⚡, живой контекст, ручной ⏮):
        # без лока один меняет словарь, пока другой его итерирует (ревью
        # 15.08 ×3). Обновление собирается под локом и подменяет ссылку
        # атомарно; lookup читает снапшот ссылки без лока. Узлы и карта
        # владельцев кодов — одна пара: двумя атрибутами lookup мог бы взять
        # узлы нового снимка с картой старого. Заглушки и нечитаемые файлы (маска
        # ПДн, №793) — в той же ссылке: отдельным атрибутом маска взяла бы новые
        # узлы со старым списком нечитаемых.
        self._snap: tuple[dict[pathlib.Path, Node], dict[str, int],
                          dict[pathlib.Path, Stub], frozenset[pathlib.Path]] = ({}, {}, {}, frozenset())
        self._refresh_lock = threading.Lock()

    @property
    def _nodes(self) -> dict[pathlib.Path, Node]:
        return self._snap[0]

    def refresh(self) -> None:
        with self._refresh_lock:
            fresh: dict[pathlib.Path, Node] = {}
            stubs: dict[pathlib.Path, Stub] = {}
            unreadable: set[pathlib.Path] = set()
            old_stubs = self._snap[2]
            for folder in self.schema.node_folders:
                d = self.graph / folder
                if not d.is_dir():
                    continue
                for p in sorted(d.glob("*.md")):
                    # точка — своё правило обхода; служебное (_ЯДРА.md, агрегаты,
                    # «Служебное_*») — по схеме, как у поиска и досье
                    if p.name.startswith(".") or self.schema.is_service_name(p.name):
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        unreadable.add(p)
                        continue
                    cached = self._nodes.get(p) or old_stubs.get(p)
                    if cached and cached.mtime == st.st_mtime \
                            and cached.size == st.st_size:
                        (stubs if isinstance(cached, Stub) else fresh)[p] = cached
                        continue
                    node = self._load(p, folder, st)
                    if node is _UNREADABLE:
                        # держим прошлый снапшот файла, но помним, что этот раз он не прочитался
                        unreadable.add(p)
                        if p in self._nodes:
                            fresh[p] = self._nodes[p]
                        elif p in old_stubs:
                            stubs[p] = old_stubs[p]
                    elif isinstance(node, Stub):
                        stubs[p] = node
                    elif node is not None:
                        fresh[p] = node
            self._snap = (fresh, _code_owners(fresh.values()), stubs, frozenset(unreadable))

    def _load(self, p: pathlib.Path, folder: str, st) -> Node | Stub | object | None:
        try:
            # форма Unicode — как в поиске: узлы и индекс поиска читают один граф,
            # и разная форма одного текста расходилась бы молча (DS, круг 4 по №291)
            text = unicodedata.normalize("NFC", p.read_text(encoding="utf-8"))
            st2 = p.stat()
        except (OSError, ValueError):   # ValueError — не-UTF8 (DS r2 #451)
            return _UNREADABLE          # refresh держит прошлый снапшот и помечает файл
        if (st2.st_mtime, st2.st_size) != (st.st_mtime, st.st_size):
            return _UNREADABLE          # файл переписывается прямо сейчас
        if redirects.is_merged(text):
            # заглушка после слияния — не узел (хвост 20.08, GLM), но запись для маски
            return Stub(p, folder, p.stem, redirects.stub_target(text),
                        st2.st_mtime, st2.st_size)
        if self.schema.is_person_folder(folder) and graph_names.is_placeholder_node(p.stem):
            return None                 # «Собеседник 3» — склейка разных людей, не подсказка (GLM I6, 07.09)
        name = p.stem
        # один разбор шапки на конвейер и поиск (frontmatter.py, #451)
        alias_names = tuple(frontmatter.aliases(text, p.name))
        aliases = [tuple(stem(t) for t in tokens(a)) for a in alias_names]
        return Node(path=p, folder=folder, name=name,
                    name_stems=tuple(stem(t) for t in tokens(name)),
                    alias_stems=tuple(aliases), aliases=alias_names,
                    mtime=st2.st_mtime, size=st2.st_size,
                    digest_lines=tuple(_digest(text, self.this_year, schema=self.schema)),
                    schema=self.schema)

    # --- лукап -----------------------------------------------------------

    def lookup(self, text: str, *, strict: bool = True,
               known_names: set[str] | None = None,
               limit: int = 2) -> list[Node]:
        """Узлы, упомянутые в тексте.

        strict=True — автоматический контур (авто-⏮): точность важнее
        полноты (ревью 15.08 ×2): многословное имя должно собраться целиком
        в окне из 8 слов одной строки (реплики); однословное — прозвучать в двух разных строках,
        кроме кода (буква и цифра в токене — «1С», «S3») с единственным
        узлом-владельцем и людей, чьё имя уже известно демону как спикер
        (known_names, нормализованные). Чисто число в одиночку узел не
        поднимает: «1494» без «ИС» — количество или год чаще, чем система
        (№776). Текст — речь без меток времени и подписей говорящих
        (`Transcript.speech_tail`): участие в встрече — не упоминание.
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
        snapshot, owners = self._snap[:2]   # ссылка атомарна: refresh подменяет снимок
        for node in snapshot.values():
            variants = (node.name_stems, *node.alias_stems)
            node_known = node.person and norm(node.name) in known
            best = 0
            best_var: tuple[str, ...] = ()
            for var in variants:
                if not var:
                    continue
                if len(var) > 1:
                    # strict: окно — внутри одной реплики; конец одной и начало
                    # следующей имени не собирают (№776 out1: 5 кандидатов на 52
                    # встречах держались только на такой склейке)
                    if any(self._window_match(list(ln), var) for ln in lines) if strict \
                            else self._window_match(stems, var):
                        if len(var) > best:
                            best, best_var = len(var), var
                        continue
                    # код узла держится и без остального имени («s3» →
                    # «Хранилище S3»): в strict — только код с буквой и одним
                    # владельцем, любой прозвучавший; опознанного спикера
                    # хватает и по фамилии
                    if strict:
                        code = any(t in stems and _is_code(t) and owners.get(t) == 1
                                   for t in var)
                    else:
                        digit = next((t for t in var if _has_digit(t)), None)
                        code = bool(digit and digit in stems)
                    if code:
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
                if not strict or (_is_code(tok) and owners.get(tok) == 1) or node_known or \
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

    def snapshot_for_mask(self) -> MaskSnapshot:
        """Свежий снимок для маски ПДн: `refresh` и одна ссылка на результат."""
        self.refresh()
        nodes, _owners, stubs, unreadable = self._snap
        return MaskSnapshot(tuple(nodes.values()), tuple(stubs.values()), unreadable)
