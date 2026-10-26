"""Маска персональных данных перед облаком (№793): имена людей и систем → ID и обратно.

Модуль без ввода-вывода: формы приходят готовыми (их строит `cloud_door` из индекса
узлов графа), спаны NER — списком. Отсюда только строки и таблица ID.

Одно определение «вхождения формы» (`Entry` + `_base`) и два обхода по нему:
маска идёт по словам текста и выбирает самые длинные совпадения, постусловие —
регуляркой-деревом по уже маскированному тексту. Это другой обход тех же форм, а не
другой алгоритм: имя, которого нет среди форм, постусловие не увидит (n793-out1).
Найдено хоть одно вхождение после маски — `MaskRefused`, в облако ничего не уходит.
Сравнение — без различия «ё» и «е» (`_yo`): распознавание пишет «Петр» вместо «Пётр».

ID живут в пределах одного отправления, номера групп — по порядку появления в тексте:
`⟦Ч3.1⟧` — третья группа-человек, форма 1;
`⟦С2.1⟧` — система; `⟦Ч~5.1⟧` — форма без однозначного узла (однофамильцы, спан NER).
Модель видит, что `Ч3.1` и `Ч3.2` — один человек; `unmask` возвращает точную форму
вхождения, токен группы без формы — имя узла (канон), остальное — `⟦?⟧` и счётчик.
"""
from __future__ import annotations

import dataclasses
import re
import unicodedata
from typing import Callable, Iterable, Sequence

OPEN, CLOSE = "⟦", "⟧"
PERSON, SYSTEM = "Ч", "С"
#: Метка на месте токена, который не удалось развернуть: модель придумала ID,
#: ответ оборвался посреди токена, `Ч~` без формы.
UNRESOLVED = "⟦?⟧"

#: Окончания склоняемых фамилий и прилагательных — как у `lexicon._ENDINGS`: основа
#: «Вельск-» ловит «Вельского/Вельскому». Хвост после такой основы — до 5 букв.
_ENDINGS = ("ского", "скому", "ский", "ским", "ском", "ская", "ской", "скую",
            "ий", "ый", "ой", "его", "ого", "ему", "ому", "им", "ым", "ом",
            "ая", "яя", "ую", "юю")
_VOWEL_END = "аяеиоуыюйь"
#: окончания для проверки «общее ли слово» по общему словарю (`_distinctive`)
_INFLECT = ("а", "я", "ы", "и", "е", "у", "ю", "о", "ь", "й", "ом", "ем", "ой", "ей",
            "ов", "ев", "ам", "ям", "ами", "ями", "ах", "ях")
_LETTERS = r"[^\W\d_]"
#: Слово текста: буквы и цифры («Ф1»), без подчёркивания.
_WORD = re.compile(r"[^\W_]+")
_TOKEN = re.compile(r"⟦\s*([ЧС])\s*(~?)\s*(\d+)\s*(?:\.\s*(\d+)\s*)?⟧")
#: Всё, что осталось от токена после разбора: обрывок «⟦Ч3.» в конце ответа,
#: выдуманный «⟦Х⟧».
#: Незакрытая скобка забирает только знаки токена, а не текст за ней (n793-out1 S-M1).
_STRAY = re.compile(r"⟦[^⟧\n]{0,16}⟧|⟦[ЧС~?\d.]{0,16}")


class MaskRefused(Exception):
    """Маска не смогла: текст в облако не уходит. Сообщение — для лога, без имён."""


@dataclasses.dataclass(frozen=True)
class Entry:
    """Одна форма: слова-основы (нижний регистр) с потолком падежного хвоста у каждого."""
    words: tuple[str, ...]
    tails: tuple[int, ...]
    kind: str                   # PERSON / SYSTEM
    group: str | None           # канон узла; None — форма у нескольких узлов
    capital: bool = False       # люди: первое слово с заглавной («Белов», не «белов»)
    exact: bool = False         # аббревиатура: регистр основы точный («ЗЛК», не «злк»)


@dataclasses.dataclass(frozen=True)
class NodeForms:
    """Узел графа глазами маски: имя файла, aliases, люди ли это."""
    name: str
    aliases: tuple[str, ...] = ()
    person: bool = True


@dataclasses.dataclass
class Forms:
    entries: list[Entry]
    #: основа первого слова → формы, которые с неё начинаются
    by_first: dict[str, list[Entry]] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        for e in self.entries:
            self.by_first.setdefault(e.words[0], []).append(e)
        self.max_tail = max((max(e.tails) for e in self.entries), default=0)


@dataclasses.dataclass
class Group:
    canon: str | None
    forms: list[str]


@dataclasses.dataclass
class Masked:
    """Маскированный текст и таблица ID этого отправления (живёт только в памяти)."""
    text: str
    level: str
    groups: dict[str, Group] = dataclasses.field(default_factory=dict)
    replaced: int = 0
    #: что маска знает о своих пределах (нечитаемые узлы систем и т. п.) — в лог
    notes: list[str] = dataclasses.field(default_factory=list)

    def summary(self) -> str:
        """Строка лога без имён: уровень и сколько чего заменено."""
        if self.level == "off":
            return "маска выключена владельцем (sufler.cloud_mask: false)"
        people = sum(1 for k in self.groups if k.startswith(PERSON))
        systems = len(self.groups) - people
        line = f"маска {self.level}: замен {self.replaced}, людей {people}, систем {systems}"
        return line + "".join(f"; {n}" for n in self.notes)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _yo(text: str) -> str:
    """«ё» → «е» с сохранением регистра и длины: офсеты спанов остаются верными."""
    return text.replace("ё", "е").replace("Ё", "Е")


def _is_abbrev(word: str) -> bool:
    """2–6 букв капсом. Двухбуквенная — только целым словом (хвост 0 в `_base`):
    иначе «ВО» съело бы «вот» и «вон»."""
    return 2 <= len(word) <= 6 and word.isupper() and word.isalpha()


def _distinctive(word: str, known: Callable[[str], bool]) -> bool:
    """Отличает ли слово имя системы от обычной речи: аббревиатура, буквы с
    цифрами («S3») или слово, которого нет в общем словаре. «Поддержка»,
    «Труба», «вторник» и голое число «28» — нет: замер 26.10 на трёх встречах
    дал сотни замен общих слов из имён и псевдонимов узлов систем."""
    if _is_abbrev(word):
        return True
    has_digit = any(c.isdigit() for c in word)
    has_letter = any(c.isalpha() for c in word)
    if has_digit:
        return has_letter
    # общий словарь знает не каждую форму («репликации» есть, «репликация» нет):
    # общее — если общеизвестна любая форма с той же основой
    base = _base(word)[0]
    return not any(known(w) for w in {word.lower(), base, *(base + e for e in _INFLECT)})


def _base(word: str) -> tuple[str, int, bool]:
    """Основа слова-формы, потолок хвоста и точный ли регистр.

    Аббревиатура и слово с цифрой — точно, хвост до 2 строчных («ЗЛКа»). Фамилия на
    «-ский/-ой…» — основа без окончания при остатке ≥ 4 буквы, хвост до 5. Слово на
    гласную — без последней буквы («Анн-а/е/у/ой»), хвост до 3; короткое (< 4) — хвост
    до 2: «Ян» не должно ловить «января», «Анн-» — «аннотацию» (там ещё и заглавная).
    """
    if _is_abbrev(word) or any(c.isdigit() for c in word):
        return _yo(word), (2 if word.isalpha() and len(word) > 2 else 0), True
    w = _yo(word.lower())
    for e in _ENDINGS:
        if len(w) - len(e) >= 4 and w.endswith(e):
            return w[: -len(e)], 5, False
    if len(w) >= 3 and w[-1] in _VOWEL_END:
        # «Аня» → «ан»: основе из двух букв — хвост до 2 («Аней»), иначе «Дел» ловит «делаю»
        return w[:-1], (3 if len(w) >= 4 else 2), False
    return w, (3 if len(w) >= 4 else 2), False


def _words(name: str) -> list[str]:
    return _WORD.findall(_nfc(name))


def same_name(a: str, b: str) -> bool:
    """Одно ли это имя с точностью до падежа: «Ивану» и «Иван», «Анне Беловой»
    и «Анна Белова». Слово к слову по основе `_base`."""
    wa, wb = _words(a), _words(b)
    return bool(wa) and len(wa) == len(wb) and all(
        _base(x)[0].lower() == _base(y)[0].lower() for x, y in zip(wa, wb))


def build_forms(nodes: Iterable[NodeForms], *,
                stubs: Iterable[tuple[str, str | None, bool]] = (),
                owner: str = "", common: Iterable[str] = (),
                known: Callable[[str], bool] = lambda _w: False) -> Forms:
    """Формы маски из узлов графа.

    `stubs` — заглушки слияния: (имя заглушки, канон живого узла или None — цель не
    найдена, человек ли — по папке заглушки). Имя заглушки — старое написание, оно
    живёт в стенограммах после слияния; без цели оно маскируется формой без узла (`Ч~`). `owner` — имя
    владельца из конфига: его может не быть среди узлов. `common` — слова, которые
    владелец разрешил отправлять как есть (файл `_маска_общие.md`). `known` —
    есть ли слово (в нижнем регистре) в общем словаре языка: имя системы
    маскируется только своей отличительной частью (`_distinctive`).
    """
    common_l = {c.lower() for c in common}
    nodes = list(nodes)
    if owner and not any(n.person and n.name == owner for n in nodes):
        nodes.append(NodeForms(owner))
    by_name = {n.name: n for n in nodes}
    # Чья основа: однофамильцы и тёзки дают форму без узла (правило `lexicon`:
    # основа у множества узлов — правило не создаётся, n793-in2 H-I4).
    owners: dict[tuple[str, str], set[str]] = {}
    raw: list[tuple[list[str], str, str | None]] = []   # слова, вид, канон

    def add(words: list[str], kind: str, canon: str | None) -> None:
        if not words:
            return
        raw.append((words, kind, canon))
        if len(words) == 1:
            owners.setdefault((kind, _base(words[0])[0]), set()).add(canon or f"?{words[0]}")

    def add_name(name: str, kind: str, canon: str | None) -> None:
        ws = _words(name)
        if kind == PERSON:
            # «Иванов (аналитик)»: в имени человека — только слова с заглавной
            ws = [w for w in ws if w[:1].isupper() and len(w) >= 2]
            for w in ws:
                add([w], kind, canon)
            if len(ws) > 1:
                add(ws, kind, canon)
            return
        # Система: имя целиком — если в нём есть отличительное слово; отдельно —
        # только отличительные слова («ЗЛК» из «Шлюз ЗЛК», не «Шлюз»).
        if not any(_distinctive(w, known) for w in ws):
            return
        add(ws, kind, canon)
        if len(ws) > 1:
            for w in ws:
                if _distinctive(w, known):
                    add([w], kind, canon)

    for n in nodes:
        for name in (n.name, *n.aliases):
            add_name(name, PERSON if n.person else SYSTEM, n.name)
    for stub, target, person in stubs:
        node = by_name.get(target) if target else None
        # вид — по папке заглушки, не по цели: заглушка системы без цели не человек
        add_name(stub, PERSON if person else SYSTEM, node.name if node is not None else None)

    seen: set[tuple] = set()
    entries: list[Entry] = []
    for words, kind, canon in raw:
        if len(words) == 1:
            if words[0].lower() in common_l or len(words[0]) < 2 \
                    or not any(c.isalpha() for c in words[0]):
                continue
            if kind == SYSTEM and not _is_abbrev(words[0]) and words[0][:1].islower():
                continue        # строчное однословное имя системы — обычное слово
            base, tail, exact = _base(words[0])
            if kind == SYSTEM and not exact and len(base) < 4:
                tail = min(tail, 1)     # «Вор» не должно ловить «ворота», «Дела» — «делать»
            if len(owners.get((kind, base), ())) > 1:
                canon = None
            # заглавная нужна имени-слову («Роза» / «роза»); фамилию, которой нет в общем
            # словаре, распознавание пишет и строчными («квакин», №799 26.10)
            capital = kind == PERSON and not _distinctive(words[0], known)
            ent = Entry((base,), (tail,), kind, canon, capital=capital, exact=exact)
            if kind == PERSON and not exact:
                for alt in _fleeting(base):
                    fe = Entry((alt,), (tail,), kind, canon, capital=capital)
                    if (fe.words, kind, canon) not in seen:
                        seen.add((fe.words, kind, canon))
                        entries.append(fe)
            if exact and base.isalpha() and len(base) >= 3:
                # распознавание пишет аббревиатуру словом: «Орисе», «Злком»
                title = Entry((base.capitalize(),), (tail,), kind, canon,
                              capital=kind == PERSON, exact=True)
                if (title.words, kind, canon) not in seen:
                    seen.add((title.words, kind, canon))
                    entries.append(title)
        else:
            bases = [_base(w) for w in words]
            ent = Entry(tuple(b.lower() for b, _t, _e in bases), tuple(t for _b, t, _e in bases),
                        kind, canon, capital=kind == PERSON)
        key = (ent.words, ent.kind, ent.group)
        if key not in seen:
            seen.add(key)
            entries.append(ent)
    return Forms(entries)


def _fleeting(base: str) -> list[str]:
    """Основа без беглой гласной: «павел» → «павл» («Павла»), «лев» → «льв»,
    «заяц» → «зайц». Только у людей: форма с заглавной, лишняя основа безвредна."""
    if len(base) < 3 or base[-1] in _VOWEL_END or base[-2] not in "еоя":
        return []
    if base[-2] == "я":
        return [base[:-2] + "й" + base[-1]]
    return [base[:-2] + base[-1], base[:-2] + "ь" + base[-1]]


# ------------------------------------------------------------------ маска

def _word_fits(word: str, base: str, tail: int, exact: bool) -> bool:
    if exact:
        rest = word[len(base):]
        return word.startswith(base) and len(rest) <= tail and (not rest or rest.islower())
    w = word.lower()
    return w.startswith(base) and len(w) - len(base) <= tail


def _dictionary_spans(text: str, forms: Forms) -> list[tuple[int, int, Entry]]:
    """Все вхождения форм по словам текста; пересечения решает `_pick`."""
    text = _yo(text)
    words = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(text)]
    found: list[tuple[int, int, Entry]] = []
    for i, (start, end, word) in enumerate(words):
        low = word.lower()
        cands: list[Entry] = []
        for cut in range(0, min(forms.max_tail, len(low) - 1) + 1):
            cands.extend(forms.by_first.get(low[: len(low) - cut] if cut else low, ()))
        if word[:1].isupper() or any(c.isdigit() for c in word):
            for cut in range(0, 3):
                if len(word) - cut >= 2:
                    cands.extend(forms.by_first.get(word[: len(word) - cut], ()))
        for e in cands:
            if e.capital and not word[:1].isupper():
                continue
            if not _word_fits(word, e.words[0], e.tails[0], e.exact):
                continue
            j, stop = i, end
            ok = True
            for base, tail in zip(e.words[1:], e.tails[1:]):
                j += 1
                if j >= len(words) or text[stop:words[j][0]].strip() \
                        or not _word_fits(words[j][2], base, tail, False):
                    ok = False
                    break
                stop = words[j][1]
            if ok:
                found.append((start, stop, e))
    return found


def _pick(spans: list[tuple[int, int, object]]) -> list[tuple[int, int, object]]:
    """Самые длинные непересекающиеся: длиннее — раньше, при равной длине — раньше по тексту."""
    taken: list[tuple[int, int, object]] = []
    for s in sorted(spans, key=lambda x: (-(x[1] - x[0]), x[0])):
        if all(s[1] <= t[0] or s[0] >= t[1] for t in taken):
            taken.append(s)
    return sorted(taken, key=lambda x: x[0])


def _ner_noise(text: str, start: int, stop: int, kind: str) -> bool:
    """Спан модели имён, который на деле — речь: «Ну», «Вот», «Коллеги» в начале
    реплики (то же слово строчными есть в этом же тексте) и запинки «А-а», «Не-не».
    Замер 26.10: Slovnet на стенограммах отдаёт такие спаны десятками. Известных
    людей и системы держит словарь графа, это сито касается только модели.
    Однословный спан человека — шум, если стоит в начале реплики или фразы и то же
    слово строчными есть в тексте. Правило — только для людей: системы в речи пишутся
    строчными сплошь («аисе»), и замер терял их (60 → 59 из 68)."""
    words = _WORD.findall(text[start:stop])
    if not words:
        return True
    if len({w.lower() for w in words}) == 1 and len(words) > 1:
        return True
    if kind == PERSON and len(words) == 1 and words[0][:1].isupper():
        # только в начале реплики или фразы: гость «Вера» посреди фразы — имя,
        # даже если «вера» строчными есть в тексте (n793-out1 S-I5)
        if not _phrase_start(text, start):
            return False
        low = words[0].lower()
        return re.search(rf"(?<![^\W_]){re.escape(low)}(?![^\W_])", text) is not None
    return False


def _phrase_start(text: str, start: int) -> bool:
    """Слово стоит в начале реплики или фразы: распознавание ставит там заглавную любому слову."""
    before = text[text.rfind("\n", 0, start) + 1:start]
    return not before.strip() or re.search(r"[.!?:…]\s*$", before) is not None


def rare_word_spans(text: str, ner: Sequence[tuple[int, int, str]],
                    known: Callable[[str], bool]) -> list[tuple[int, int, str]]:
    """Имена, которых нет ни в графе, ни у модели: слово кириллицей с заглавной, которого
    нет в общем словаре, — посреди фразы, обращением с запятой («Гошан, сможешь…») или
    вплотную к имени от модели («Пыжов Андрей»). Замер №799 26.10: уменьшительное владельца и фамилия гостя
    ушли открытыми, Slovnet их не видел. Вид — человек без узла."""
    out: list[tuple[int, int, str]] = []
    persons = [(s, e) for s, e, k in ner if k == PERSON]
    for m in _WORD.finditer(text):
        w = m.group(0)
        if len(w) < 3 or not (w[:1].isupper() and w[1:].islower()) \
                or not all("а" <= c.lower() <= "я" or c.lower() == "ё" for c in w):
            continue
        if not _distinctive(w, known):
            continue
        # то же слово строчными в этом тексте — термин или речь, а не имя («бакеты»)
        if re.search(rf"(?<![^\W_]){re.escape(w.lower())}(?![^\W_])", text):
            continue
        near = any(not text[e:m.start()].strip() and m.start() - e <= 2 or
                   not text[m.end():s].strip() and s - m.end() <= 2 for s, e in persons)
        # в начале фразы заглавная — у любого слова; обращение («Гошан, сможешь…») — с запятой
        address = text[m.end():m.end() + 1] == ","
        if near or address or not _phrase_start(text, m.start()):
            out.append((m.start(), m.end(), PERSON))
    return out


def _ner_pieces(text: str, ner: Sequence[tuple[int, int, str]],
                covered: list[tuple[int, int, object]]) -> list[tuple[int, int, str]]:
    """Спаны NER за вычетом занятого словарём: словарь выигрывает пересечения, а
    остаток спана («Иван Сидоров», где «Иван» взял словарь) маскируется сам."""
    out: list[tuple[int, int, str]] = []
    words = [(m.start(), m.end()) for m in _WORD.finditer(text)]
    for s, e, kind in ner:
        cuts = [(s, e)]
        for cs, ce, _x in covered:
            nxt = []
            for a, b in cuts:
                if ce <= a or cs >= b:
                    nxt.append((a, b))
                    continue
                if a < cs:
                    nxt.append((a, cs))
                if ce < b:
                    nxt.append((ce, b))
            cuts = nxt
        for a, b in cuts:
            # до границ слов: спан, оборванный посреди слова, оставил бы хвост
            # имени открытым («Сидоро|в»)
            ws = [w for w in words if w[1] > a and w[0] < b]
            if ws:
                out.append((ws[0][0], ws[-1][1], kind))
    return out


def prepare(text: str) -> str:
    """Текст до маски и до NER: NFC и без скобок ID.

    Скобки ID в исходнике — подделка токена: модель развернула бы её в имя
    (n793-in2 S-I4). Имена их не содержат, поэтому замена на обычные скобки.
    Длина не меняется только после NFC, поэтому NER зовут по уже подготовленному тексту."""
    return _nfc(text).replace(OPEN, "[").replace(CLOSE, "]")


def mask(text: str, forms: Forms, ner: Sequence[tuple[int, int, str]] = (), *,
         level: str = "basic") -> Masked:
    """Маскировать текст. `ner` — спаны (start, end, PERSON|SYSTEM) по `prepare(text)`.

    Бросает `MaskRefused`, если после маски словарь всё ещё находит форму."""
    text = prepare(text)
    dict_spans = _pick(_dictionary_spans(text, forms))
    pieces = _ner_pieces(text, [x for x in ner if not _ner_noise(text, *x)], dict_spans)
    spans = _pick([*dict_spans, *pieces])
    groups: dict[str, Group] = {}
    key_to_id: dict[tuple, str] = {}
    counters: dict[str, int] = {}
    out: list[str] = []
    pos = 0
    for s, e, what in spans:
        surface = text[s:e]
        if isinstance(what, Entry):
            kind, canon = what.kind, what.group
            gkey = (kind, canon) if canon else (kind, "~", " ".join(what.words))
        else:
            kind, canon = what, None
            # падежные формы одного имени — одна группа, как у словаря (n793-out1 DS-7)
            gkey = (kind, "~", " ".join(_base(w)[0] for w in _WORD.findall(surface)))
        gid = key_to_id.get(gkey)
        if gid is None:
            prefix = kind + ("" if canon else "~")
            counters[prefix] = counters.get(prefix, 0) + 1
            gid = f"{prefix}{counters[prefix]}"
            key_to_id[gkey] = gid
            groups[gid] = Group(canon, [])
        g = groups[gid]
        if surface not in g.forms:
            g.forms.append(surface)
        out.append(text[pos:s])
        out.append(f"{OPEN}{gid}.{g.forms.index(surface) + 1}{CLOSE}")
        pos = e
    out.append(text[pos:])
    masked = Masked("".join(out), level, groups, len(spans))
    left = postcondition_hits(masked.text, forms)
    if left:
        raise MaskRefused(f"после маски словарь видит {left} форм — отправка отменена")
    return masked


# ------------------------------------------------------------- постусловие

def _trie_regex(words: Iterable[str]) -> str:
    """Дерево строк → регулярка без перебора альтернатив по каждой позиции."""
    trie: dict = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = True

    def walk(node: dict) -> str:
        end = "" in node
        alts = [re.escape(ch) + walk(sub) for ch, sub in sorted(node.items()) if ch]
        if not alts:
            return ""
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        return f"(?:{body})?" if end else body

    return walk(trie)


def postcondition_hits(masked_text: str, forms: Forms) -> int:
    """Сколько форм словаря видно в маскированном тексте — другим алгоритмом, чем маска.

    Однословные формы ищутся деревом основ, многословные — фразой; правила
    заглавной, точного регистра и хвоста — те же поля `Entry`."""
    plain = _yo(_TOKEN.sub(" ", _nfc(masked_text)))
    singles: dict[str, list[Entry]] = {}
    phrases: list[Entry] = []
    for e in forms.entries:
        if len(e.words) == 1:
            singles.setdefault(e.words[0].lower(), []).append(e)
        else:
            phrases.append(e)
    hits = 0
    if singles:
        rx = re.compile(rf"(?<![^\W_])({_trie_regex(singles)})({_LETTERS}*)(?![^\W_])",
                        re.IGNORECASE)
        for m in rx.finditer(plain):
            word = m.group(0)
            # дерево берёт самую длинную основу; короче её тоже могли быть формой
            for cut in range(len(m.group(1)), 0, -1):
                for e in singles.get(word[:cut].lower(), ()):
                    if e.capital and not word[:1].isupper():
                        continue
                    if _word_fits(word, e.words[0], e.tails[0], e.exact):
                        hits += 1
                        break
                else:
                    continue
                break
    for e in phrases:
        parts = [re.escape(b) + f"{_LETTERS}{{0,{t}}}" for b, t in zip(e.words, e.tails)]
        rx = re.compile(r"(?<![^\W_])" + r"\s+".join(parts) + r"(?![^\W_])", re.IGNORECASE)
        for m in rx.finditer(plain):
            if not e.capital or m.group(0)[:1].isupper():
                hits += 1
    return hits


# ------------------------------------------------------------------ обратно

def unmask(reply: str, masked: Masked) -> tuple[str, int]:
    """Развернуть ответ модели: `(текст, сколько токенов не развернулось)`."""
    unresolved = 0

    def put(m: re.Match) -> str:
        nonlocal unresolved
        gid = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        g = masked.groups.get(gid)
        if g is not None and g.canon and m.string[max(0, m.start() - 2):m.start()] == "[[":
            # цель ссылки — имя узла, а не падежная форма: «[[Беловой]]» битая (n793-out1 DS-5)
            return g.canon
        if g is not None:
            if m.group(4):
                i = int(m.group(4))
                if 1 <= i <= len(g.forms):
                    return g.forms[i - 1]
            elif g.canon:
                # токен группы без формы — имя узла, а не склонённая первая форма:
                # её примет «Исправления имён» как имя (n793-in2 S-C1)
                return g.canon
        unresolved += 1
        return UNRESOLVED

    text = _TOKEN.sub(put, reply)

    def stray(m: re.Match) -> str:
        nonlocal unresolved
        if m.group(0) == UNRESOLVED:
            return UNRESOLVED
        unresolved += 1
        return UNRESOLVED

    return _STRAY.sub(stray, text), unresolved


def passthrough(text: str) -> Masked:
    """Маска выключена владельцем (`sufler.cloud_mask: false`): текст как есть."""
    return Masked(text, "off")
