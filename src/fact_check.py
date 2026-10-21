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
человек решает сам.
"""
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
# «15.08», «15/08», «15-08», «04.10.2026», «04.10.2026г.». Голова (?<![\d./-]) и
# хвост (?![./-]?\d) отсекают куски цепочки: версии 1.5.2 и 1.15.2, хвост ISO;
# год — только четыре цифры, иначе «1.5.20» было бы датой
DATE_NUM_RE = re.compile(
    r"(?<![\d./-])(\d{1,2})([./-])(\d{1,2})(?:[./-]((?:19|20)\d{2}))?(?![./-]?\d)")
# «12.10-15.10», «12.10.2026 – 15.10.2026»: дефис между двумя датами — диапазон,
# а не кусок цепочки; голова и хвост DATE_NUM_RE его бы отрезали с обеих сторон
_NUM = r"(\d{1,2})[./-](\d{1,2})(?:[./-]((?:19|20)\d{2}))?"
DATE_RANGE_RE = re.compile(rf"(?<![\d./-]){_NUM}\.?\s*[-–—]\s*{_NUM}(?![./-]?\d)")
# «2026-10-04» — ISO: год впереди, иначе DATE_NUM_RE прочёл бы «10-04» как 10.04
DATE_ISO_RE = re.compile(r"(?<![\d./-])(?:19|20)\d{2}-(\d{2})-(\d{2})(?![./-]?\d)")
# «версия 1.5», «v2.1» — номер релиза, а не дата
VERSION_CTX_RE = re.compile(r"(?:верси\w*|version|v\.?)\s*$", re.I)
# «23 августа», «4-го октября», «4го октября»: число с окончанием или без;
# число и месяц — на одной строке («в списке 3» и «мая…» ниже — не дата);
# пробел любой, кроме перевода строки: неразрывный (23\u00a0августа) — тоже
DATE_WORD_RE = re.compile(
    rf"\b(\d{{1,2}})(?:-?(?:го|ого|е|ое))?[^\S\n]+({_alt(MONTHS)})\b")
# «четвёртого октября», «двадцать пятого октября»: десяток и единица — только
# соседние слова без знака препинания («было двадцать, пятого октября» — 05.10)
DATE_ORDINAL_RE = re.compile(
    rf"\b(?:({_alt(TENS_WORDS)})[^\S\n]+)?({_alt(ORDINAL_DAYS)})[^\S\n]+({_alt(MONTHS)})\b")


def _add(out: set, day: int, month: str) -> None:
    if 1 <= day <= 31:
        out.add(f"{day:02d}.{month}")


def _add_numeric(out: set, text: str, start: int, day: str, mon: str, year: str | None) -> None:
    """Числовая дата «день.месяц[.год]», начатая в `text` с `start`, — в `out`, если это дата."""
    if not (1 <= int(day) <= 31 and 1 <= int(mon) <= 12):
        return
    if VERSION_CTX_RE.search(text[max(0, start - 12):start]):
        return  # «версия 1.5»
    # 1.5 — это и 1 мая, и версия 1.5. Различаем по форме записи: у даты
    # месяц пишут двузначным (15.08) либо день заведомо не месяц (23.8).
    # Однозначный месяц при дне ≤ 12 (1.5, 2.3) считаем номером версии:
    # пропустить сомнительную дату дешевле, чем дёргать ложной тревогой.
    # С годом (1.5.2026) сомнения нет.
    if len(mon) == 1 and int(day) <= 12 and not year:
        return
    out.add(f"{int(day):02d}.{int(mon):02d}")


def _dates(text: str) -> set[str]:
    """Все даты текста в едином виде ДД.ММ — иначе «23 августа» и «23.08» не сойдутся.

    Один экстрактор, одна таблица месяцев. Не даты: «четвёртого числа» (без
    месяца — граница, которую не закрываем), версии «1.5.2», день больше 31."""
    out: set[str] = set()
    for match in DATE_NUM_RE.finditer(text):
        day, _, mon, year = match.groups()
        _add_numeric(out, text, match.start(), day, mon, year)
    for match in DATE_RANGE_RE.finditer(text):
        d1, m1, y1, d2, m2, y2 = match.groups()
        _add_numeric(out, text, match.start(), d1, m1, y1)
        _add_numeric(out, text, match.start(), d2, m2, y2)
    for mon, day in DATE_ISO_RE.findall(text):
        if 1 <= int(mon) <= 12:
            _add(out, int(day), mon)
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


def unanchored(document: str, transcript: str) -> list[str]:
    """Факты документа, которых нет в стенограмме. Пустой список — всё сошлось."""
    if not document.strip() or not transcript.strip():
        return []
    bad = []
    src_tasks, src_dates = _tasks(transcript), _dates(transcript)
    for name, num in TASK_RE.findall(document):
        if _is_year(num):
            continue
        key = f"{name.lower()} {num}"
        # голый номер в стенограмме тоже засчитываем: STT часто теряет имя системы
        if key not in src_tasks and not re.search(rf"\b{num}\b", transcript):
            bad.append(f"{name} {num}")
    for date in sorted(_dates(document) - src_dates):
        bad.append(date)
    return sorted(set(bad), key=str.lower)


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


def annotate(document: str, transcript: str) -> str:
    """Дописывает сноску о ненайденных фактах. Тело документа не трогает;
    прежнюю сноску снимает и пишет новую.

    Прежняя сноска снимается до сверки: её факты по построению не в
    стенограмме, и второй вызов дописывал второй блок (№732, Sonnet I2)."""
    document = without_footnote(document)
    bad = unanchored(document, transcript)
    if not bad:
        return document
    return document.rstrip() + _FOOTNOTE_SEP + FOOTNOTE_MARK + " " + ", ".join(bad) + "\n"
