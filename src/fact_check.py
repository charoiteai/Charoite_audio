"""Сверка фактических якорей документа со стенограммой.

Замер 22.07 на реальной встрече: из четырёх прогонов минуток один приписал
задачу TASK 3242, которой в стенограмме нет ни разу. Внешне такой пункт
неотличим от настоящего — та же формулировка, тот же тон.

Номера задач и даты — вещи буквальные: если они прозвучали, они есть в тексте
стенограммы. Поэтому их не надо ни переспрашивать у модели, ни гонять через
NLI — достаточно поиска по строке. Стоит миллисекунды против четырёх прогонов
35B-модели, а ловит ровно тот класс ошибок, который дороже всего обходится:
выдуманный номер задачи или срок уезжает в поручение и живёт своей жизнью.

Найденное НЕ вычищаем: пропуск верного факта хуже пометки. Дописываем сноску,
человек решает сам. Одно исключение — срок поручения (№805): из четырёх сроков
минуток на длинных встречах один-два модель додумывает («до 28 ноября» при «к
28-му»), а срок в окне «Задачи» читается как факт и рисуется чипом. Срок, который
речь не подтверждает, меняется в строке на пометку «срок не из речи» (её пишет
`action_items.check_deadlines`, слова — `task_line.DUE_UNHEARD_MARKS`); дата модели
остаётся в сноске — `annotate` стоит в цепочке раньше.

Дата без месяца («к 28-му») подтверждает дату документа, только если она в окне
дня встречи (`due_window`): ближайший такой день не раньше «встреча − неделя» или
не раньше самой встречи. Люфт — тот же, что у срока в приложении (Swift
`TaskDue.restatedGraceDays`); обе стороны сверяет общая таблица
`tests/fixtures/due_window.json`. Решение «дата подтверждена речью» одно —
`_date_heard`: по нему и сноска, и проверка срока, иначе строка оставалась бы со
сроком, а сноска называла бы тот же срок выдуманным (№805, in3 Sonnet I5).
"""
import datetime
import re

# Месяц — только точными словоформами (именительный, родительный, дательный,
# предложный) и обычными сокращениями. Сравнение префиксом давало ложные даты:
# «2 масштаба» → 02.05, «23 магазина» → 23.05, и выдуманный срок «02.05» в
# документе проходил сверку (№762b).
_MONTH_FORMS = {
    "01": "январь января январю январе янв",
    "02": "февраль февраля февралю феврале фев февр",
    "03": "март марта марту марте мар",
    "04": "апрель апреля апрелю апреле апр",
    "05": "май мая маю мае",
    "06": "июнь июня июню июне июн",
    "07": "июль июля июлю июле июл",
    "08": "август августа августу августе авг",
    "09": "сентябрь сентября сентябрю сентябре сен сент",
    "10": "октябрь октября октябрю октябре окт",
    "11": "ноябрь ноября ноябрю ноябре ноя нояб",
    "12": "декабрь декабря декабрю декабре дек",
}
MONTHS = {form: num for num, forms in _MONTH_FORMS.items() for form in forms.split()}

# День словом: порядковые именительный (среднего рода: «четвёртое октября»),
# родительный, дательный, предложный. Основа + 4 окончания.
_ORD_STEMS = {
    "перв": 1, "втор": 2, "четвёрт": 4, "пят": 5, "шест": 6, "седьм": 7,
    "восьм": 8, "девят": 9, "десят": 10, "одиннадцат": 11, "двенадцат": 12,
    "тринадцат": 13, "четырнадцат": 14, "пятнадцат": 15, "шестнадцат": 16,
    "семнадцат": 17, "восемнадцат": 18, "девятнадцат": 19, "двадцат": 20,
    "тридцат": 30,
}
_ORD_ENDINGS = ("ое", "ого", "ому", "ом")
ORDINAL_DAYS: dict[str, int] = {}
for _stem, _day in _ORD_STEMS.items():
    for _end in _ORD_ENDINGS:
        ORDINAL_DAYS[_stem + _end] = _day
for _end, _form in zip(_ORD_ENDINGS, ("ье", "ьего", "ьему", "ьем")):
    ORDINAL_DAYS["трет" + _form] = 3
for _word, _day in list(ORDINAL_DAYS.items()):  # «четвертого» без «ё» тоже
    ORDINAL_DAYS[_word.replace("ё", "е")] = _day
# «двадцать пятого», «тридцать первого»: десяток — отдельное слово перед единицей
TENS_WORDS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}


def _alt(words) -> str:
    return "|".join(sorted(map(re.escape, words), key=len, reverse=True))


# «TASK 3318», «РБ-1234» — латиница и кириллица, дефис необязателен
TASK_RE = re.compile(r"\b([A-Za-zА-Яа-яЁё]{2,12})\s*[-–—]?\s*(\d{3,6})\b")
# «15.08», «15/08», «15-08», «04.10.2026», «04.10.2026г.». Хвост (?![./-]?\d)
# отсекает версии вида 1.5.2; год — только четыре цифры, иначе «1.5.20» было бы
# датой; после числа — граница слова («3.10a» — не дата). «г» — только сразу за
# годом (группа 4): «2.05г», «15.2гб», «п. 1.10г» — граммы, объём, подпункт;
# единица через пробел («15.2 гб») — долг зоны вместе с цепочками. Цепочки
# (1.15.2, 2026-10-04, диапазоны, дроби) голова \b не разбирает — долг зоны:
# один разбор цепочки [\d./-]+, а не ещё один lookaround
DATE_NUM_RE = re.compile(
    r"\b(\d{1,2})([./-])(\d{1,2})(?:[./-]((?:19|20)\d{2}))?(?![./-]?\d)(?(4)г?)(?!\w)")
# «версия 1.5», «v2.1» — номер релиза, а не дата
VERSION_CTX_RE = re.compile(r"(?:верси\w*|version|v\.?)\s*$", re.I)
# число и месяц — через пробел на одной строке: любой горизонтальный, как \s
# базы (23\u00a0августа, 23\u2003августа); перевод строки, табуляция и
# разделители строк — не пробел
_SP = "[ \u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]+"
# «23 августа», «4-го октября», «4го октября»: число с окончанием или без
DATE_WORD_RE = re.compile(
    rf"\b(\d{{1,2}})(?:-?(?:го|ого|е|ое))?{_SP}({_alt(MONTHS)})\b")
# «четвёртого октября», «двадцать пятого октября»: десяток и единица — только
# соседние слова без знака препинания («было двадцать, пятого октября» — 05.10)
DATE_ORDINAL_RE = re.compile(
    rf"\b(?:({_alt(TENS_WORDS)}){_SP})?({_alt(ORDINAL_DAYS)}){_SP}({_alt(MONTHS)})\b")


def _add(out: set, day: int, month: str) -> None:
    if 1 <= day <= 31:
        out.add(f"{day:02d}.{month}")


def _dates(text: str) -> set[str]:
    """Все даты текста в едином виде ДД.ММ — иначе «23 августа» и «23.08» не сойдутся.

    Один экстрактор, одна таблица месяцев. Не даты: «четвёртого числа» (без
    месяца — граница, которую не закрываем), версии «1.5.2», день больше 31."""
    out: set[str] = set()
    for match in DATE_NUM_RE.finditer(text):
        day, _, mon, year = match.groups()
        if not (1 <= int(day) <= 31 and 1 <= int(mon) <= 12):
            continue
        if VERSION_CTX_RE.search(text[max(0, match.start() - 12):match.start()]):
            continue  # «версия 1.5»
        # 1.5 — это и 1 мая, и версия 1.5. Различаем по форме записи: у даты
        # месяц пишут двузначным (15.08) либо день заведомо не месяц (23.8).
        # Однозначный месяц при дне ≤ 12 (1.5, 2.3) считаем номером версии:
        # пропустить сомнительную дату дешевле, чем дёргать ложной тревогой.
        # С годом (1.5.2026) сомнения нет.
        if len(mon) == 1 and int(day) <= 12 and not year:
            continue
        out.add(f"{int(day):02d}.{int(mon):02d}")
    lowered = text.lower()
    for day, word in DATE_WORD_RE.findall(lowered):
        _add(out, int(day), MONTHS[word])
    for tens, word, mon in DATE_ORDINAL_RE.findall(lowered):
        day = ORDINAL_DAYS[word]
        if tens:
            if day > 9:  # «двадцать десятого» — не день
                continue
            day += TENS_WORDS[tens]
        _add(out, day, MONTHS[mon])
    return out


def _is_year(num: str) -> bool:
    """«до начала 2028» — это год, а не задача Начала-2028."""
    return len(num) == 4 and 1990 <= int(num) <= 2100


def _tasks(text: str) -> set[str]:
    """Номера задач как «система+номер», регистр и дефисы не важны."""
    return {f"{name.lower()} {num}"
            for name, num in TASK_RE.findall(text) if not _is_year(num)}


#: люфт окна дня встречи — как Swift `TaskDue.restatedGraceDays`: срок, пропущенный
#: на днях, приносят на встречу именно потому, что он горит («к 30-му» на встрече
#: 02.11 — это 30.10). Сверяет общая таблица tests/fixtures/due_window.json.
DUE_GRACE_DAYS = 7


def due_dates(day: int, meeting: datetime.date) -> set[datetime.date]:
    """Даты, которыми день без месяца («к 28-му») бывает на встрече `meeting`:
    ближайший такой день не раньше «встреча − DUE_GRACE_DAYS» (просрочка) и
    ближайший не раньше самой встречи. Считается датами, а не числами месяца:
    через границу месяца и года; дня, которого в месяце нет (31.02), не бывает —
    берётся следующий месяц с таким днём. Год — тот же, что выводит приложение
    (`TaskDue.status` с якорем встречи): общая таблица это и сверяет."""
    out: set[datetime.date] = set()
    if not 1 <= day <= 31:
        return out
    for floor in (meeting - datetime.timedelta(days=DUE_GRACE_DAYS), meeting):
        year, month = floor.year, floor.month
        for _ in range(3):
            try:
                cand = datetime.date(year, month, day)
            except ValueError:
                cand = None
            if cand is not None and cand >= floor:
                out.add(cand)
                break
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def due_window(day: int, meeting: datetime.date) -> set[str]:
    """`due_dates` в виде ДД.ММ — так даты стоят в строке поручения."""
    return {d.strftime("%d.%m") for d in due_dates(day, meeting)}


# Что в речи днём не бывает: полные даты (их судит `_dates`), время «10:40» и метки
# реплик «[10:00]». Без вычета день 28 «звучал» в «сдам 28.11», а день 10 — в
# каждой метке встречи в 10:xx (№805 out1: DS I1, Haiku I1).
_NOT_A_DAY = re.compile(r"\[?\d{1,2}:\d{2}(?::\d{2})?\]?")


def _day_spoken(day: int, low: str) -> bool:
    """День без месяца, названный как день (речь в нижнем регистре): «к 28-му»,
    «до 28», «28-го», «28 числа», «двадцать восьмого», «by the 28th», «28号».
    Голое число («28 человек», «12 мин») днём не считается."""
    for rx in (DATE_NUM_RE, DATE_WORD_RE, DATE_ORDINAL_RE, _NOT_A_DAY):
        low = rx.sub(" ", low)
    n = rf"(?<![\d.,:]){day}(?![\d.,:]\d)"
    if re.search(
            rf"\b(?:к|ко|до|по|на|с|со|дедлайн\w*|срок\w*|by|before|until|till|on|due)\s+(?:the\s+)?{n}(?!\d)"
            rf"|{n}\s*-?\s*(?:го|ого|е|ое|ому|му|м|st|nd|rd|th)\b"
            rf"|{n}\s+(?:числа|число|числу)\b"
            rf"|{n}\s*[号日]", low):
        return True
    for tens, word in re.findall(rf"\b(?:({_alt(TENS_WORDS)}){_SP})?({_alt(ORDINAL_DAYS)})\b", low):
        value = ORDINAL_DAYS[word]
        if tens:
            if value > 9:
                continue
            value += TENS_WORDS[tens]
        if value == day:
            return True
    return False


# День недели и «завтра» в речи — дата модели выведена из них, а не выдумана
# («сдай в пятницу» на встрече в понедельник 26.10 → «до 30.10»; Sonnet I3 out1).
# Засчитываются только ближайшие две недели от встречи: дальше «пятница» уже не
# указывает на одну дату.
_WEEKDAYS = (
    r"понедельник\w*|monday|周一|星期一", r"вторник\w*|tuesday|周二|星期二", r"сред[аеуы]\b|wednesday|周三|星期三",
    r"четверг\w*|thursday|周四|星期四", r"пятниц\w*|friday|周五|星期五", r"суббот\w*|saturday|周六|星期六",
    r"воскресень\w*|sunday|周日|周天|星期日|星期天")
_RELATIVE = {0: r"сегодня|today|今天", 1: r"(?<!после)завтра|tomorrow|明天", 2: r"послезавтра|后天"}


def _relative_heard(date: str, low: str, meeting: datetime.date) -> bool:
    """Дата ДД.ММ — названный в речи день недели или «сегодня/завтра/послезавтра»
    от дня встречи."""
    d, m = (int(x) for x in date.split("."))
    for year in (meeting.year, meeting.year + 1):
        try:
            cand = datetime.date(year, m, d)
        except ValueError:
            continue
        ahead = (cand - meeting).days
        if 0 <= ahead <= 13:
            if ahead in _RELATIVE and re.search(_RELATIVE[ahead], low):
                return True
            return bool(re.search(_WEEKDAYS[cand.weekday()], low))
    return False


def _date_heard(date: str, src_dates: set[str], low: str, meeting: datetime.date | None) -> bool:
    """Дата ДД.ММ подтверждена речью: прозвучала целиком; при известном дне
    встречи — ещё прозвучал её день (как день, `_day_spoken`) и она в окне
    `due_window`, либо она — названный день недели или «завтра»
    (`_relative_heard`). Без дня встречи — только целиком, как до №805."""
    if date in src_dates:
        return True
    if meeting is None:
        return False
    day = int(date.split(".")[0])
    return (date in due_window(day, meeting) and _day_spoken(day, low)) or _relative_heard(date, low, meeting)


def unanchored(document: str, transcript: str, *, day: datetime.date | None = None) -> list[str]:
    """Факты документа, которых нет в стенограмме. Пустой список — всё сошлось.
    `day` — день встречи: с ним дата документа засчитывается и по дню без месяца
    в окне встречи (`_date_heard`)."""
    if not document.strip() or not transcript.strip():
        return []
    bad = []
    src_tasks, src_dates = _tasks(transcript), _dates(transcript)
    low = transcript.lower()
    for name, num in TASK_RE.findall(document):
        if _is_year(num):
            continue
        key = f"{name.lower()} {num}"
        # голый номер в стенограмме тоже засчитываем: STT часто теряет имя системы
        if key not in src_tasks and not re.search(rf"\b{num}\b", transcript):
            bad.append(f"{name} {num}")
    for date in sorted(_dates(document) - src_dates):
        if not _date_heard(date, src_dates, low, day):
            bad.append(date)
    return sorted(set(bad), key=str.lower)


# ---- срок поручения (№805) -------------------------------------------------------
# Хвост поручения — срок, только если в нём дата, число или слово времени из
# закрытого списка. Предлог сам срока не делает: «— на английском», «— по итогам
# аудита» — продолжение дела, и пометка стёрла бы его (№805 out1: DS I2, Sonnet I2).
_DUE_TIME = re.compile(
    r"\b(?:понедельник\w*|вторник\w*|сред[аеуы]|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*|выходн\w*"
    r"|недел\w*|месяц\w*|квартал\w*|полугоди\w*|год|года|году|конц[аеу]|начал[аеу]|середин\w*"
    r"|сегодня|завтра\w*|послезавтра|утр[аоу]|утром|вечер\w*|обед\w*|сразу|срочно|дедлайн\w*"
    r"|январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*"
    r"|ноябр\w*|декабр\w*"
    r"|monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend|week|weeks|month|months|quarter"
    r"|year|today|tomorrow|tonight|asap|eod|eow|noon|morning|evening|deadline"
    r"|january|february|april|june|july|august|september|october|november|december)\b")
_DUE_ZH = re.compile(r"(之前|以前|前)$|今天|明天|后天|周|星期|下个?月|月底|日")
# дата цифрами по-китайски («10月30日») — `_dates` её не разбирает, а речь
# скажет её словами («十月三十号»): подтвердить нечем, стирать нельзя (DS M5 out1)
_DUE_ZH_DATE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]")
# «срок: не указан» и «срок не указан» — не утверждение о сроке, а его отсутствие
_DUE_PREFIX = re.compile(r"^(?:срок|deadline|期限)\s*[:：]\s*")
_DUE_EMPTY = ("срок не указан", "срок не назван", "срок не определ", "срок не обознач", "без срока", "в процессе",
              "не указан", "не назван", "no deadline", "tbd", "n/a")
# числа словами — STT пишет «через две недели», модель — «через 2 недели» (Sonnet I3 out1)
_CARDINALS = {
    1: "один одна одну одного одной one", 2: "два две двух двум two", 3: "три трёх трех трём трем three",
    4: "четыре четырёх четырех four", 5: "пять пяти five", 6: "шесть шести six", 7: "семь семи seven",
    8: "восемь восьми eight", 9: "девять девяти nine", 10: "десять десяти ten", 11: "одиннадцать одиннадцати eleven",
    12: "двенадцать двенадцати twelve", 13: "тринадцать тринадцати", 14: "четырнадцать четырнадцати",
    15: "пятнадцать пятнадцати fifteen", 16: "шестнадцать шестнадцати", 17: "семнадцать семнадцати",
    18: "восемнадцать восемнадцати", 19: "девятнадцать девятнадцати", 20: "двадцать двадцати twenty",
    30: "тридцать тридцати thirty"}


def _number_heard(num: int, low: str) -> bool:
    if re.search(rf"(?<!\d){num}(?!\d)", low):
        return True
    words = _CARDINALS.get(num)
    return bool(words) and bool(re.search(rf"\b(?:{'|'.join(words.split())})\b", low))


def looks_like_due(tail: str) -> bool:
    """Хвост поручения — утверждение о сроке: дата, число или слово времени
    (`_DUE_TIME`), а не пустой срок и не продолжение дела."""
    low = _DUE_PREFIX.sub("", tail.lower().strip())
    if not low or low.startswith(_DUE_EMPTY):
        return False
    if _DUE_ZH.search(low):
        return True
    return bool(_dates(tail)) or bool(re.search(r"\d", low)) or bool(_DUE_TIME.search(low))


def due_heard(tail: str, speech: str, *, day: datetime.date | None = None) -> bool:
    """Срок поручения подтверждён речью. Есть дата — решают только даты
    (`_date_heard`): подтверждённая дата не стирается из-за слова модели рядом
    («до 28.10 включительно» — DS C1 out1). Даты нет — каждое число хвоста
    («к 16:00», «через 2 недели») прозвучало цифрой или словом, каждое слово
    времени («к пятнице») — основой; прочие слова не судятся. Китайская фраза —
    сама, без «前»; китайская дата цифрами — не судится. Не похожий на срок
    хвост (`looks_like_due`) сюда не приходит."""
    low = speech.lower()
    src_dates = _dates(speech)
    dates = _dates(tail)
    if dates:
        return all(_date_heard(d, src_dates, low, day) for d in dates)
    if _DUE_ZH_DATE.search(tail):
        return True
    for run in re.findall(r"[\u3400-\u9fff]+", tail):
        run = re.sub(r"(之前|以前|前)$", "", run)
        if run and run not in speech:
            return False
    rest = _DUE_PREFIX.sub("", tail.lower())
    for num in re.findall(r"\d+", rest):
        if int(num) and not _number_heard(int(num), low):
            return False
    for word in _DUE_TIME.findall(rest):
        if word[:max(4, len(word) - 2)] not in low:
            return False
    return True


#: начало строки сноски; её снимает `without_footnote` и пропускает разбор
#: приложения (`MeetingMinutes.tailMarks`) — писатель один, этот модуль
FOOTNOTE_MARK = "⚠️ Нет в стенограмме, проверьте:"
_FOOTNOTE_SEP = "\n\n---\n"


def without_footnote(document: str) -> str:
    """Документ без сносок `annotate`: снимается строка сноски и её `---`,
    всё прочее остаётся на месте. Резать «от сноски до конца» нельзя: после
    неё дописывают другие писатели (`review_bridge` ставит «Поручения» в
    конец), и извлечение теряло их молча (№732, выход DS C1). Снимаются все
    сноски, не только последняя: прежний писатель оставлял две (DS I3)."""
    if FOOTNOTE_MARK not in document:
        return document
    out: list[str] = []
    for line in document.split("\n"):
        if line.startswith(FOOTNOTE_MARK):
            if out and out[-1] == "---":
                out.pop()
                if out and out[-1] == "":
                    out.pop()
            continue
        out.append(line)
    return "\n".join(out)


DATE_FULL = re.compile(r"\d{2}\.\d{2}")


def _footnote_facts(document: str) -> list[str]:
    """Факты всех сносок `annotate` в документе — как она их написала; снимает
    их `without_footnote` тоже все (DS M2 out1)."""
    return [x.strip() for line in document.split("\n") if line.startswith(FOOTNOTE_MARK)
            for x in line[len(FOOTNOTE_MARK):].split(",") if x.strip()]


def annotate(document: str, transcript: str, *, day: datetime.date | None = None) -> str:
    """Дописывает сноску о ненайденных фактах. Тело документа не трогает;
    прежнюю сноску снимает и пишет новую. `day` — день встречи (`unanchored`).

    Прежняя сноска снимается до сверки: её факты по построению не в
    стенограмме, и второй вызов дописывал второй блок (№732, Sonnet I2).

    Даты прежней сноски, которых речь по-прежнему не подтверждает, переходят в
    новую: гейт срока (№805) заменил такую дату в строке пометкой, и в теле её
    больше нет — без переноса повторный проход по готовому документу (пересборка
    поверх черновика) молча терял дату модели и менял байты. Цена: дату,
    которую человек сам стёр из тела, сноска держит до новой генерации минуток;
    сегодня все четыре писателя зовут `finish_minutes` по свежему ответу модели,
    и этот перенос держит только повторный проход (DS M1 out1)."""
    carried = [d for d in _footnote_facts(document)
               if DATE_FULL.fullmatch(d) and not _date_heard(d, _dates(transcript), transcript.lower(), day)]
    document = without_footnote(document)
    bad = sorted(set(unanchored(document, transcript, day=day)) | set(carried), key=str.lower)
    if not bad:
        return document
    return document.rstrip() + _FOOTNOTE_SEP + FOOTNOTE_MARK + " " + ", ".join(bad) + "\n"
