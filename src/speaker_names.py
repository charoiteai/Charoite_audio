"""Единственное место, где решается, можно ли доверять опознанному имени.

Опознание работает в двух режимах: с моделью голосов («Собеседник 1/2/…») и
без неё, по канальным меткам. Проверки доверия писались отдельно для каждого,
и разошлись: гвард против «Саш, ну а кто…» (обращение принималось за имя
говорящего) появился только в мультиспикерном режиме, а безмодельный —
поведение ПО УМОЛЧАНИЮ, потому что ERes2Net в поставку не входит, — остался с
одним промптом к лёгкой модели.

Цена ошибки здесь выше цены пропуска: `rename_speaker` переписывает метку
задним числом по всей встрече, минутки и граф наследуют её молча, поручение
уходит не тому человеку. «Собеседник 2» честен, неверное имя — врёт.

Правила, которые обязаны действовать в обоих режимах:

    1. Имя владельца не достаётся собеседнику. Сравнение — по СЛОВАМ
       `user_name`, а не со всей строкой: в конфиге просят «ваше имя», и там
       обычно стоит имя с фамилией.
    2. Имя, которое звучит только в репликах самой метки и не является
       представлением, — это обращение к кому-то другому, а не имя говорящего.
    3. Имени, которого нет в стенограмме, не существует: модель его выдумала.
    4. Падежи приводятся к известным людям графа («Полин» → «Полина»), чтобы
       в графе не появился узел в звательном падеже.

Модуль намеренно без зависимостей: чистые строки, поэтому проверяется тестами
без звука, PortAudio и запущенной модели.
"""
from __future__ import annotations

import re
import typing

import once
import voice_pitch

# Планка длины. Ниже трёх — мусор от лёгкой модели («Ок», «Да»); выше
# пятнадцати — не имя, а склеенная фраза. Безмодельный режим раньше пускал
# двухбуквенные, и это была единственная разница между ветками не в пользу
# осторожности.
MIN_LEN = 3
MAX_LEN = 15

# По этому префиксу имя из разговора склеивается с известным человеком графа:
# «Полин» → «Полина», «Андрюх» → «Андрей». Тем же префиксом узнаётся владелец,
# названный уменьшительно.
PREFIX = 4

# Самопредставление: только оно оправдывает имя, прозвучавшее исключительно в
# собственных репликах говорящего.
_INTRO = r"(это|я|меня\s+зовут)\s+"

# Звательный падеж — имя на «-а/-я» без последней гласной: «Коль», «Ань»,
# «Саш». Префиксная склейка выше этого не видит, когда расходится четвёртая
# буква: «Коль» и «Коля» — и 05.09 Коля получил второй узел «Коль» в том же
# графе. Обратный ход детерминирован и узкий: мягкий знак → «я» на месте
# знака ИЛИ после него (Коль → Коля, Иль → Илья, Наталь → Наталья), «й» у
# короткого имени → «я» (Зой → Зоя), другой согласный у имени в 3–5 букв →
# «а» (Саш → Саша, Мариш → Мариша). Длинные и похожие на фамилию
# («…ов/…ев/…ин/…ын») не трогаем: «Александр» + «а» и «Ильин» + «а» — другие
# люди. Стоп-лист — мужские имена, чья форма на «-а/-я» есть самостоятельное
# женское имя; он же выключает и префиксную склейку ниже (DS, круг 1 #508).
_VOWELS = "аеёиоуыэюя"
_SURNAME_TAILS = ("ов", "ев", "ёв", "ин", "ын")
_NOT_VOCATIVE = frozenset({"влад", "кир", "дан", "юлий"})


def nominative_candidates(name: str) -> tuple[str, ...]:
    """Именительные формы, звательным падежом которых могло быть `name`.

    Пусто — если имя кончается гласной (это уже именительный), короче
    MIN_LEN, длиннее пяти букв с обычным согласным, похоже на фамилию или
    стоит в стоп-листе. Сама по себе форма ничего не решает: склейка
    происходит только при точном совпадении с ОДНИМ известным человеком, так
    что «Игорь» → («Игоря», «Игорья») безвредно, пока таких людей нет."""
    n = (name or "").strip()
    low = n.casefold()
    if len(n) < MIN_LEN or not n.replace("-", "").isalpha() or low in _NOT_VOCATIVE:
        return ()
    last = low[-1]
    if last in _VOWELS:
        return ()
    if last == "ь":
        return (n[:-1] + "я", n + "я")     # Коль → Коля; Иль → Илья (GLM I1, круг 1 #508)
    # Творительный падеж — второе после звательного, как модель отдаёт имя
    # из речи («договорились с Сашей», «созвон с Ромой»): «Сашей» → Саша,
    # «Колей» → Коля, «Ромой» → Рома, «Иваном» → Иван, «Игорем» → Игорь.
    # Склейка по-прежнему только при точном единственном совпадении с
    # известным человеком (GLM Critical 2, аудит памяти 07.09: ~50 битых
    # «Сашей/Ромой» в архиве — тот же механизм, что у звательного до №180).
    # «-ей»: основа на «и» — женское имя на «-ия» («Марией» → Мария,
    # «Юлией» → Юлия, DS r3 M1); иначе только короткие формы (Сашей, Колей —
    # ≤5 букв): «Алексей», «Андрей», «Сергей» — именительный, и «Алексей» →
    # «Алекса» склеил бы с чужим узлом (DS r2 B2). «Наташей», «Гришей»
    # этим гейтом не ловятся — принятая цена.
    if low.endswith("ей"):
        base = n[:-2]
        if base[-1:].casefold() == "и" and len(n) >= 5:
            return (base + "я",)
        if 4 <= len(n) <= 5:
            return (base + "я", base + "а")
        return ()
    if low.endswith("ой") and len(n) >= 4:
        return (n[:-2] + "а",)
    if low.endswith(("ом", "ем")) and len(n) >= 5:
        base = n[:-2]
        forms = [base, base + "ь"]
        if base[-1:].casefold() == "е":
            forms.append(base + "й")   # Андреем → Андрей, Сергеем → Сергей (DS r2 M1)
        return tuple(forms)
    if last == "й":
        return (n[:-1] + "я",) if len(n) <= 4 else ()
    if len(n) > 5 or low.endswith(_SURNAME_TAILS):
        return ()
    return (n + "а",)


def resolve_vocative(name: str, known) -> str | None:
    """Известный человек, к которому обращались «name»: «Коль» + («Коля»,) → «Коля».

    Только точное совпадение выведенной формы с известным именем, без
    догадок: «Мариш» при известной «Марине» остаётся как есть (Мариша ≠
    Марина). Два известных на разные формы одного обращения («Илья» и
    «Иля» для «Иль») — не гадаем, пусть решает следующий проход."""
    forms = {f.casefold() for f in nominative_candidates(name)}
    if not forms:
        return None
    hits = [k for k in known if str(k).casefold() in forms]
    return hits[0] if len(hits) == 1 else None


def _clean(raw: str) -> str:
    """Обрезка пунктуации и кавычек, единый регистр имени."""
    return str(raw or "").strip().strip(".,!?:;«»\"'()").capitalize()


def _words(full_name: str) -> list[str]:
    return [w for w in re.split(r"[\s,]+", (full_name or "").casefold()) if w]


def is_owner(name: str, owner_name: str) -> bool:
    """Это владелец под другим написанием?

    Сравниваем со всеми словами `sufler.user_name`: «Игорь» — это «Игорь
    Ветров», а не новый участник встречи. Уменьшительные ловим префиксом — то
    же правило, которым «Полин» приводится к «Полина», только с обратным
    знаком: похоже на владельца — не присваиваем никому.
    """
    if not name or not owner_name:
        return False
    low = name.casefold()
    for word in _words(owner_name):
        if low == word:
            return True
        short = min(PREFIX, len(low), len(word))
        if short >= PREFIX and low[:short] == word[:short]:
            return True
    return False


def is_counterpart(speaker: str, owner_name: str) -> bool:
    """Гейт мгновенных ответов: ⚡ стреляет по вопросу любого НЕ-владельца.

    Прежний гейт в демоне проверял startswith(«Собеседник») — и как только
    name_loop опознавал собеседника по имени, мгновенные ответы для него
    умирали до конца встречи (аудит 14.08). Живая разметка владельца не
    угадывает, а имя владельца собеседнику не достаётся (правило 1) — значит
    всё, что не владелец, и есть «та сторона»: и «Собеседник N», и уже
    опознанный «Сергей». Владелец отсекается той же пословной сверкой
    user_name, что и везде в этом модуле.
    """
    return bool((speaker or "").strip()) and not is_owner(speaker, owner_name)


def heard_forms(name: str, sample: str) -> tuple[str, ...]:
    """Формы, в которых `name` слышно в стенограмме: само имя целым словом и
    слова с заглавной, чьей звательной или творительной формой оно могло быть
    («Саш» для «Саша», «Колей» для «Коля»). Пусто — имени в разговоре не было.

    Правило 3 требует, чтобы имя звучало в тексте, но модель отдаёт
    именительный падеж, а в речи имя чаще звучит обращением: «Тань, глянь»
    — это «Таня». Прямая подстрока отвергала бы такое имя как выдуманное.
    Обратный ход — тем же `nominative_candidates`, которым граф клеит
    обращение к узлу человека, и только из положения, где так и звучит имя:
    обращение отделено знаком или стоит в конце («Коль, ты тут?», «Саш!»,
    «…спроси, Тань»), творительный падеж — после «с»/«со» («договорились с
    Сашей»). Слово с заглавной в начале предложения без такого контекста
    («Ром был отличный», «Людей было много») формой имени не считается
    (DS I1 по #551); косвенные падежи третьего лица («звонил Тане») сюда
    намеренно не входят — цена ложного имени выше цены пропуска.
    """
    low = (name or "").casefold()
    if not low:
        return ()
    forms: list[str] = []
    # целым словом, не подстрокой: «Ян» в «Январь» уже ловили в перештамповке
    # минуток (DS Critical по #464), «Коль» в «кольцо» — та же дыра (критика
    # GLM по #551)
    if _whole_word(low, sample):
        forms.append(low)
    for m in re.finditer(r"(?<!\w)([А-ЯЁA-Z][\w-]*)", sample):
        word = m.group(1).strip("-")
        if not word or low not in {c.casefold() for c in nominative_candidates(word)}:
            continue
        if _instrumental_like(word):
            heard = re.search(r"(?<!\w)со?\s+$", sample[max(0, m.start() - 4):m.start()], re.I) is not None
        else:
            heard = re.match(r"\s*(?:[,!?…—–:;.-]|\n|$)", sample[m.end():m.end() + 3]) is not None
        if heard and word.casefold() not in forms:
            forms.append(word.casefold())
    return tuple(forms)


def _instrumental_like(word: str) -> bool:
    """«Сашей», «Ромой», «Иваном», «Игорем» — творительный падеж, который
    nominative_candidates умеет разворачивать; ему нужен предлог «с» рядом."""
    low = word.casefold()
    return len(low) >= 4 and low.endswith(("ей", "ой", "ом", "ем"))


def _whole_word(form: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(form)}(?!\w)", text, re.I) is not None


def _own_lines_only(name: str, sample: str, label: str,
                    forms: tuple[str, ...] | None = None) -> bool:
    """Имя звучит ТОЛЬКО в репликах самой метки и это не представление.

    Формат хвоста стенограммы — «[ЧЧ:ММ] метка: текст», метка не в начале
    строки, поэтому ищем «] метка:», а не `startswith`. `forms` — в каких
    формах имя слышно (heard_forms): обращение «Саш, а ты…» в собственной
    реплике — тот самый случай, ради которого правило и писалось. Не
    передали — считаем сами: молчаливого отката к голой подстроке нет
    (критика GLM по #551).
    """
    if forms is None:
        forms = heard_forms(name, sample)
    if not forms:
        return False
    lines_with = [ln for ln in sample.splitlines()
                  if any(_whole_word(f, ln) for f in forms)]
    if not lines_with:
        return False
    own = [ln for ln in lines_with if re.search(rf"\]\s*{re.escape(label)}\s*:", ln)]
    if len(own) != len(lines_with):
        return False    # имя звучало и с другой стороны — законный источник
    intro = _INTRO + "(?:" + "|".join(re.escape(f) for f in forms) + ")"
    return not re.search(intro, sample, re.I)


#: Причины отказа гварда (№502) — код, по которому журнал называет сработавшее
#: правило. Текст для человека — только в REASON_TEXT: журнал пересборки и журнал
#: демона берут его оттуда, а шапка пересылаемой стенограммы причин не несёт
#: вовсе — плашка общая, действие владельца от причины не меняется.
REASON_EMPTY = "empty"
REASON_NOT_A_WORD = "not_a_word"
REASON_LENGTH = "length"
REASON_LABEL = "label"
REASON_NOT_HEARD = "not_heard"
REASON_OWNER = "owner"
REASON_OWN_LINES = "own_lines"
REASON_VOICE = "voice"

REASON_TEXT = {
    REASON_EMPTY: "пусто или NONE",
    REASON_NOT_A_WORD: "не одно слово из букв",
    REASON_LENGTH: f"длина вне {MIN_LEN}–{MAX_LEN} букв",
    REASON_LABEL: "это метка, а не имя",
    REASON_NOT_HEARD: "не звучало в разговоре",
    REASON_OWNER: "имя владельца",
    REASON_OWN_LINES: "звучит только в своих репликах — обращение к другому",
    REASON_VOICE: "голос и род имени противоречат",
}


class NameVerdict(typing.NamedTuple):
    """Вердикт гварда: `name` — принятое имя или None; `reason` — код причины
    отказа (пусто — принято); `said` — что предложила модель, после чистки;
    `resolved` — имя после приведения падежа, ровно то, что судили последние
    правила («Андрюх» → «Андрей» → владелец)."""
    name: str | None
    reason: str
    said: str
    resolved: str


def judge_name(raw: str, *, sample: str, label: str,
               owner_name: str = "", known: tuple[str, ...] | list[str] = (),
               voice: str | None = None, name_gender: str | None = None,
               ) -> NameVerdict:
    """Имя, которому можно доверять, — с одинаковой строгостью в обоих режимах
    опознания, и какое правило отказало, если нет.

    raw    — что предложила модель (может быть мусором и «NONE»)
    sample — хвост стенограммы, по которому она решала
    label  — метка говорящего, которую собираемся заменить
    owner_name — `sufler.user_name`, целиком, как в конфиге
    known  — имена людей графа для приведения падежей
    voice  — регистр голоса этой метки: «low» / «high» / None (voice_pitch)
    name_gender — род имени: «male» / «female» / «unisex» / None

    Последние два — про случай «мужчину назвали Анной»: имя приходит из
    текста, и про голос оно не знает ничего. Отказ бывает только при
    уверенном противоречии: обе стороны определённы и противоположны.
    Пусто, «не знаю» или «unisex» («Саша», «Женя») ничего не блокируют.
    """
    said = name = _clean(raw)
    if not name or name.upper() == "NONE":
        return NameVerdict(None, REASON_EMPTY, said, name)
    if not name.replace("-", "").isalpha():
        return NameVerdict(None, REASON_NOT_A_WORD, said, name)
    if not (MIN_LEN <= len(name) <= MAX_LEN):
        return NameVerdict(None, REASON_LENGTH, said, name)
    if name.casefold() == label.casefold() or name.casefold().startswith("собеседник"):
        return NameVerdict(None, REASON_LABEL, said, name)
    forms = heard_forms(name, sample)
    if not forms:
        return NameVerdict(None, REASON_NOT_HEARD, said, name)    # модель выдумала имя, которого в разговоре не было

    # падежи — по известным людям графа, до проверки владельца: «Игорёк» из
    # разговора должен сначала стать «Игорь», чтобы владелец узнался.
    if known and name not in known and name.casefold() not in _NOT_VOCATIVE:
        # сначала точный обратный ход из звательного падежа («Коль» → «Коля»),
        # потом — префиксная склейка («Андрюх» → «Андрей»). Стоп-лист
        # закрывает оба хода: «Влад» не становится «Владой» и по префиксу.
        voc = resolve_vocative(name, known)
        if voc:
            name = voc
        else:
            low = name.casefold()
            hit = [k for k in known if k.casefold().startswith(low[:PREFIX])]
            if len(hit) == 1:
                name = hit[0]

    if is_owner(name, owner_name):
        return NameVerdict(None, REASON_OWNER, said, name)
    if _own_lines_only(name, sample, label, forms):
        return NameVerdict(None, REASON_OWN_LINES, said, name)
    if voice_pitch.contradicts(voice, name_gender):
        return NameVerdict(None, REASON_VOICE, said, name)     # басовитый голос и женское имя — оставляем «Собеседник N»
    return NameVerdict(name, "", said, name)


def trustworthy_name(raw: str, *, sample: str, label: str,
                     owner_name: str = "", known: tuple[str, ...] | list[str] = (),
                     voice: str | None = None, name_gender: str | None = None,
                     ) -> str | None:
    """Имя, которому можно доверять, или None — вердикт `judge_name` без причины."""
    return judge_name(raw, sample=sample, label=label, owner_name=owner_name, known=known,
                      voice=voice, name_gender=name_gender).name


def is_refusal(verdict: NameVerdict) -> bool:
    """Отверг ли гвард предложенное имя. «Пусто или NONE» — не отказ, а ответ «имени
    нет»: он не считается предложенным именем и не пишется в журнал — одно решение
    на пересборку и живой цикл (выходной круг 1 по №502, M1)."""
    return verdict.reason not in ("", REASON_EMPTY)


def refusal_line(label: str, verdict: NameVerdict) -> str | None:
    """Строка журнала об отвергнутом имени: что предложила модель, во что его
    привели, для какой метки и какое правило отказало. Одна на пересборку и демон,
    чтобы текст причины жил в одном месте (REASON_TEXT). Не отказ (`is_refusal`) —
    None: писать нечего."""
    if not is_refusal(verdict):
        return None
    moved = verdict.resolved != verdict.said
    shown = f"«{verdict.said}»" + (f" (→ {verdict.resolved})" if moved else "")
    return f"{shown} для «{label}» не принято — {REASON_TEXT[verdict.reason]}"


#: Пространство реестра «сказать один раз» для отказов гварда в живом цикле имён.
REFUSALS = "names"


def say_refusal(label: str, verdict: NameVerdict, stream=None) -> bool:
    """Строка журнала об отказе гварда — один раз за встречу на тройку «метка,
    предложенное имя, правило» (№502). Живой цикл имён переспрашивает модель на
    каждом такте роста стенограммы, и та же строка иначе повторялась бы. Другое
    правило для той же пары — новое событие, звучит снова. «Пусто или NONE» —
    не отвергнутое имя, а ответ «имени нет» (одиночная ветка так и просит
    отвечать), и в журнал он не идёт."""
    line = refusal_line(str(label), verdict)
    if line is None:
        return False
    key = (REFUSALS, (str(label), verdict.said, verdict.reason))
    return once.say(key, "имена: " + line, stream=stream)


def forget_refusals() -> None:
    """Новая встреча — чистый лист: реестр живёт на процесс демона, и отказ новой
    встречи иначе молчал бы, если такая же тройка прозвучала на прошлой."""
    once.reset(REFUSALS)
