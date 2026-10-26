"""Подставные имена вместо меток маски (№793, решение 26.10 по замеру №799, плечо C).

Маска (`pii_mask.mask`) находит имена и собирает их в группы — одна группа на узел графа
или на имя без узла. Здесь метки `⟦Ч1.2⟧` превращаются в правдоподобные подделки: человек —
имя и фамилия того же рода и падежа (pymorphy3), система — подставное слово или сокращение.
На 5 встречах №799 подделки дали лишних ⛔ 4 против 11 у меток: метку «Дим» и «Диму» модель
читала как двух людей.

Одна подделка на группу: все формы и псевдонимы узла сходятся в одно подставное имя, падеж
формы переносится. Подделки выбираются заново на каждый запрос (облако не связывает узел
между запросами) и не совпадают ни с формой словаря графа, ни со словом отправляемого текста.

Обратно (`undress`) — по лемме подделки: слово ответа → исходное слово в падеже ответа;
не склоняется — исходное имя как есть. Новые имена модели не трогаются, только считаются.
"""
from __future__ import annotations

import collections
import dataclasses
import random
import re
import secrets
import threading
from typing import Sequence

import pii_mask

INSTALL_RECIPE = "поставьте пакет pymorphy3 в окружение Чароита (pip install pymorphy3==2.0.6)"

#: Слово — без дефиса: подделка внутри составного («Аметист-луп») разворачивается тоже.
_WORD = re.compile(r"[^\W\d_]+|\d+")
_ABBR = re.compile(r"^([A-ZА-ЯЁ]{2,6})([а-яё]{0,3})$")

#: Пулы подделок: редкие русские имена и фамилии, которых нет у живых коллег по
#: замеру №799, и предметные слова для систем. Совпадение с графом и текстом всё
#: равно проверяет код (`_free`).
NAMES = {
    "masc": ("Глеб Тимофей Ярослав Святослав Елисей Платон Всеволод Мирон Демид Савелий Прохор Ростислав "
             "Арсений Евсей Остап Нестор Лукьян Ермолай Захар Порфирий Агафон Аверкий Адриан Аким Амвросий "
             "Анисим Антип Аристарх Варлаам Венедикт Викентий Гавриил Гордей Давыд Дементий Демьян Дорофей "
             "Евграф Елизар Емельян Еремей Ерофей Ефрем Зиновий Иларион Иннокентий Ипполит Исидор Капитон "
             "Корней Кузьма Лаврентий Лазарь Леонтий Мартын Мстислав Наум Никанор Никифор Никодим Онисим "
             "Панкрат Пантелей Парамон Потап Савва Самсон Сильвестр Спиридон Терентий Трифон Трофим "
             "Харитон Христофор Яким").split(),
    "femn": ("Злата Милана Ярослава Василиса Есения Агата Мирослава Таисия Ульяна Стефания Любава Варвара "
             "Евдокия Пелагея Аглая Марфа Серафима Леся Ангелина Олеся Фаина Дарина Глафира Агафья Агния "
             "Акулина Аксинья Алевтина Ариадна Аврора Ефросинья Зинаида Капитолина Клавдия Лукерья Мелания "
             "Мирра Олимпиада Павлина Прасковья Руфина Устинья Феодора Эмилия Ядвига").split(),
}
SURNAMES = ("Ладыгин Зимогоров Брусницын Щеглов Тихомиров Ракитин Овсянников Колокольцев Веретенников "
            "Самоцветов Лыков Пестряков Чернолесов Голубцов Кудеяров Мухортов Порошин Сизов Дятлов "
            "Кречетов Голованов Шелепин Воротынцев Зябликов Тетерин Кулибин Сухомлинов Ботвинин Чеглоков "
            "Пятибратов Скоробогатов Толоконников Рукавишников Бородулин Кочергин Сыромятников Гремячкин "
            "Ступишин Вершинин Лапшин Деревянкин Полозов Курбатов Мещеряков Бахрушин Ратманов").split()
#: Отчество строится от мужского имени пула: «Глеб» → «Глебович».
SYSTEM_WORDS = ("Альбатрос Бриг Гиацинт Палантин Изумруд Тамарин Кондор Мангуст Пеликан Агат Аметист "
                "Малахит Нефрит Опал Топаз Янтарь Гранат Берилл Циркон Корунд Родонит Кварц Турмалин "
                "Обсидиан Хризолит Лазурит Флюорит Сапсан Беркут Ястреб Буревестник Альмандин Кальцит "
                "Пирит Галенит Секстант Астролябия Барометр Пеленгатор Компас Флюгер Маяк").split()
#: попыток подобрать группе подделку, не совпавшую с текстом и графом
_TRIES = 20
_TRIES_WHOLE = 5  # пересборка всех подделок, если итоговая строка сложила форму графа
_CASES = ("nomn", "gent", "datv", "accs", "ablt", "loct", "voct", "gen2", "acc2", "loc2")
_CASE_BASE = {"gen2": "gent", "acc2": "accs", "loc2": "loct", "voct": "nomn"}
#: Окончания подставной фамилии на -ов/-ин: pymorphy знает не каждую.
_SURN_END = {("masc", "nomn"): "", ("masc", "gent"): "а", ("masc", "datv"): "у", ("masc", "accs"): "а",
             ("masc", "ablt"): "ым", ("masc", "loct"): "е", ("femn", "nomn"): "а", ("femn", "gent"): "ой",
             ("femn", "datv"): "ой", ("femn", "accs"): "у", ("femn", "ablt"): "ой", ("femn", "loct"): "ой",
             ("plur", "nomn"): "ы", ("plur", "gent"): "ых", ("plur", "datv"): "ым", ("plur", "accs"): "ых",
             ("plur", "ablt"): "ыми", ("plur", "loct"): "ых"}

_lock = threading.Lock()
_morph = None
_pools: dict[str, list[str]] = {}


def _load():
    global _morph
    with _lock:
        if _morph is None:
            try:
                import pymorphy3
                m = pymorphy3.MorphAnalyzer()
            except Exception as e:  # noqa: BLE001 — любой сбой = подделок нет, отправки нет
                raise pii_mask.MaskRefused(f"нет морфологии для подставных имён ({type(e).__name__}): "
                                           f"{INSTALL_RECIPE}") from None
            # в пул — только то, что словарь склоняет: «Есения» осталась бы в именительном
            for g in ("masc", "femn"):
                _pools[g] = [x for x in NAMES[g] if _declines(m, x, "Name", g)]
            # второй ярус — когда первый занят: на встрече до 90 людей (замер №793, 18 встреч №799)
            import pii_fake_pool
            _pools["masc2"], _pools["femn2"] = list(pii_fake_pool.MASC), list(pii_fake_pool.FEMN)
            _pools["surn2"] = list(pii_fake_pool.SURNAMES)
            _pools["system"] = [x for x in SYSTEM_WORDS if _declines(m, x, None, None)]
            _morph = m
        return _morph


def _declines(m, word: str, want: str | None, gender: str | None) -> bool:
    return all(_e(_inflect(m, word, c, "sing", gender, want)) != _e(word) for c in ("datv", "ablt"))


def _e(word: str) -> str:
    return word.lower().replace("ё", "е")


def _keep_case(src: str, word: str) -> str:
    if src.isupper() and len(src) > 1:
        return word.upper()
    return (word[:1].upper() if src[:1].isupper() else word[:1].lower()) + word[1:]


def _case(p) -> tuple[str, str]:
    case = next((c for c in _CASES if c in p.tag), "nomn")
    return _CASE_BASE.get(case, case), ("plur" if "plur" in p.tag else "sing")


def _role(m, word: str):
    """Роль слова имени: имя в ед. ч. → фамилия → отчество → имя во мн. ч. («Власов» —
    фамилия, а не род. мн. «Влас»). None — не имя по словарю."""
    # склоняемый разбор раньше несклоняемого: «Анне» — дательный «Анны», а не имя «Анне»
    ps = sorted(m.parse(word), key=lambda p: "Fixd" in p.tag)
    for tag, cond in (("Name", "sing"), ("Surn", None), ("Patr", None), ("Name", None)):
        for p in ps:
            if tag in p.tag and (cond is None or cond in p.tag):
                return tag, p
    return None, ps[0]


def _inflect(m, lemma: str, case: str, num: str, gender: str | None, want: str | None) -> str:
    ps = m.parse(lemma)
    exact = [p for p in ps if _e(p.normal_form) == _e(lemma)]
    ps = ([p for p in exact if want and want in p.tag and (gender is None or gender in p.tag)]
          or [p for p in exact if want is None or want in p.tag] or exact or ps)
    p = ps[0]
    r = p.inflect({case, num} if num == "plur" else {case}) or p.inflect({case}) or p
    return r.word[:1].upper() + r.word[1:]


def _surname(base: str, case: str, num: str, gender: str) -> str:
    return base + _SURN_END[("plur" if num == "plur" else gender, case)]


def _patronymic(name: str, gender: str) -> str:
    stem = name[:-1] + "ев" if name.endswith("й") else name + "ов"
    return stem + ("ич" if gender == "masc" else "на")


@dataclasses.dataclass
class Dress:
    """Подделки одного запроса: группа → пары (исходная форма, подделка). Живёт в памяти
    рядом с `Masked`, как таблица ID."""
    pairs: dict[str, list[tuple[str, str]]] = dataclasses.field(default_factory=dict)
    canon: dict[str, str | None] = dataclasses.field(default_factory=dict)
    #: род человека группы: падеж подделки и ответа — только по разборам этого рода
    gender: dict[str, str] = dataclasses.field(default_factory=dict)
    #: счёт разворота последнего ответа — в журнал
    back: collections.Counter = dataclasses.field(default_factory=collections.Counter)

    def back_summary(self) -> str:
        b = self.back
        return (f"разворот: точная форма {b['exact']}, тот же падеж {b['case']}, "
                f"канон без падежа {b['canon']}, подделка осталась {b['left']}, "
                f"новых имён модели {b['new']}")


class _Picker:
    """Выбор подделок запроса: не слово текста и не форма графа, без повторов."""

    def __init__(self, m, text: str, forms: pii_mask.Forms, rnd: random.Random) -> None:
        self.m, self.forms, self.rnd = m, forms, rnd
        self.banned: set[str] = set()
        for w in set(_WORD.findall(text)):
            self.banned.add(_e(w))
            self.banned.add(_e(m.parse(w)[0].normal_form))
        #: слова текста — без выбранных подделок: по ним `clashes` судит готовые формы
        self.text = frozenset(self.banned)

        self._lexemes: dict[str, set[str]] = {}

    def taken(self, word: str) -> bool:
        lexeme = self._lexemes.get(word)
        if lexeme is None:
            # все формы слова: «Емельян» во мн. ч. — «Емельяновы», а это уже фамилия
            p = self.m.parse(word)[0]
            lexeme = {_e(f.word) for f in p.lexeme} | {_e(word), _e(p.normal_form)}
            if pii_mask._dictionary_spans(" ".join(f.word for f in p.lexeme) + " " + word, self.forms):
                lexeme = lexeme | {"\0граф"}       # форма словаря графа — занято навсегда
            self._lexemes[word] = lexeme
        return "\0граф" in lexeme or bool(lexeme & self.banned)

    def free(self, *pools: Sequence[str]) -> str | None:
        """Случайное свободное слово первого пула, где оно есть; ярусы по порядку.
        Проверка `taken` дорогая (лексема и словарь графа) — до первого свободного."""
        for pool in pools:
            order = list(pool)
            self.rnd.shuffle(order)
            x = next((x for x in order if not self.taken(x)), None)
            if x is not None:
                # все формы выбранной: «Агат» (система) занимает и «Агата» (n793-out4 S-I2)
                self.banned |= self._lexemes[x] | {_e(x)}
                return x
        return None

    def clashes(self, fakes: list[str]) -> bool:
        """Готовые формы подделки — слово текста или форма графа? Проверка того, что уходит,
        а не слова пула: отчество «Тимофеевич» строится из свободного «Тимофей» и может
        совпасть с настоящим (n793-out5 DS-C3)."""
        return any(len(w) > 1 and _e(w) in self.text for f in fakes for w in _WORD.findall(f)) \
            or any(pii_mask._dictionary_spans(f, self.forms) for f in fakes)

    def abbr(self) -> str:
        # как настоящие: «ЗЛК», «ОРИС» — три-четыре буквы, гласная не больше одной
        while True:
            x = "".join(self.rnd.choice("БВГДКЛМНПРСТФХЦШ") for _ in range(self.rnd.choice((3, 4))))
            if self.rnd.random() < 0.5:
                i = self.rnd.randrange(len(x) + 1)
                x = x[:i] + self.rnd.choice("АОУИЕ") + x[i:]
            # не слово языка: «ПЛАН» в начале фразы ответа развернулся бы в систему (n793-out5 H-C2)
            if not self.taken(x) and not self.m.word_is_known(x.lower()):
                self.banned.add(_e(x))
                return x


def _gender(m, forms: list[str]) -> str:
    for f in forms:
        for w in _WORD.findall(f):
            r, p = _role(m, w)
            if r in ("Name", "Surn") and ("masc" in p.tag or "femn" in p.tag):
                return "femn" if "femn" in p.tag else "masc"
    return "masc"


def _words(m, f: str, gender: str):
    """Слова формы человека: (совпадение, роль, разбор в роде группы); инициал — тоже слово
    своей роли (в фамилии с инициалами первая буква — имя)."""
    for i, mt in enumerate(_WORD.finditer(f)):
        w = mt.group(0)
        r, p = _role(m, w)
        r = r or ("Name" if i == 0 else "Surn")
        p = next((q for q in sorted(m.parse(w), key=lambda q: "Fixd" in q.tag)
                  if r in q.tag and gender in q.tag), p)
        yield mt, r, p


def _person(pk: _Picker, forms: list[str]) -> list[str]:
    m = pk.m
    gender = _gender(m, forms)
    # ключ — роль и её номер: «Иванов-Петров» — две фамилии, иначе разворот «Иванов-Иванов»
    # (n793-out5 DS-C4). Номер слова, которое в группе встречается рядом с другим той же
    # роли, — по его лемме: одиночное «Петров» — вторая фамилия «Иванов-Петров», а не первая
    # (n793-out6 H-C1). Прочие — по порядку в форме: «Дима» и «Дмитрий» — одна подделка.
    order: dict[str, list[str]] = collections.defaultdict(list)
    for f in forms:
        ws = list(_words(m, f, gender))
        ws = [x for x in ws if len(x[0].group(0)) > 1]
        for _mt, r, p in ws:
            if sum(1 for x in ws if x[1] == r) > 1 and _e(p.normal_form) not in order[r]:
                order[r].append(_e(p.normal_form))
    parts: dict[tuple[str, int], str | None] = {}
    out = []
    for f in forms:
        res, pos = [], 0
        nth: collections.Counter = collections.Counter()
        for mt, r, p in _words(m, f, gender):
            w = mt.group(0)
            lem_e = _e(p.normal_form)
            k = (r, order[r].index(lem_e) if lem_e in order[r] else nth[r])
            nth[r] += 1
            if k not in parts:
                if r == "Patr":
                    name = pk.free(_pools["masc"], _pools["masc2"])
                    parts[k] = _patronymic(name, gender) if name else None
                else:
                    parts[k] = (pk.free(_pools[gender], _pools[gender + "2"]) if r == "Name"
                                else pk.free(SURNAMES, _pools["surn2"]))
                if parts[k] is None:
                    raise pii_mask.MaskRefused("пул подставных имён исчерпан — отправка отменена")
            case, num = _case(p)
            lem = p.normal_form
            if len(w) == 1:
                # инициал — первая буква подделки той же роли: настоящая буква не уходит
                # (n793-out6 S-I3 = H-I2); обратно — только целой формой (`undress`)
                fw = parts[k][:1]
            elif r == "Name" and lem[-1:] in "ая" and _e(w) in (_e(lem[:-1]), _e(lem[:-1]) + "ь"):
                # звательная усечённая («Саш», «Дим») — так же усечь подделку на -а/-я
                fw = parts[k][:-1] if parts[k][-1:] in "ая" else parts[k]
            elif r == "Surn":
                fw = _surname(parts[k], case, num, gender)
            elif r == "Patr":
                fw = _inflect(m, parts[k], case, num, gender, "Patr")
            else:
                fw = _inflect(m, parts[k], case, num, gender, r)
            res.append(f[pos:mt.start()])
            res.append(_keep_case(w, fw))
            pos = mt.end()
        res.append(f[pos:])
        out.append("".join(res))
    return out


def _system(pk: _Picker, forms: list[str]) -> list[str]:
    m = pk.m
    if any(re.search(r"[а-яё]{3}", f) for f in forms):
        base = pk.free(_pools["system"])
        if base is not None:
            out = []
            for f in forms:
                ws = [w for w in _WORD.findall(f) if re.search(r"[А-Яа-яЁё]", w)]
                case, num = _case(m.parse(ws[-1])[0]) if ws else ("nomn", "sing")
                out.append(_keep_case(f, _inflect(m, base, case, num, None, None)))
            return out
    a = pk.abbr()
    out = []
    for f in forms:
        t = _ABBR.match(f)
        out.append(a + (t.group(2) if t else ""))
    return out


def _dress_once(m, masked: pii_mask.Masked, pk: _Picker) -> tuple[Dress, str]:
    d = Dress()
    fakes: dict[str, list[str]] = {}
    for gid, g in masked.groups.items():
        make = _person if gid.startswith(pii_mask.PERSON) else _system
        for _try in range(_TRIES):
            # выбранное уже занято в `pk` — следующая попытка берёт другие слова
            fakes[gid] = make(pk, g.forms)
            if not pk.clashes(fakes[gid]):
                break
        else:
            raise pii_mask.MaskRefused("подделка совпала со словом текста или формой графа — "
                                       "отправка отменена")
        d.pairs[gid] = list(zip(g.forms, fakes[gid]))
        d.canon[gid] = g.canon
        if gid.startswith(pii_mask.PERSON):
            d.gender[gid] = _gender(m, g.forms)

    def put(mt: re.Match) -> str:
        gid = f"{mt.group(1)}{mt.group(2)}{mt.group(3)}"
        return fakes[gid][int(mt.group(4) or 1) - 1]

    text = pii_mask._TOKEN.sub(put, masked.text)
    if pii_mask._STRAY.search(text):
        raise pii_mask.MaskRefused("после подстановки осталась метка — отправка отменена")
    return d, text


def dress(masked: pii_mask.Masked, prompt: str, forms: pii_mask.Forms, *,
          seed: int | None = None) -> Dress:
    """Заменить метки в `masked.text` подделками; вернуть таблицу для `undress`.

    `prompt` — весь отправляемый текст до маски (инструкция тоже): подделка не должна
    в нём встречаться. `seed` — для тестов; без него подделки свои на каждый запрос.
    Бросает `MaskRefused`, если подделку не подобрать или метка осталась."""
    m = _load()
    rnd = random.Random(seed if seed is not None else secrets.randbits(64))
    # гейт на строке, которая уходит: подделка рядом с соседним словом не должна сложить
    # форму графа («Северный» + подделка «Маяк» = узел «Северный Маяк», n793-out6 DS-C1);
    # вне областей данных формы могут быть и до подстановки — счёт не должен вырасти;
    # один `pk` на все попытки: отвергнутые подделки остаются занятыми
    pk = _Picker(m, prompt, forms, rnd)
    for _whole in range(_TRIES_WHOLE):
        d, text = _dress_once(m, masked, pk)
        # формы, не задевающие подделку, до и после одни и те же — считаем только задевающие
        near = pii_mask.forms_touching(forms, (w for prs in d.pairs.values() for _o, f in prs
                                               for w in re.findall(r"[^\W_]+", f)))
        if pii_mask.postcondition_hits(text, near) <= pii_mask.postcondition_hits(masked.text, near):
            break
    else:
        raise pii_mask.MaskRefused("подделка сложилась с соседним словом в форму графа — "
                                   "отправка отменена")
    masked.text = text
    masked.dress = d
    return d


# ------------------------------------------------------------------ обратно

@dataclasses.dataclass
class _Slot:
    gid: str
    fake: str           # слово подделки, как ушло
    orig: str           # исходное слово (или вся форма системы, если подделка короче)
    cases: frozenset    # (падеж, число) подделки
    gender: str | None  # род человека; у системы None


def _index(m, d: Dress) -> tuple[dict[str, list[_Slot]], dict[str, list[_Slot]]]:
    """Слова подделок: точная форма → слоты и лемма → слоты."""
    exact: dict[str, list[_Slot]] = collections.defaultdict(list)
    by_lemma: dict[str, list[_Slot]] = collections.defaultdict(list)
    for gid, pairs in d.pairs.items():
        for orig, fake in pairs:
            fw, ow = _WORD.findall(fake), _WORD.findall(orig)
            whole = len(fw) != len(ow)
            for i, w in enumerate(fw):
                o = orig if whole else ow[i]
                if len(w) == 1:
                    continue   # инициал по одной букве не разворачивается: «В» в начале фразы
                lemmas, _c = _lemmas(m, w)
                g = d.gender.get(gid)
                s = _Slot(gid, w, o, frozenset(_cases(m, w, g)), g)
                exact[_e(w)].append(s)
                for lem in lemmas:
                    by_lemma[lem].append(s)
    return exact, by_lemma


def _lemmas(m, word: str) -> tuple[set[str], set[tuple[str, str]]]:
    if _ABBR.match(word) and word[:2].isupper():
        return {_e(_ABBR.match(word).group(1))}, set()
    lemmas, cases = set(), set()
    for p in m.parse(word):
        lemmas.add(_e(p.normal_form))
        cases.add(_case(p))
    # подставная фамилия, которой нет в словаре: «Зимогоровым» → «зимогоров»; только с
    # заглавной — иначе «опала» стала бы «Опалом»
    w = _e(word)
    if word[:1].isupper():
        for (_g, _c), end in _SURN_END.items():
            if end and w.endswith(end):
                lemmas.add(w[: -len(end)])
    return lemmas, cases


def _cases(m, word: str, gender: str | None) -> set[tuple[str, str]]:
    """(падеж, число) слова — по разборам рода человека, если такие есть: «Ракитина» —
    жен. им., а не муж. род. («Ракитиной» иначе развернулось бы в именительный)."""
    ps = m.parse(word)
    if gender:
        ps = [p for p in ps if gender in p.tag or "plur" in p.tag] or ps
    return {_case(p) for p in ps}


def _orig_in_case(m, s: _Slot, cases: set) -> str | None:
    """Исходное слово в падеже ответа: склонение исходного имени pymorphy."""
    if not cases or " " in s.orig:
        return None
    r, p = _role(m, s.orig)
    if s.gender:
        p = next((q for q in sorted(m.parse(s.orig), key=lambda q: "Fixd" in q.tag)
                  if (r is None or r in q.tag) and s.gender in q.tag), p)
    for case, num in sorted(cases):
        q = p.inflect({case, num} if num == "plur" else {case})
        if q is not None and _e(q.normal_form) == _e(p.normal_form):
            return _keep_case(s.orig, q.word)
    return None


def _fits(s: _Slot, w: str) -> bool:
    """Подделка-имя ушла с заглавной — строчное слово ответа не она: «причины корней»
    при подделке «Корней» (n793-out4 H-C2). Подделка со строчной (система «портал») —
    в любом регистре: в начале фразы модель напишет её с заглавной."""
    return not s.fake[:1].isupper() or w[:1].isupper()


def undress(reply: str, d: Dress, *, sent: str = "") -> tuple[str, int]:
    """Ответ модели обратно в имена: `(текст, сколько подделок осталось)`. Счёт —
    в `d.back`. `sent` — отправленный текст: имена ответа, которых в нём не было, —
    новые имена модели (считаются, не трогаются)."""
    m = _load()
    exact, by_lemma = _index(m, d)
    d.back = collections.Counter()
    sent_words = {_e(w) for w in _WORD.findall(sent)}

    def one(mt: re.Match) -> str:
        w = mt.group(0)
        hit = [s for s in exact.get(_e(w), ()) if _fits(s, w)]
        if hit and len({s.gid for s in hit}) == 1:
            d.back["exact"] += 1
            return next((s for s in hit if s.fake == w), hit[0]).orig
        lemmas, cases = _lemmas(m, w)
        slots = [s for lem in lemmas for s in by_lemma.get(lem, ()) if _fits(s, w)]
        if not slots or len({s.gid for s in slots}) != 1:
            if w[:1].isupper() and _e(w) not in sent_words and any(
                    ("Name" in p.tag or "Surn" in p.tag) for p in m.parse(w)):
                d.back["new"] += 1
            return w
        for s in slots:
            if s.cases & _cases(m, w, s.gender):
                d.back["case"] += 1
                return s.orig
        for s in slots:
            o = _orig_in_case(m, s, _cases(m, w, s.gender))
            if o:
                d.back["case"] += 1
                return o
        d.back["canon"] += 1
        return d.canon.get(slots[0].gid) or slots[0].orig

    def groups(w: str) -> set[str]:
        lemmas, _c = _lemmas(m, w)
        return ({s.gid for lem in lemmas for s in by_lemma.get(lem, ()) if _fits(s, w)}
                | {s.gid for s in exact.get(_e(w), ()) if _fits(s, w)})

    # ссылка целиком на подделку — имя узла, а не форма: «[[Глеба Ладыгина]]» битая.
    # Только когда КАЖДОЕ слово цели — подделка этого узла, как `pii_mask.unmask`:
    # «[[Встреча с Глебом 12.10]]» — заметка встречи, не узел человека (n793-out4 H-C1)
    def link(mt: re.Match) -> str:
        target = mt.group(1)
        per = [groups(w) for w in _WORD.findall(target)]
        gids = set().union(*per) if per else set()
        if per and all(per) and len(gids) == 1 and d.canon.get(next(iter(gids))):
            d.back["exact"] += 1
            return "[[" + d.canon[next(iter(gids))]
        return "[[" + _WORD.sub(one, target)

    # форма с инициалами (фамилия и две буквы) — обратно целиком, раньше слов: буква отдельно
    # не индексируется. Только многословная и по границам слов: одиночная буква-подделка
    # («А» от мусорного спана NER) внутри «ДАП» — не имя (n793-out6, опыт false_undress)
    for gid, pairs in d.pairs.items():
        for orig, fake in sorted(pairs, key=lambda x: -len(x[1])):
            ws = _WORD.findall(fake)
            if len(ws) > 1 and any(len(w) == 1 for w in ws):
                reply, n = re.subn(rf"(?<![^\W_]){re.escape(fake)}(?![^\W_])",
                                   lambda _mt, o=orig: o, reply)
                d.back["exact"] += n
    text = re.sub(r"\[\[([^\]|#\n]+)", link, reply)
    parts = re.split(r"(\[\[[^\]|#\n]+)", text)
    text = "".join(p if p.startswith("[[") else _WORD.sub(one, p) for p in parts)
    # осталась — слово, которое всё ещё разбирается в подделку (двум группам сразу и т. п.):
    # в граф оно уйдёт помеченным «⟦?…⟧», как обещают сообщения вызывающих (n793-out4 S-I3)
    left = 0

    def mark(mt: re.Match) -> str:
        nonlocal left
        if not groups(mt.group(0)):
            return mt.group(0)
        left += 1
        return f"⟦?{mt.group(0)}⟧"

    text = _WORD.sub(mark, text)
    d.back["left"] = left
    return text, left

