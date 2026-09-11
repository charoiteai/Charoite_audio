"""Мост L4 → минутки: поручения, восстановленные ревизией, — чекбоксами.

Облачная ревизия (scripts/cloud_review.py, уровень 4) сверяет минутки со
стенограммой и с 06.09 отдаёт пропущенные поручения под строгим
заголовком «## Восстановленные поручения» (graph_updater.cloud_enrich_prompt).
Пока они жили только в тексте ревизии, вкладка «Задачи» их не видела: она
читает Минутки.md архива, а тот копируется из `<стем>_minutes.md`.

Мост дописывает недостающие пункты в раздел поручений минуток ДО
повторной раскладки архива (cloud_review.deliver_review → archive_meeting)
с пометкой «(из ревизии)». Остальной текст минуток не трогается; пункт,
который уже есть (тот же исполнитель и то же дело с точностью до регистра
и пунктуации), не дублируется; исполнитель не из участников получает ту же
пометку ⚠, что и в минутках (action_items.flag_outsiders). Раздела
поручений нет — он создаётся в конце файла.
"""
from __future__ import annotations

import pathlib
import re

import action_items
import safe_write

# «## Восстановленные поручения», «**Восстановленные поручения:**»,
# «Восстановленные поручения:» — модель просят строгую форму, но жирный и
# голый варианты она пишет тоже (GLM r1 M1 по #518)
RECOVERED_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*восстановленные поручения\s*(?:\*\*)?\s*[:：.]?\s*(?:\*\*)?\s*$",
                            re.IGNORECASE)
# строка, начинающаяся с этих слов (после # или **), — заголовок, а не
# упоминание вроде «восстановленных поручений нет» в прозе (DS r2 M5)
RECOVERED_WORD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*восстановленн\w* поручени", re.IGNORECASE | re.MULTILINE)
MARKS = {"ru": "(из ревизии)", "en": "(from the review)", "zh": "（来自审阅）"}
SECTION_TITLE = {"ru": "## Поручения", "en": "## Action items", "zh": "## 行动项"}
_HEADING = re.compile(r"^\s*#{1,6}\s")
_BOLD_HEADING = re.compile(r"^\s*\*\*[^*]+\*\*\s*$")
# «-**Иван**» без пробела — тоже пункт (GLM r2 M6), а вот «*» без пробела —
# начало жирного текста, не маркер
_BULLET = re.compile(r"^\s*(?:-\s*|[*+•–—⁃‣▪]\s+|\d+[.)]\s+)(?=\S)")
_CHECKBOX = re.compile(r"^\s*\[[ xX]\]\s*")
_ITEM = re.compile(r"^\s*(?:-\s*|[*+•–—⁃‣▪]\s+|\d+[.)]\s+)(?:\[[ xX]\]\s*)?(?P<text>\S.*)$")
_EMPTY_ITEM = re.compile(r"^(?:нет|none|无|[—–\-•⁃‣▪*\s]+)\.?$", re.IGNORECASE)
# «**Пётр** — позвонить» / «**Пётр**: позвонить» без маркера — узнаваемое
# поручение: свой пункт, а не хвост чужого и не потеря (GLM r4 I1, r5 I1 и
# критика 1); «**на стенд** до пятницы» и «**срок:** пятница» — перенос с
# жирного спана, его клеим
_OWN_ITEM = re.compile(r"^\s*\*\*[^*]+\*\*\s*[—–-]|^\s*\*\*[^*:：]+\*\*\s*[:：]")
# легаси-заголовок раздела: markdown-заголовок со слова «Поручения» либо
# голая/жирная строка из известного списка — форму «слово + что угодно +
# двоеточие» не угадываем, как и action_items (GLM r5, критика 2)
_SECTION_WORD = re.compile(
    r"^\s*(?:#{1,6}\s*(?:\*\*)?\s*(?:поручени|action item|行动项)"
    r"|(?:\*\*)?\s*(?:поручения|поручения и сроки|action items|行动项)\s*[:：]\s*\**\s*$)",
    re.IGNORECASE)
_PAREN_NOTE = re.compile(r"^\s*[(（][^)）]*[)）]\s*$")


# Классы строк внутри раздела ревизии. Один классификатор вместо цепочки
# условий в цикле: пять кругов по #518 двигали по одному крайнему случаю за
# раз, и каждый круг менял ветвление в теле цикла. Теперь у строки ровно один
# класс, таблица «строка → класс» лежит в тестах, а цикл только раскладывает
# (GLM r4 по #518, критика 1; №187).
LINE_HEAD = "head"            # заголовок «Восстановленные поручения» (повтор внутри — пропуск)
LINE_END = "end"              # граница раздела: следующий заголовок или известная секция
LINE_BLANK = "blank"          # пустая строка — не пункт и не граница
LINE_ITEM = "item"            # пункт с маркером: «- [ ] **Иван** — …»
LINE_OWN_ITEM = "own_item"    # «**Пётр** — …» без маркера — свой пункт, не хвост чужого
LINE_CONT = "continuation"    # перенос строки внутри предыдущего пункта
LINE_NOISE = "noise"          # «нет», «(срок не назван)», голый маркер, строка до первого пункта


def _item_text(line: str) -> str:
    """Текст пункта с маркером — без маркера и чекбокса; не пункт — пусто.
    У «- [ ]» без текста регэксп _ITEM отдаёт чекбокс как текст (хвост
    после него пуст, и группа чекбокса откатывается) — снимаем его отдельно,
    иначе в минутки уезжал пункт «[ ]» (DS r1 по #533, проверено прогоном).
    Одна функция на классификатор и цикл: LINE_ITEM ⟹ текст непуст —
    инвариант структурный, а не свойство регэкспов (критика GLM r1)."""
    m = _ITEM.match(line)
    if not m:
        return ""
    return _CHECKBOX.sub("", m.group("text"), count=1).strip()


def classify_line(line: str, had_items: bool) -> str:
    """Класс строки раздела ревизии. Порядок проверок — тот же, что раньше
    в цикле: граница раньше пустоты (жирная подпись без двоеточия — граница
    только когда пункты уже были), пункт с маркером раньше переноса."""
    if RECOVERED_HEAD.match(line):
        return LINE_HEAD
    if _section_end(line, had_items):
        return LINE_END
    if not line.strip():
        return LINE_BLANK
    if _BULLET.match(line):
        text = _item_text(line)
        if not text or _EMPTY_ITEM.match(text.strip("*").strip()):
            return LINE_NOISE                    # «- нет», «- [ ] нет», «- ---», «- [ ]»
        return LINE_ITEM
    # без маркера: поручение с жирного имени — свой пункт; иное непустое —
    # продолжение предыдущего; «нет», комментарий в скобках и всё до первого
    # пункта — шум (GLM r1 M2, DS r1 I2, DS r2 I2/M3, GLM r3 M3, r5 I1)
    if had_items and _OWN_ITEM.match(line):
        return LINE_OWN_ITEM
    bare = line.strip().strip("*").strip()
    if had_items and bare and not _EMPTY_ITEM.match(bare) and not _PAREN_NOTE.match(line):
        return LINE_CONT
    return LINE_NOISE


def recovered_items(review: str, dropped: list[str] | None = None) -> list[str]:
    """Пункты раздела «## Восстановленные поручения» ревизии без маркеров
    и чекбоксов; «нет» и пустые строки — не пункты; раздела нет — пусто.
    `dropped` — сюда, если передан, складываются непустые строки раздела,
    которые не стали ни пунктом, ни продолжением: по ним видно, что мост
    выбросил (GLM r4 по #518, критика 1)."""
    items: list[str] = []
    inside = False
    for line in (review or "").split("\n"):
        if not inside:
            inside = bool(RECOVERED_HEAD.match(line))
            continue
        kind = classify_line(line, had_items=bool(items))
        if kind == LINE_END:
            break
        if kind == LINE_ITEM:
            items.append(_item_text(line))
        elif kind == LINE_OWN_ITEM:
            items.append(line.strip())
        elif kind == LINE_CONT:
            items[-1] = (items[-1] + " " + line.strip()).strip()
        elif kind == LINE_NOISE and dropped is not None:
            dropped.append(line.strip())
    return items


def _section_end(line: str, had_items: bool) -> bool:
    """Конец раздела ревизии: markdown-заголовок, голая известная секция
    («Решения:») или жирная подпись — с двоеточием всегда, без двоеточия
    («**Что сделано в графе**») — когда пункты уже были: до первого пункта
    жирная строка может быть подписью самого раздела (GLM r3 I1 по #518 —
    иначе пункты следующего раздела ехали в минутки)."""
    if _HEADING.match(line):
        return True
    if _BOLD_HEADING.match(line):
        return had_items or line.strip().rstrip("*").rstrip().endswith((":", "："))
    return bool(action_items._BARE_HEADING.match(line) and action_items._KNOWN_BARE_SECTION.match(line))


def section_present(review: str) -> bool:
    """В ревизии есть слова о восстановленных поручениях: если пунктов при
    этом не извлечено, мосту есть о чём сказать в лог (GLM r1, критика 1)."""
    return bool(RECOVERED_WORD.search(review or ""))


def _key(item: str) -> str:
    """Ключ дедупа: без пометок, жирного, чекбокса, пунктуации и регистра."""
    text = re.sub(r"⚠[^:]*:", " ", item)           # «⚠ не участник (Имя):» целиком
    for mark in MARKS.values():
        text = text.replace(mark, " ")
    text = re.sub(r"^\s*(?:[-*+•–—]\s*)?(?:\[[ xX]\]\s*)?", "", text)
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


_ASSIGNEE = re.compile(r"^\s*(?:[-*+•–—]\s*)?(?:\[[ xX]\]\s*)?(?:⚠[^:]*:\s*)?\*\*(?P<name>[^*]+)\*\*\s*(?P<rest>.*)$")
# Порог высокий намеренно: лишний дубль в «Задачах» виден и снимается одним
# кликом, а съеденное поручение невидимо (DS r1 по #518, критика 2)
SIMILAR = 0.7


def _split(item: str) -> tuple[str, set[str]]:
    """(исполнитель, слова дела) — для нечёткого сравнения пунктов."""
    m = _ASSIGNEE.match(item)
    if not m:
        return "", set(_key(item).split())
    name = " ".join(re.findall(r"[^\W_]+", m.group("name").lower()))
    # предлоги и союзы («с», «и», «до») не считаются: иначе «согласовать
    # бюджет с финансами» и «… с юристами» сходились как одно (GLM r1 I2)
    return name, {w for w in _key(m.group("rest")).split() if len(w) > 2}


def _same_item(a: str, b: str) -> bool:
    """Один и тот же пункт: тот же ключ, либо тот же исполнитель и то же дело
    другими словами (пересечение значимых слов ≥ SIMILAR по Жаккару) —
    ревизия пересказывает поручение минуток, а не находит новое."""
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return True
    na, wa = _split(a)
    nb, wb = _split(b)
    if not na or na != nb or not wa or not wb:
        return False
    common = len(wa & wb)
    # ревизия сжимает: «собрать примеры вопросов» ⊂ «… для теста» — то же
    # дело (GLM r2, критика 1). Только в эту сторону: пункт минуток короче
    # ревизионного — у ревизии могло появиться новое дело (GLM r3, критика 1)
    if common >= 2 and common == len(wa):
        return True
    return common / len(wa | wb) >= SIMILAR


def _empty_line(line: str) -> bool:
    """«нет», «- нет», «- [ ] нет» — честно пустой раздел, не пункт
    (чекбокс срезается, GLM r1 I1 по #518)."""
    if not line.strip():
        return False                       # пустые строки считает хвост раздела
    bare = _CHECKBOX.sub("", line.strip().lstrip("-*•–— ").strip()).strip().strip("*").strip()
    return not bare or bool(_EMPTY_ITEM.match(bare))   # «—» съедается lstrip → пусто = маркер


def _section_bounds(lines: list[str]) -> tuple[int, int] | None:
    """(начало, конец) строк раздела поручений: конец — следующий заголовок
    или конец файла (граница — как у action_items)."""
    start = None
    for i, line in enumerate(lines):
        if action_items._SECTION.match(line):
            start = i
            break
    if start is None:
        # «## Поручения и сроки» прежних минуток — тот же раздел, а не повод
        # завести второй (DS r1 M3 по #518)
        for i, line in enumerate(lines):
            if _SECTION_WORD.match(line):
                start = i
                break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if ((action_items._OTHER_SECTION.match(line)
             or (action_items._BARE_HEADING.match(line) and action_items._KNOWN_BARE_SECTION.match(line)))
                and not action_items._BULLET.match(line)):
            end = j
            break
    return start, end


def merge_into_minutes(minutes: str, items: list[str], participants: set[str] | None = None,
                       lang: str = "ru") -> tuple[str, int]:
    """Минутки с дописанными пунктами и сколько дописано. Повторы не
    дописываются; «нет» в пустом разделе уступает место первому пункту."""
    lang = (lang or "ru").strip().lower()[:2]
    mark = MARKS.get(lang, MARKS["ru"])
    fresh: list[str] = []
    for item in items:
        if not _key(item):
            continue
        # внутри одной ревизии вложенность судим в обе стороны, и выживает
        # самая полная форма — порядок строк не решает ни счёт, ни текст
        # (GLM r4 M4, r5 M4)
        for i, other in enumerate(fresh):
            if _same_item(item, other):
                break
            if _same_item(other, item):
                fresh[i] = item
                break
        else:
            fresh.append(item)
    if not fresh:
        return minutes, 0
    lines = minutes.split("\n")
    bounds = _section_bounds(lines)
    if bounds is None:
        body = minutes.rstrip("\n")
        lines = (body.split("\n") if body else []) + ["", SECTION_TITLE.get(lang, SECTION_TITLE["ru"])]
        start, end = len(lines) - 1, len(lines)
    else:
        start, end = bounds
    existing = [line for line in lines[start + 1:end] if _key(line)]
    new_lines = [f"- [ ] {item} {mark}" for item in fresh
                 if not any(_same_item(item, line) for line in existing)]
    if not new_lines:
        return minutes, 0
    section = lines[start + 1:end]
    # «нет» — честный пустой раздел; с первым пунктом он перестаёт быть пустым
    section = [ln for ln in section if not _empty_line(ln)]
    # дописываем после последнего пункта, до пустых строк перед следующим разделом
    tail = 0
    while section and not section[-1].strip():
        section.pop()
        tail += 1
    merged = lines[:start + 1] + section + new_lines + [""] * tail + lines[end:]
    text = "\n".join(merged)
    if participants:
        text = action_items.flag_outsiders(text, participants, lang=lang)
    return text, len(new_lines)


def minutes_path(transcript: pathlib.Path) -> pathlib.Path:
    return transcript.with_name(transcript.stem + "_minutes.md")


def bridge(review: pathlib.Path, transcript: pathlib.Path, owner: str = "",
           lang: str = "ru", dropped: list[str] | None = None) -> int:
    """Дописать восстановленные поручения ревизии в минутки этой встречи.
    Возвращает число дописанных; нет ревизии, минуток или пунктов — 0.
    `dropped` — список для строк раздела, которые мост выбросил (см.
    recovered_items); вызывающий пишет их в свой лог."""
    try:
        text = review.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    items = recovered_items(text, dropped=dropped)
    if not items:
        return 0
    # Владелец — одним написанием ДО сверки с минутками: «**Игорю** — позвонить»
    # против «**Игорь** — позвонить» иначе не дубль, а второй пункт (№188)
    items = [action_items.canon_owner_item(item, owner) for item in items]
    minutes = minutes_path(transcript)
    if not minutes.is_file():
        return 0
    try:
        speech = transcript.read_text(encoding="utf-8", errors="replace")
    except OSError:
        speech = ""
    participants = action_items.participants_of(speech, owner) if speech else set()
    before = minutes.read_text(encoding="utf-8", errors="replace")
    after, added = merge_into_minutes(before, items, participants, lang=lang)
    if added:
        safe_write.write_text(minutes, after)
    return added
