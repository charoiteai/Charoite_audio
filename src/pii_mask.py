"""Маска персональных данных перед облаком (№793): имена людей и систем → ID и обратно.

Модуль без ввода-вывода: формы приходят готовыми (их строит `cloud_door` из индекса
узлов графа), спаны NER — списком. Отсюда только строки и таблица ID.

Одно определение «вхождения формы» (`Entry` + `_base`, слово к форме — `_occurs`) и два обхода по нему:
маска идёт по словам текста и выбирает самые длинные совпадения, постусловие —
регуляркой-деревом по уже маскированному тексту. Это другой обход тех же форм, а не
другой алгоритм: имя, которого нет среди форм, постусловие не увидит (n793-out1).
Найдено хоть одно вхождение после маски — `MaskRefused`, в облако ничего не уходит.
Сравнение — без различия «ё» и «е» (`_yo`): распознавание пишет «Петр» вместо «Пётр».
Формы — на текст (`attest`, №828): имя-слово, которое в данных стоит с заглавной посреди
фразы, ловит и свои строчные падежи; их же берут постусловие и подделки (`Masked.forms`).

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
#: конец основы, который `_base` срезает; не путать с `_WORD_VOWEL_END` ниже (разные вопросы)
_VOWEL_END = "аяеиоуыюйь"
#: конец слова узла, у которого беглой гласной нет («Вера»); «ь» и «й» сюда не входят — у «Журавель»,
#: «Евгений» запасная основа есть, как на main (n828-opus C1, n828-out8 DS-M1)
_WORD_VOWEL_END = tuple("аеиоуыэюя")
#: окончания для проверки «общее ли слово» по общему словарю (`_distinctive`)
_INFLECT = ("а", "я", "ы", "и", "е", "у", "ю", "о", "ь", "й", "ом", "ем", "ой", "ей",
            "ов", "ев", "ам", "ям", "ами", "ями", "ах", "ях")
#: Хвост слова человека сверх основы узла, при котором слово — падеж узла, а не другое имя
#: той же основы: «Василию» — Василий, «Василиса», «Васильев» — нет (n793-final-opus C1).
#: Маскируется слово по-прежнему по основе; узел (канон) группе даёт только этот хвост.
_CASE_TAILS = frozenset(("", *_INFLECT, *_ENDINGS, "ым", "ыми", "ых", "ими", "их", "ия", "ию", "ием",
                         "ии", "ою", "ею", "ье", "ья", "ью", "ьем"))
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
    capital: bool = False       # первое слово с заглавной: имя-слово («Роза», «Ракурс»), не «роза»
    exact: bool = False         # аббревиатура: регистр основы точный («ЗЛК», не «злк»)
    source: tuple[str, ...] = ()  # слова формы как в узле: по ним `Forms.lexeme` судит, падеж ли это
    #: та же форма у других узлов («Парт» и «Парта»): общее слово снимается, только если оно не
    #: падеж ни одного из них (n828-in1 S-C1)
    alts: tuple[str, ...] = ()
    uncommon: bool = False      # строчная запись аббревиатуры («абв» при «АБВ»): только слово вне языка
    attested: bool = False      # в этом тексте имя-слово стоит с заглавной посреди фразы (`attest`)


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
    #: (слово текста, слово узла) → одна лексема; None — только `_CASE_TAILS` (тесты без pymorphy)
    lexeme: Callable[[str, str], bool] | None = dataclasses.field(default=None, compare=False)
    #: слово (нижний регистр) в общем словаре языка (Navec); None — не знаем (тесты без словаря)
    known: Callable[[str], bool] | None = dataclasses.field(default=None, compare=False)
    #: слово по грамматике — речь, а не имя (`pii_fake.speech_word`): сито спанов модели
    speech: Callable[[str], bool] | None = dataclasses.field(default=None, compare=False)
    #: метка диаризации («Собеседник 5»), не имя (`graph_names.split_label`; маска графа не
    #: импортирует — слой `cloud` ниже `graph`)
    label: Callable[[str], list[str] | None] | None = dataclasses.field(default=None, compare=False)
    #: полный словарь, от которого `like` отрезал этот: «слово — узел» (`_node_word`) судит по всем
    #: узлам, иначе срез `forms_touching` считает вхождения иначе, чем маска (n828-out8 H-I2)
    whole: "Forms | None" = dataclasses.field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        for e in self.entries:
            self.by_first.setdefault(e.words[0], []).append(e)
        self.max_tail = max((max(e.tails) for e in self.entries), default=0)

    def like(self, entries: Iterable[Entry]) -> "Forms":
        """Другие формы с теми же правилами вхождения — единственный способ собрать производный
        словарь: ручной `Forms(...)` терял поля политики, `replace` — индекс (n828-in1 DS-M2)."""
        return Forms(list(entries), lexeme=self.lexeme, known=self.known, speech=self.speech,
                     label=self.label, whole=self.whole or self)


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
    #: подставные имена запроса (`pii_fake.Dress`): метки в тексте заменены ими
    dress: object = None
    #: формы этого текста (после `attest`): их же берёт `dress` — одно определение вхождения
    #: на маску и подделки (n828-in1 DS-C2)
    forms: Forms | None = None

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


def _common_form(word: str, known: Callable[[str], bool]) -> bool:
    """Есть ли в общем словаре сама форма или голая основа. Строже `_distinctive`: его
    падежный перебор врёт для имён — «кузнец» + «ов» даёт фамилию из словаря, и
    «Кузнецом» уходил открытым (№799 26.10)."""
    return known(word.lower()) or known(_base(word)[0])


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


def common_key(line: str) -> str:
    """Строка файла владельца `_маска_общие.md` — ключ сравнения: слова через пробел, ё → е.
    Одна нормализация на `build_forms` и `abbrev_spans` (n828-out2 DS-M7)."""
    return _yo(" ".join(_words(line)))


def same_name(a: str, b: str) -> bool:
    """Одно ли это имя с точностью до падежа: «Ивану» и «Иван», «Анне Беловой»
    и «Анна Белова». Слово к слову по основе `_base`."""
    wa, wb = _words(a), _words(b)
    return bool(wa) and len(wa) == len(wb) and all(
        _base(x)[0].lower() == _base(y)[0].lower() for x, y in zip(wa, wb))


def name_part(part: str, known: Callable[[str], bool]) -> bool:
    """Часть имени системы в скобках или кавычках — имя, а не пояснение: с заглавной или
    с отличительным словом («(Контрактные задания)», «(ЗЛК)»; не «(данные)»)."""
    return part.strip()[:1].isupper() or any(_distinctive(w, known) for w in _words(part))


def build_forms(nodes: Iterable[NodeForms], *,
                stubs: Iterable[tuple[str, str | None, bool]] = (),
                owner: str = "", common: Iterable[str] = (),
                known: Callable[[str], bool] | None = None,
                lexeme: Callable[[str, str], bool] | None = None,
                speech: Callable[[str], bool] | None = None,
                label: Callable[[str], list[str] | None] | None = None) -> Forms:
    """Формы маски из узлов графа.

    `stubs` — заглушки слияния: (имя заглушки, канон живого узла или None — цель не
    найдена, человек ли — по папке заглушки). Имя заглушки — старое написание, оно
    живёт в стенограммах после слияния; без цели оно маскируется формой без узла (`Ч~`). `owner` — имя
    владельца из конфига: его может не быть среди узлов. `common` — имена и слова, которые
    владелец разрешил отправлять как есть (файл `_маска_общие.md`): это единственный
    путь узлу уйти открытым (состав 26.10). `known` — есть ли слово (в нижнем регистре)
    в общем словаре языка: от него зависит только заглавная у имени из общего слова
    и какие слова длинного имени системы маскируются ещё и поодиночке. `lexeme` — одна ли
    лексема у слова текста и слова узла (pymorphy, `pii_fake.same_lexeme`): канон узла получает
    только его падеж, «Петров» при узле «Пётр» — другое слово (n793-out10 C1). `speech` —
    слово по грамматике речь (`pii_fake.speech_word`), для сита спанов модели. `label` — разбор
    метки диаризации (`graph_names.split_label`): метка не форма у узла людей и не имя в спане
    модели; имя узла системы метку не разбирает — как на main (n828-out6 Haiku C1). Без `known`
    словаря нет: ни одно слово не подтверждено языком — маскируется больше, а правила, которые
    снимают маску по «слову языка» (М2, сито речи), молчат (n828-out2 DS-I2).
    """
    policy_known, known = known, known or (lambda _w: False)
    common_l = {common_key(c).lower() for c in common}
    nodes = list(nodes)
    if owner and not any(n.person and n.name == owner for n in nodes):
        nodes.append(NodeForms(owner))
    by_name = {n.name: n for n in nodes}
    # Чья основа: однофамильцы и тёзки дают форму без узла (правило `lexicon`:
    # основа у множества узлов — правило не создаётся, n793-in2 H-I4).
    owners: dict[tuple[str, str], set[str]] = {}
    raw: list[tuple[list[str], str, str | None, bool]] = []   # слова, вид, канон, заглавная

    def add(words: list[str], kind: str, canon: str | None, plain: bool = False) -> None:
        if not words:
            return
        raw.append((words, kind, canon, plain))
        if len(words) == 1:
            owners.setdefault((kind, _base(words[0])[0]), set()).add(canon or f"?{words[0]}")

    def add_name(name: str, kind: str, canon: str | None) -> None:
        # «КЗ (Контрактные задания)», «Релиз «Глубина»»: часть в скобках и кавычках у системы —
        # тоже её имя; пояснение строчными общими словами («(данные)», «(поле)») — нет, гейт
        # 26.10 дал на нём тысячи замен речи. У человека там чужие имена («Полина (упоминание
        # Светы)») — маскируются, но без узла: слить Свету с Полиной нельзя
        if kind == SYSTEM:
            # система — как на базе 63f98d13: метку не разбирать, «Спикер» и «Speaker» в Системах —
            # имя, а не метка (n828-out6 Haiku C1)
            inner = re.findall(r"\(([^()]*)\)|«([^«»]*)»", name)
            for a, b in inner:
                if name_part(a or b, known):
                    add_name(a or b, kind, canon)
            name = re.sub(r"\([^()]*\)", " ", name)
        else:
            # скобки обеих ширин, как `graph_names._LABEL_TAIL_RE`: ASCII-only оставлял «（Иван）» при
            # метке, и `label` возвращал ту же строку — рекурсия без дна (n828-out5 C1)
            paren = r"[(（]([^()（）]*)[)）]"
            inner = re.findall(paren + r"|«([^«»]*)»", name)
            while inner:
                for a, b in inner:
                    add_name(a or b, kind, None)
                name = re.sub(paren + r"|«[^«»]*»", " ", name)
                # вложенная скобка открывается, когда снята внутренняя: «Собеседник 4 (Аристарх
                # (Чебуреков))» терял «Аристарх» вместе с меткой (n828-out3 DS-I2)
                inner = [(a, "") for a in re.findall(paren, name)]
            # висячая скобка после снятия парных — разделитель, как запятая: «Собеседник 5 (Иван»
            # иначе не метка, и «Собеседник» шёл в имя
            rest = label(re.sub(r"[()（）]", ",", name)) if label is not None else None
            if rest is not None:
                # метки и пометки диаризации долой, остальное — имя: «Собеседник 5, Иван» → «Иван»
                # (n828-out2 I1). Часть всегда короче строки: скобок в ней нет, метка без скобки
                # отброшена
                for part in rest:
                    add_name(part, kind, canon)
                # метка диаризации («Собеседник 5» заглушки «Коллега (Собеседник 5)») — не имя: форма
                # «собеседник» маскировала каждую реплику (n828-in1 DS-C1); имя в скобках при метке
                # («Собеседник 2 (Саша)») уже добавлено выше
                return
        ws = _words(name)
        if kind == PERSON:
            # «Иванов (аналитик)»: в имени человека — только слова с заглавной
            ws = [w for w in ws if w[:1].isupper() and len(w) >= 2]
            for w in ws:
                add([w], kind, canon)
            if len(ws) > 1:
                add(ws, kind, canon)
            return
        # Система: имя и псевдоним — всегда целиком (состав 26.10: общие термины снимает только
        # файл исключений, эвристика «отличительной части» давала по утечке на круг: «Реактив»,
        # «Экстра офис»). Поодиночке — ещё отличительные слова длинного имени («ЗЛК» из «Шлюз ЗЛК»).
        # Имя только из общих слов («Проблема», «Старый офис») — с заглавной, как в имени:
        # строчное «проблема» — речь.
        plain = not any(_distinctive(w, known) or any(c.isdigit() for c in w) for w in ws)
        add(ws, kind, canon, plain=plain)
        if len(ws) > 1:
            for w in ws:
                if _distinctive(w, known):
                    add([w], kind, canon)

    for n in nodes:
        for name in (n.name, *n.aliases):
            add_name(name, PERSON if n.person else SYSTEM, n.name)
    for stub, target, person in stubs:
        node = by_name.get(target) if target else None
        # вид — по живой цели («Кузнец» в Командах → человек, №799 26.10), без цели — по
        # папке заглушки: заглушка системы без цели не человек
        if node is not None:
            person = node.person
        add_name(stub, PERSON if person else SYSTEM, node.name if node is not None else None)

    seen: set[tuple] = set()
    entries: list[Entry] = []
    for words, kind, canon, plain in raw:
        if _yo(" ".join(words).lower()) in common_l:
            continue
        if len(words) == 1:
            if len(words[0]) < 2 or not any(c.isalpha() for c in words[0]):
                continue
            base, tail, exact = _base(words[0])
            if kind == SYSTEM and not exact and len(base) < 4:
                tail = min(tail, 1)     # «Вор» не должно ловить «ворота», «Дела» — «делать»
            if len(owners.get((kind, base), ())) > 1:
                canon = None
            # заглавная нужна имени-слову («Роза» / «роза»); фамилию, которой нет в общем
            # словаре, распознавание пишет и строчными («квакин», №799 26.10)
            capital = plain and words[0][:1].isupper() \
                or kind == PERSON and not _distinctive(words[0], known)
            ent = Entry((base,), (tail,), kind, canon, capital=capital, exact=exact, source=(words[0],))
            if kind == PERSON and not exact and not _yo(words[0].lower()).endswith(_WORD_VOWEL_END):
                # беглая гласная — по слову узла, не по обрезанной основе: у слова на гласную её нет
                # («Вера» → «вр» ловило «Вроде»), у «Павел», «Журавель», «Евгений» — как на main (n828-opus C1)
                for alt in _fleeting(base):
                    # запасная основа чужого имени — не форма этого («Олег» → «ольг» у «Ольги», n793-out2 C2);
                    # своя («Льва» псевдонимом у Льва) — уже форма узла, без узла её не дублировать (n793-out3 DS-M2)
                    if owners.get((kind, alt)):
                        continue
                    # без узла: «Ольга» при одном «Олеге» маскируется, но не в его группу —
                    # иначе модель сливает двух людей, а ссылка разворачивается в чужое имя
                    fe = Entry((alt,), (tail,), kind, None, capital=capital)
                    if (fe.words, kind, None) not in seen:
                        seen.add((fe.words, kind, None))
                        entries.append(fe)
            if exact and base.isalpha() and len(base) >= 3:
                # распознавание пишет аббревиатуру словом: «Орисе», «Злком»
                title = Entry((base.capitalize(),), (tail,), kind, canon,
                              capital=kind == PERSON, exact=True, source=(words[0],))
                if (title.words, kind, canon) not in seen:
                    seen.add((title.words, kind, canon))
                    entries.append(title)
                # и строчными («абв» при «АБВ»): только слово, которого нет в языке (`_occurs`)
                # свой ключ: та же основа у обычной формы («Дапо») — её правило, не это (опыт n828:
                # слитая с ней строчная запись теряла «дап» узла «Дапо»)
                # exact — падеж по перечню хвостов: у словаря нет лексемы аббревиатуры, иначе
                # «Злком» уходил этой формой без канона человека (CI 4097f133)
                low = Entry((base.lower(),), (tail,), kind, canon, uncommon=True, exact=True,
                            source=(words[0],))
                if (low.words, kind, canon, "uncommon") not in seen:
                    seen.add((low.words, kind, canon, "uncommon"))
                    entries.append(low)
        else:
            bases = [_base(w) for w in words]
            ent = Entry(tuple(b.lower() for b, _t, _e in bases), tuple(t for _b, t, _e in bases),
                        kind, canon, capital=kind == PERSON or plain and words[0][:1].isupper(),
                        source=tuple(words))
        key = (ent.words, ent.kind, ent.group)
        if key not in seen:
            seen.add(key)
            entries.append(ent)
        else:
            # «Кластер» узлом и «кластер» псевдонимом: строчное имя тоже имя — заглавная не нужна,
            # если её не требует хоть один источник. «Парт» и «Парта»: слово второго узла — тоже
            # мерка лексемы (n828-in1 S-C1)
            more = tuple(w for w in ent.source[:1] if len(ent.words) == 1)
            entries[:] = [dataclasses.replace(
                              e, capital=e.capital and ent.capital,
                              alts=e.alts + tuple(w for w in more if _yo(w.lower()) not in
                                                {_yo(x.lower()) for x in (*e.source, *e.alts)}))
                          if (e.words, e.kind, e.group) == key and not e.uncommon else e
                          for e in entries]
    # одни слова у разных узлов, каким бы путём форма ни родилась (основа, ё/е, аббревиатура
    # словом), — форма без узла: иначе чужое имя уходит в группу и обратно — чужим каноном
    canons: dict[tuple, set] = {}
    for e in entries:
        canons.setdefault((e.words, e.kind), set()).add(e.group)
    return Forms([dataclasses.replace(e, group=None) if len(canons[(e.words, e.kind)]) > 1 else e
                  for e in entries], lexeme=lexeme, known=policy_known, speech=speech,
                  label=label)


def _fleeting(base: str) -> list[str]:
    """Основа без беглой гласной: «павел» → «павл» («Павла»), «лев» → «льв»,
    «заяц» → «зайц». Только у людей, с заглавной и без узла: «ольг» у «Олега» ловит и
    гостью Ольгу — маскировать её надо, сливать с Олегом нельзя (n793-out2 C2)."""
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


def _inflected(word: str, node_word: str, grown: bool = False) -> bool:
    """Слово и слово узла — одно с падежным хвостом («Спарка» при «Спарк», «цод» при «ЦОДы»):
    падеж, что бы ни думал о лексеме словарь. Опыт n828-in1: без обратного направления
    «ЦОДы → цод» снимался как чужое слово. `grown` — только слово длиннее узла."""
    w, n = _yo(word.lower()), _yo(node_word.lower())
    short, long_ = (n, w) if grown else sorted((w, n), key=len)
    return long_.startswith(short) and long_[len(short):] in _CASE_TAILS


def _other_word(word: str, entry: Entry, forms: Forms) -> bool:
    """Однословная форма системы совпала основой с общим словом другой лексемы: «стороны» при
    «Стори», «против» при «Прот». Единственное правило, которое снимает маску, поэтому три
    защиты (n828-in1 H-C1, DS-I1): имя узла не слово языка, слово — не узел с падежным хвостом,
    лексема не совпала ни с одним узлом формы. Опыт: 0 из 7142 падежей узлов систем."""
    if entry.kind != SYSTEM or entry.exact or len(entry.words) != 1 or not entry.source \
            or forms.known is None or forms.lexeme is None or not forms.known(word.lower()):
        return False
    nodes = (entry.source[0], *entry.alts)
    if any(forms.known(_yo(n.lower())) for n in nodes):
        # имя узла само слово языка («Хосты», «Согласий»): его падеж словарь может не узнать
        return False
    return not any(_inflected(word, n) or forms.lexeme(word, n) for n in nodes) \
        and not _node_word(word, forms)


def _node_word(word: str, forms: Forms) -> bool:
    """Слово — имя или падеж однословной формы графа (имя узла, псевдоним, заглушка, слово длинного
    имени): «postgres» при узлах «Postgres» и «Postgre» — не чужое общее слово, снимать маску нельзя
    (дифф-сверка форм с main, n828-opus). Судит по полному словарю, и на срезе тоже (n828-out8 H-I2).
    Зовёт только `_other_word`, где лексема уже проверена; проверка здесь — для прямых вызовов в тестах."""
    whole = forms.whole or forms
    low = _yo(word.lower())
    for cut in range(0, min(whole.max_tail, len(low) - 1) + 1):
        for e in whole.by_first.get(low[: len(low) - cut] if cut else low, ()):
            if len(e.words) == 1 and any(_yo(n.lower()) == low or forms.lexeme is not None and forms.lexeme(word, n)
                                         for n in (*e.source[:1], *e.alts)):
                return True
    return False


def _occurs(word: str, entry: Entry, forms: Forms) -> bool:
    """Слово текста — вхождение первого слова формы. Одно определение на маску, постусловие и
    подделки (n828-in1 S-I1, DS-C3): правило вхождения пишется только здесь."""
    if entry.uncommon:
        # «абв» при узле «АБВ» — если самого слова нет в языке; не `_common_form`: Navec знает
        # «дап», «пак», и «дапа» при узле «ДАП» уходил открытым (опыт n828-in1)
        # без словаря слово не подтверждено языком — маска (n828-out2 DS-I2: умолчание маскирует больше)
        return _word_fits(word.lower(), entry.words[0], entry.tails[0], False) \
            and not (forms.known is not None and forms.known(word.lower()))
    if not _word_fits(word, entry.words[0], entry.tails[0], entry.exact):
        return False
    if entry.capital and not word[:1].isupper():
        # строчная запись имени-слова — только если в этом тексте оно же стоит именем (`attest`),
        # и это падеж того же слова: «веру» при «… Веру …», но не «верой» при «Верочке»
        nodes = (*entry.source[:1], *entry.alts)
        return entry.attested and (
            # у узла из слова языка «узел + хвост» — только наращением: «маяка», «графы» при
            # «Маяк», «Граф» (лексема без множественного их не знает, n828-out4 DS-C1), но не
            # «пол» при «Поле» (n828-out3 S-I1, H-I1)
            entry.kind == SYSTEM and any(
                _inflected(word, n, grown=forms.known is not None and forms.known(_yo(n.lower())))
                for n in nodes)
            or forms.lexeme is not None and any(forms.lexeme(word, n) for n in nodes))
    return not _other_word(word, entry, forms)


def _case_of(surface: str, entry: Entry, lexeme: Callable[[str, str], bool] | None) -> bool:
    """Слова спана — падежные формы слов узла, а не другое имя той же основы. Решает
    лексема: хвост «ов», «ия» — и падеж, и другое имя («Петров» при «Пётр», n793-out10 C1),
    а перечень хвостов не знает «Вельских» и «Толстых». Без лексемы (тесты без pymorphy) —
    перечень `_CASE_TAILS`."""
    raw = _WORD.findall(_yo(surface))
    if len(raw) != len(entry.words):
        return False
    # аббревиатура словом («Злком» при «ЗЛК») — у словаря нет её лексемы: падеж по перечню
    # хвостов, иначе человек-аббревиатура терял канон (n793-out12 DS-M5)
    if lexeme is not None and not entry.exact and len(entry.source) == len(raw):
        return all(lexeme(w, src) for w, src in zip(raw, entry.source))
    return all(w.lower().startswith(b.lower()) and w.lower()[len(b):] in _CASE_TAILS
               for w, b in zip(raw, entry.words))


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
            if not _occurs(word, e, forms):
                continue
            j, stop = i, end
            ok = True
            for base, tail in zip(e.words[1:], e.tails[1:]):
                j += 1
                if j >= len(words) or not _joint(text[stop:words[j][0]]) \
                        or not _word_fits(words[j][2], base, tail, False):
                    ok = False
                    break
                stop = words[j][1]
            if ok:
                found.append((start, stop, e))
    return found


#: Стык слов многословной формы: пробел, дефис, точка, косая, кавычка, скобка, запятая,
#: подчёркивание — до трёх знаков («Аллюр-отчёт», «35-я», «Орбита 12.4-7», «enquiry_cd»), либо
#: перевод строки в пробелах («Старый\nофис» — разрыв строки в данных, n793-out5 DS-C2), но не
#: перевод строки со знаком: «Релиз\n[10:41]» — таймкод следующей реплики (n793-out6 H-I1).
#: `\r\n`, одна пустая строка и цитата «\n> » — тоже разрыв в данных (n793-out7 H-C1, S-I2).
#: Пустого стыка нет: «Портал20» — одно слово. Одно правило на маску и постусловие (n793-out3
#: S-I3); лишняя склейка через точку («Орбита. 12») — лишняя замена, не утечка.
_JOINT = r"(?:(?:[^\w\n]|_){1,3}|[ \t\r]*\n(?:[ \t\r>]*\n)?[ \t\r>]*)"


def _joint(sep: str) -> bool:
    return re.fullmatch(_JOINT, sep) is not None


def _merge(spans: list[tuple[int, int, Entry]]) -> list[tuple[int, int, Entry]]:
    """Пересекающиеся формы словаря — один спан от начала первой до конца последней, с
    формой самой длинной: «Таблицы SF» и «SF (Payment Type)» двух узлов, выбор одной
    оставлял бы хвост другой открытым (гейт по графу 26.10)."""
    out: list[list] = []
    for s, e, ent in sorted(spans, key=lambda x: (x[0], -x[1])):
        if out and s < out[-1][1]:
            last = out[-1]
            if e - s > last[3]:
                last[2], last[3] = ent, e - s
            last[1] = max(last[1], e)
        else:
            out.append([s, e, ent, e - s])
    return [(s, e, ent) for s, e, ent, _n in out]


def _pick(spans: list[tuple[int, int, object]]) -> list[tuple[int, int, object]]:
    """Самые длинные непересекающиеся: длиннее — раньше, при равной длине — раньше по тексту."""
    taken: list[tuple[int, int, object]] = []
    for s in sorted(spans, key=lambda x: (-(x[1] - x[0]), x[0])):
        if all(s[1] <= t[0] or s[0] >= t[1] for t in taken):
            taken.append(s)
    return sorted(taken, key=lambda x: x[0])


def _ner_noise(text: str, start: int, stop: int, kind: str, forms: Forms | None = None) -> bool:
    """Спан модели имён, который на деле — речь: «Ну», «Вот», «Коллеги» в начале
    реплики (то же слово строчными есть в этом же тексте) и запинки «А-а», «Не-не».
    Замер 26.10: Slovnet на стенограммах отдаёт такие спаны десятками. Известных
    людей и системы держит словарь графа, это сито касается только модели.
    Однословный спан человека — шум, если стоит в начале реплики или фразы и то же
    слово строчными есть в тексте. Правило — только для людей: системы в речи пишутся
    строчными сплошь («аисе»), и замер терял их (60 → 59 из 68).
    Метка реплики «Собеседник 3» — шум везде. Однословный спан человека из общего словаря, который
    по грамматике речь («Вроде», «Угу», «Попроси»), — шум в любой позиции: 24 из 154 групп модели
    на 8 встречах 28.10; слово вне словаря не трогаем — это может быть фамилия гостя (n828-in1 S-I4)."""
    words = _WORD.findall(text[start:stop])
    if not words or _never_a_name(text, start, stop, kind, forms):
        return True
    if len({w.lower() for w in words}) == 1 and len(words) > 1:
        return True
    if kind == PERSON and len(words) == 1 and words[0][:1].isupper():
        # только в начале реплики или фразы: гость «Вера» посреди фразы — имя,
        # даже если «вера» строчными есть в тексте (n793-out1 S-I5)
        if not _phrase_start(text, start):
            return False
        low = words[0].lower()
        # «Хмм,», «Пфф,» — междометие: в имени всегда есть гласная; с подделками такое
        # «обращение» становилось выдуманным собеседником (n793-out3 H-I1)
        if not re.search(r"[аеёиоуыэюяaeiouy]", low):
            return True
        # обращение («Вера, глянь») — имя гостя, даже если «вера» есть строчными (n793-out2 C3)
        if text[stop:stop + 1] == ",":
            return False
        return re.search(rf"(?<![^\W_]){re.escape(low)}(?![^\W_])", text) is not None
    return False


def _is_label(s: str, label: Callable[[str], list[str] | None] | None) -> bool:
    """Спан целиком — метка диаризации: разбор метки ничего, кроме метки, не оставил
    («Собеседник 1, муж», «Собеседник 3 (модератор)»). Имя рядом — нет: «Собеседник 5, Иван»,
    «Собеседник 5 (Иван)» (n828-out1 DS-M7, out2 DS-M4, out3 DS-I1; правило — `graph_names`)."""
    return label is not None and label(s) == []


def _never_a_name(text: str, start: int, stop: int, kind: str, forms: Forms | None) -> bool:
    """Кусок спана модели — не имя в любой позиции: метка реплики или слово языка, которое по
    грамматике речь. Годится и для остатка спана после словаря («Попроси Лену» минус «Лену»);
    правило начала фразы — нет: «Вера» из «Вера Петрова» — имя."""
    if forms is not None and _is_label(text[start:stop], forms.label):
        return True
    words = _WORD.findall(text[start:stop])
    return kind == PERSON and len(words) == 1 and forms is not None and forms.known is not None \
        and forms.speech is not None and forms.known(words[0].lower()) and forms.speech(words[0])


def drop_noise(text: str, ner: Sequence[tuple[int, int, str]],
               forms: Forms | None = None) -> list[tuple[int, int, str]]:
    """Спаны модели без шума — до всех, кто их читает: `rare_word_spans` брал шумный спан
    уликой «имя рядом» (n828-in1 DS-I5)."""
    return [x for x in ner if not _ner_noise(text, *x, forms)]


def _phrase_start(text: str, start: int) -> bool:
    """Слово стоит в начале реплики или фразы: распознавание ставит там заглавную любому слову."""
    before = text[text.rfind("\n", 0, start) + 1:start]
    return not before.strip() or re.search(r"[.!?:…]\s*$", before) is not None


#: Разметка в начале строки: таймкод, маркер и номер списка, флажок задачи, цитата, жирный, ссылка.
_MARKUP = re.compile(r"(?:\s|[-–—*>#•]|\[\[|\[[ xX]?\]|\d{1,3}[.)]|\[\d{1,2}:\d{2}(?::\d{2})?\])*")


def _speech_start(text: str, start: int) -> bool:
    """`_phrase_start` для `attest`: разметка строки («[10:41] », «- », «**») — тоже начало
    реплики (n828-in1 S-I2). Сите модели не годится: метка «**Вера**» там стала бы шумом."""
    before = text[text.rfind("\n", 0, start) + 1:start]
    return _MARKUP.fullmatch(before) is not None or _phrase_start(text, start)


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
        if _common_form(w, known):
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


def abbrev_spans(text: str, known: Callable[[str], bool],
                 common: Iterable[str] = ()) -> list[tuple[int, int, str]]:
    """Аббревиатура кириллицей 3–6 букв, которой нет в общем словаре («ЖЗИК»), — система без
    узла: Slovnet такие не видит (замер 29.10: 19 на 8 встречах, все доменные; «ИНН», «СМС», «ЦОД»
    — в словаре). Свои общие владелец снимает файлом `_маска_общие.md` (`common`)."""
    skip = {common_key(c).upper() for c in common}
    return [(m.start(), m.end(), SYSTEM) for m in _WORD.finditer(text)
            if 3 <= len(m.group(0)) <= 6 and _is_abbrev(m.group(0))
            and all("А" <= c <= "Я" or c == "Ё" for c in m.group(0))
            and not known(m.group(0).lower()) and _yo(m.group(0)) not in skip]


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


def _clip(spans: Sequence[tuple], regions: Sequence[tuple[int, int]] | None) -> list[tuple]:
    """Спаны внутри областей данных: словарный — только целиком, спан модели — обрезанный."""
    if regions is None:
        return list(spans)
    out = []
    for s, e, *rest in spans:
        for a, b in regions:
            if a <= s and e <= b:
                out.append((s, e, *rest))
                break
            if not isinstance(rest[0], Entry) and s < b and a < e:
                out.append((max(s, a), min(e, b), *rest))
    return out


def _blank_outside(text: str, regions: Sequence[tuple[int, int]] | None) -> str:
    """Текст для постусловия: вне областей данных — пробелы (переводы строк целы)."""
    if regions is None:
        return text
    keep = [False] * len(text)
    for a, b in regions:
        keep[a:b] = [True] * (b - a)
    return "".join(c if k or c == "\n" else " " for c, k in zip(text, keep))


def attest(text: str, forms: Forms, regions: Sequence[tuple[int, int]] | None = None) -> Forms:
    """Формы этого текста: имя-слово, которое в данных стоит с заглавной не в начале реплики,
    ловит и свои строчные падежи («веру» при «… Веру …»): распознавание пишет имена и
    строчными (замер 29.10: «вера» ×3 открытым при ×3 заглавных). Только добавляет вхождения."""
    keys = {(e.words, e.kind, e.group) for s, _e, e in _clip(_dictionary_spans(text, forms), regions)
            if e.capital and len(e.words) == 1 and not _speech_start(text, s)}
    if not keys:
        return forms
    return forms.like(dataclasses.replace(e, attested=True) if (e.words, e.kind, e.group) in keys else e
                      for e in forms.entries)


def mask(text: str, forms: Forms, ner: Sequence[tuple[int, int, str]] = (), *,
         level: str = "basic", regions: Sequence[tuple[int, int]] | None = None) -> Masked:
    """Маскировать текст. `ner` — спаны (start, end, PERSON|SYSTEM) по `prepare(text)`.

    `regions` — области данных (стенограмма, узлы) в `prepare(text)`: вне них наша
    инструкция, её слова («промпт», «суфлёр») совпадают с узлами систем, а ПДн в ней
    нет (замер №799 26.10: «задачи ставит только этот ⟦С~2.1⟧»). None — весь текст.
    Бросает `MaskRefused`, если после маски словарь всё ещё находит форму в данных."""
    text = prepare(text)
    forms = attest(text, forms, regions)
    dict_spans = _merge(_clip(_dictionary_spans(text, forms), regions))
    # остаток спана после словаря — тоже бывает меткой или речью: «Собеседник 5\n\n**Коль**» минус
    # «Коль», «Попроси Лену» минус «Лену» (замер 29.10)
    pieces = [x for x in _ner_pieces(text, _clip(drop_noise(text, ner, forms), regions), dict_spans)
              if not _never_a_name(text, *x, forms)]
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
            if canon and kind == PERSON and not _case_of(surface, what, forms.lexeme):
                canon = None    # другое имя той же основы — своя группа, не узел
                gkey = (kind, "~", " ".join(_base(w)[0] for w in _WORD.findall(surface)))
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
    masked = Masked("".join(out), level, groups, len(spans), forms=forms)
    # постусловие — только по данным: те же спаны поверх текста с пустой инструкцией
    check, pos = [], 0
    blank = _blank_outside(text, regions)
    for s, e, _what in spans:
        check.append(blank[pos:s])
        check.append(" ")
        pos = e
    check.append(blank[pos:])
    left = postcondition_hits("".join(check), forms)
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


def forms_touching(forms: Forms, words: Iterable[str]) -> Forms:
    """Формы, хоть одно слово которых может совпасть с одним из `words`, — с запасом:
    основа — префикс слова без регистра и потолка хвоста. Новая форма в тексте после
    подстановки обязана задеть подставленное слово, остальные формы не меняются. Срез перечисляет
    только свои формы, а «слово — узел» судит по полному словарю (`Forms.whole`, n828-out9 DS-M1)."""
    prefixes = {w[:k] for w in (_yo(_nfc(x)).casefold() for x in words) for k in range(1, len(w) + 1)}
    return forms.like(e for e in forms.entries if any(_yo(b).casefold() in prefixes for b in e.words))


def postcondition_hits(masked_text: str, forms: Forms) -> int:
    """Сколько форм словаря видно в маскированном тексте — другим алгоритмом, чем маска.

    Однословные формы ищутся деревом основ, многословные — фразой; правила
    заглавной, точного регистра и хвоста — те же поля `Entry`. На срезе `forms_touching` перечисляются
    формы среза, а соседние узлы берутся из `forms.whole`."""
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
                    if _occurs(word, e, forms):
                        hits += 1
                        break
                else:
                    continue
                break
    for e in phrases:
        rx = r"(?<![^\W_])" + re.escape(e.words[0]) + f"{_LETTERS}{{0,{e.tails[0]}}}"
        for b, t in zip(e.words[1:], e.tails[1:]):
            rx += _JOINT + re.escape(b) + f"{_LETTERS}{{0,{t}}}"
        rx = re.compile(rx + r"(?![^\W_])", re.IGNORECASE)
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
        s, e = m.start(), m.end()
        if g is not None and g.canon and m.string[max(0, s - 2):s] == "[[" \
                and (m.string.startswith("]]", e) or m.string[e:e + 1] in ("|", "#")):
            # цель ссылки целиком — имя узла, а не падежная форма: «[[Беловой]]» битая
            # (n793-out1 DS-5); «[[Белов 1-1]]» — заметка, а не узел, токен — форма (n793-out2 C1)
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
