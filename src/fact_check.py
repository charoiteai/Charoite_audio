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

MONTHS = {
    "янв": "01", "фев": "02", "мар": "03", "апр": "04", "ма": "05", "июн": "06",
    "июл": "07", "авг": "08", "сен": "09", "окт": "10", "ноя": "11", "дек": "12",
}

# «TASK 3318», «РБ-1234» — латиница и кириллица, дефис необязателен
TASK_RE = re.compile(r"\b([A-Za-zА-Яа-яЁё]{2,12})\s*[-–—]?\s*(\d{3,6})\b")
# «15.08», «15/08», «15-08». Хвост (?![./-]?\d) отсекает версии вида 1.5.2
DATE_NUM_RE = re.compile(r"\b(\d{1,2})([./-])(\d{1,2})(?![./-]?\d)\b")
# «версия 1.5», «v2.1» — номер релиза, а не дата
VERSION_CTX_RE = re.compile(r"(?:верси\w*|version|v\.?)\s*$", re.I)
# «23 августа», «5 сентября»
DATE_WORD_RE = re.compile(r"\b(\d{1,2})\s+([а-яё]{3,10})\b", re.I)


def _dates(text: str) -> set[str]:
    """Все даты текста в едином виде ДД.ММ — иначе «23 августа» и «23.08» не сойдутся."""
    out = set()
    for match in DATE_NUM_RE.finditer(text):
        day, _, mon = match.groups()
        if not (1 <= int(day) <= 31 and 1 <= int(mon) <= 12):
            continue
        if VERSION_CTX_RE.search(text[max(0, match.start() - 12):match.start()]):
            continue  # «версия 1.5»
        # 1.5 — это и 1 мая, и версия 1.5. Различаем по форме записи: у даты
        # месяц пишут двузначным (15.08) либо день заведомо не месяц (23.8).
        # Однозначный месяц при дне ≤ 12 (1.5, 2.3) считаем номером версии:
        # пропустить сомнительную дату дешевле, чем дёргать ложной тревогой.
        if len(mon) == 1 and int(day) <= 12:
            continue
        out.add(f"{int(day):02d}.{int(mon):02d}")
    for d, word in DATE_WORD_RE.findall(text.lower()):
        for pref, num in MONTHS.items():
            # «мая» и «марта» начинаются одинаково — сверяем самый длинный префикс
            if word.startswith(pref) and 1 <= int(d) <= 31:
                out.add(f"{int(d):02d}.{num}")
                break
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


def annotate(document: str, transcript: str) -> str:
    """Дописывает сноску о ненайденных фактах. Текст документа не трогает."""
    bad = unanchored(document, transcript)
    if not bad:
        return document
    return (document.rstrip() +
            "\n\n---\n⚠️ Нет в стенограмме, проверьте: " + ", ".join(bad) + "\n")
