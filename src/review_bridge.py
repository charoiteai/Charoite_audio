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
import task_line

# «## Восстановленные поручения», «**Восстановленные поручения:**»,
# «Восстановленные поручения:» — модель просят строгую форму, но жирный и
# голый варианты она пишет тоже (GLM r1 M1 по #518)
RECOVERED_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*восстановленные поручения\s*(?:\*\*)?\s*[:：.]?\s*(?:\*\*)?\s*$",
                            re.IGNORECASE)
# строка, начинающаяся с этих слов (после # или **), — заголовок, а не
# упоминание вроде «восстановленных поручений нет» в прозе (DS r2 M5)
RECOVERED_WORD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*восстановленн\w* поручени", re.IGNORECASE | re.MULTILINE)
# Обратная сторона моста (№238): ревизия так же строго отдаёт поручения,
# которых в записи НЕ было (исполнителя на встрече нет, срок не звучал,
# шутка, пересказ с ошибкой), — под «## Снятые поручения». Мост переносит
# такие пункты из раздела поручений в «## Снято ревизией» с причиной: они
# исчезают из вкладки «Задачи» (она читает только раздел поручений), но не
# из минуток — след виден, и человек может вернуть строку руками.
WITHDRAWN_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*снятые поручения\s*(?:\*\*)?\s*[:：.]?\s*(?:\*\*)?\s*$",
                            re.IGNORECASE)
WITHDRAWN_WORD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*снят\w* поручени", re.IGNORECASE | re.MULTILINE)
# «- **Имя** — что сделать — причина: Светы на встрече нет» → пункт и причина
_REASON = re.compile(r"\s+[—–-]\s*(?:причина|reason|原因)\s*[:：]\s*(?P<why>.+?)\s*$", re.IGNORECASE)
MARKS = {"ru": "(из ревизии)", "en": "(from the review)", "zh": "（来自审阅）"}
WITHDRAWN_MARKS = {"ru": "снято ревизией", "en": "withdrawn by the review", "zh": "已由审阅撤回"}
SECTION_TITLE = {"ru": "## Поручения", "en": "## Action items", "zh": "## 行动项"}
WITHDRAWN_TITLE = {"ru": "## Снято ревизией", "en": "## Withdrawn by the review", "zh": "## 审阅撤回"}
_WITHDRAWN_TITLE_RE = re.compile(r"^\s*#{1,6}\s*(?:снято ревизией|withdrawn by the review|审阅撤回)\s*$", re.IGNORECASE)
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
_PAREN_NOTE = re.compile(r"^\s*[(（][^)）]*[)）]\s*$")


# Гейт потери обновления с повтором и его сигнал живут в safe_write (один на
# всех писателей); здесь — те же имена для cloud_review, name_fixes и тестов.
LostRace = safe_write.LostRace
rewrite_file = safe_write.rewrite_file

# Ревизию (ответ облака) читаем СТРОГО, при отказе — с заменой (`read_review`):
# обрыв ответа не должен глушить мост целиком. Но то, что из неё ВЗЯТО и уедет
# в минутки, проверяется по СОДЕРЖИМОМУ в обоих случаях: «�» в пункте остался
# бы в графе навсегда, а целый файл ревизии ничего не доказывает — модель
# цитирует наши же минутки, и символ приезжает оттуда (№263, DS r3 Critical 1).
# Проверка одна на восстановленные и снятые пункты.
MANGLED = "�"


class MangledFile(RuntimeError):
    """Файл, который мост должен переписать, не в UTF-8 — запись не сделана.

    Свой сигнал, а не `return 0`: ноль вызывающий печатает как «пунктов не
    извлечено или все уже в минутках», хотя пункты извлечены и отказала
    ЗАПИСЬ. Ровно от этой лжи в #553 завели LostRace — здесь тот же случай
    (DS r1 I1 и GLM r1 I2 по №263)."""

    PREFIX = "запись не состоялась: "

    def __init__(self, path: pathlib.Path, what: str, reason: str):
        self.path = path
        super().__init__(f"{self.PREFIX}{path.name} не в UTF-8 ({reason}) — {what}")


def read_review(review: pathlib.Path) -> tuple[str, bool]:
    """Текст ревизии и признак «читали с заменой».

    Сначала СТРОГО: у целого ответа облака «�» — это авторский символ, а не
    потеря, и фильтровать по нему пункты нельзя (luna, критика решения по
    №263) — флаг нужен только для формулировки в логе, фильтр по «�» работает
    в обоих случаях. Не декодируется — заменяем: обрыв ответа не должен глушить
    мост целиком. Байты читаются ОДИН раз: два захода к файлу давали окно, в
    котором флаг и текст описывали разные версии, а OSError на втором заходе
    притворялся «ревизии нет» (DS r3 I3). Файла нет — пусто и False."""
    try:
        data = review.read_bytes()
    except OSError:
        return "", False
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace"), True


def _drop_mangled(items: list, dropped: list[str] | None, what: str, lossy: bool = True) -> list:
    """Пункты без нечитаемых символов; выброшенное — строкой в `dropped`.

    Элемент — строка (восстановленные) или пара (снятые: пункт, причина).
    ПРИЧИНА проверяется наравне с пунктом: `withdraw_from_minutes` пишет её в
    строку минуток («~~пункт~~ _(снято ревизией: причина)_»), и битый байт в
    хвосте «— причина: …» уезжал бы в граф тем же путём (DS r1 Critical по
    №263). Но целое поручение из-за испорченного хвоста не теряем: пункт
    остаётся, причина обнуляется, строка об этом — в `dropped`.

    Фильтр работает по СОДЕРЖИМОМУ и не зависит от того, как читался файл:
    целый UTF-8 ничего не говорит о том, авторский ли это символ. Модель
    получает в промпт наши же минутки и стенограмму, и «�», уже лежащий там,
    она процитирует — ревизия останется валидной, а порча расползётся дальше
    (DS r3 Critical 1). `lossy` меняет только формулировку в `dropped`.
    """
    why_lost = "ревизию читали с заменой" if lossy else "нечитаемый символ в целом файле"
    ok = []
    for item in items:
        text, why = (item, "") if isinstance(item, str) else (item[0], item[1])
        if MANGLED in text:
            if dropped is not None:
                dropped.append(f"{what}: {why_lost}, в минутки не дописан: {text[:60]}")
            continue
        if MANGLED in why:
            if dropped is not None:
                dropped.append(f"{what}: причина — {why_lost}, снимаем без причины: {text[:60]}")
            ok.append((text, ""))
            continue
        ok.append(item)
    return ok


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


def classify_line(line: str, had_items: bool, head: re.Pattern[str] = RECOVERED_HEAD) -> str:
    """Класс строки раздела ревизии. Порядок проверок — тот же, что раньше
    в цикле: граница раньше пустоты (жирная подпись без двоеточия — граница
    только когда пункты уже были), пункт с маркером раньше переноса.
    `head` — заголовок разбираемого раздела: восстановленные или снятые
    поручения, один классификатор на оба (№238)."""
    if head.match(line):
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
    return _section_items(review, RECOVERED_HEAD, dropped)


def _section_items(review: str, head: re.Pattern[str], dropped: list[str] | None) -> list[str]:
    """Пункты одного строгого раздела ревизии (см. recovered_items)."""
    items: list[str] = []
    inside = False
    for line in (review or "").split("\n"):
        if not inside:
            inside = bool(head.match(line))
            continue
        kind = classify_line(line, had_items=bool(items), head=head)
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


def withdrawn_items(review: str, dropped: list[str] | None = None) -> list[tuple[str, str]]:
    """Пункты раздела «## Снятые поручения» — (пункт как в минутках, причина).
    Причина — хвост «— причина: …»; без него пункт целиком, причина пустая.
    Раздела нет — пусто (№238)."""
    out: list[tuple[str, str]] = []
    for item in _section_items(review, WITHDRAWN_HEAD, dropped):
        m = _REASON.search(item)
        if m:
            out.append((item[:m.start()].strip(), m.group("why").strip()))
        else:
            out.append((item, ""))
    return out


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


def withdrawn_section_present(review: str) -> bool:
    """То же для снятых: раздел свободной формы («**Что снять:**», «## Снятые
    поручения (проверка)») не разбирается — по логу это должно отличаться
    от «снимать нечего» (DS r1 M1, GLM r1 M4 по #545)."""
    return bool(WITHDRAWN_WORD.search(review or ""))


def _key(item: str) -> str:
    """Ключ дедупа: без пометок (ревизии, «не участник», контроля задач), жирного,
    чекбокса, пунктуации и регистра."""
    text = re.sub(r"⚠[^:]*:", " ", item)           # «⚠ не участник (Имя):» целиком
    text = task_line.CONTROL_MARK.sub(" ", text)     # «_(снято по сроку 24.09)_» (№366)
    for mark in MARKS.values():
        text = text.replace(mark, " ")
    text = re.sub(r"^\s*(?:" + task_line.MARKER + r"\s*)?(?:" + task_line.BOX + r"\s*)?", "", text)
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


# исполнитель строки минуток в любом статусе, снятое «[-]» — тоже (№366): иначе пересказ
# снятого поручения ревизией не узнавался и дописывался заново открытым
# Маркер — общий с грамматикой статуса: свой узкий набор не узнавал «1. [x] **Коля** — …»,
# и снятие уходило соседнему открытому пункту, а merge дописывал снятое открытым (Opus I2
# круга 2 по коду)
_ASSIGNEE = re.compile(r"^\s*(?:" + task_line.MARKER + r"\s*)?(?:" + task_line.BOX + r"\s*)?(?:⚠[^:]*:\s*)?\*\*(?P<name>[^*]+)\*\*\s*(?P<rest>.*)$")
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


def _same_assignee(na: str, nb: str) -> bool:
    """Один исполнитель в разных падежах: «сергею» и «сергей», «иван орлов» и
    «ивану орлову» — пословно через action_items.same_case_form, БЕЗ таблицы
    уменьшительных: «Вере» и «Веронике» — разные люди, и склейка съела бы
    поручение (DS Critical по #553). Владелец приводится к канону раньше
    (_dedup_view); остальные участники до 13.09 сравнивались строкой, и
    «**Сергею** — позвонить» дописывался вторым пунктом рядом с «**Сергей**
    — позвонить» (аудит зон 12.09, зона 4). Разное число слов — разные
    записи: дубль «Иван Петрович»/«Иван» дешевле съеденного пункта."""
    if not na or not nb:
        return False
    if na == nb:
        return True
    pa, pb = na.split(), nb.split()
    return len(pa) == len(pb) and all(action_items.same_case_form(x, y) for x, y in zip(pa, pb))


def _same_item(a: str, b: str) -> bool:
    """Один и тот же пункт: тот же ключ, либо тот же исполнитель (в любом
    падеже) и то же дело другими словами (пересечение значимых слов ≥ SIMILAR
    по Жаккару) — ревизия пересказывает поручение минуток, а не находит новое."""
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return True
    na, wa = _split(a)
    nb, wb = _split(b)
    if not _same_assignee(na, nb) or not wa or not wb:
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
    # Предикат общий с action_items (normalize, canon_owner, flag_outsiders):
    # «## Поручения и сроки» прежних минуток — тот же раздел, а не повод
    # завести второй (DS r1 M3 по #518), и пометка «не участник» обязана
    # видеть его так же, как мост (аудит зон 12.09, зона 4). Но приоритет
    # прежний: сначала текущий заголовок по всему файлу, легаси — только
    # если текущего нет; иначе минутки с обоими получали пункты в старый
    # блок (GLM M3 / DS I4 по #553)
    start = next((i for i, line in enumerate(lines) if action_items._SECTION.match(line)), None)
    if start is None:
        start = next((i for i, line in enumerate(lines) if action_items.is_section_heading(line)), None)
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


def _section_spans(lines: list[str]) -> list[tuple[int, int]]:
    """(начало, конец) КАЖДОГО раздела поручений в файле — текущего и легаси
    заголовков; граница раздела та же, что у _section_bounds."""
    spans: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if not action_items.is_section_heading(line):
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if ((action_items._OTHER_SECTION.match(nxt)
                 or (action_items._BARE_HEADING.match(nxt) and action_items._KNOWN_BARE_SECTION.match(nxt)))
                    and not action_items._BULLET.match(nxt)):
                end = j
                break
        spans.append((i, end))
    return spans


def _dedup_view(line: str, owner: str) -> str:
    """Строка минуток ДЛЯ СРАВНЕНИЯ с пунктом ревизии: владелец в том же
    каноне, что у ревизии (canon_owner_item), а полное user_name жирным —
    тоже первым словом: «**Иван Орлов** — …», «**Ивану** — …» (минутки до
    канонизации) и «**Иван** — …» — один исполнитель, а не три (Important
    DS/GLM, круг 1 по #536). Файл этим не переписывается."""
    if not (owner or "").strip():
        return line
    line = action_items.canon_owner_item(line, owner)
    words = owner.split()
    m = _ASSIGNEE.match(line)
    # полное имя сравниваем без регистра и ё: «**Петр Иванов**» в минутках при
    # user_name «Пётр Иванов» — тот же владелец (Important GLM r2 по #536)
    if len(words) > 1 and m and _plain(m.group("name")) == _plain(" ".join(words)):
        line = line[:m.start("name")] + words[0] + line[m.end("name"):]
    return line


def _plain(text: str) -> str:
    """Полное имя для сравнения: свёртка пробелов + та же нормализация регистра
    и ё/е, что у сверки участников (action_items._norm), — одна на всех."""
    return action_items._norm(" ".join(text.split()))


def merge_into_minutes(minutes: str, items: list[str], participants: set[str] | None = None,
                       lang: str = "ru", owner: str = "") -> tuple[str, int]:
    """Минутки с дописанными пунктами и сколько дописано. Повторы не
    дописываются; «нет» в пустом разделе уступает место первому пункту.
    `owner` — user_name: существующие пункты сравниваются с ревизионными
    в одном каноне владельца (_dedup_view), сам текст минуток не меняется."""
    lang = (lang or "ru").strip().lower()[:2]
    mark = MARKS.get(lang, MARKS["ru"])
    fresh: list[str] = []
    for item in items:
        if not _key(item):
            continue
        # внутри одной ревизии вложенность судим в обе стороны, и выживает
        # самая полная форма — порядок строк не решает ни счёт, ни текст
        # (GLM r4 M4, r5 M4); сравнение — в каноне владельца с обеих сторон,
        # как и с минутками (GLM r3 M2 по #545)
        view = _dedup_view(item, owner)
        for i, other in enumerate(fresh):
            other_view = _dedup_view(other, owner)
            if _same_item(view, other_view):
                break
            if _same_item(other_view, view):
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
    # дедуп — по ВСЕМ разделам поручений файла (текущий и легаси), запись — в
    # выбранный: минутки с «## Поручения и сроки» выше и «## Поручения» ниже
    # иначе получали дубль пункта из старого блока (GLM M1, круг 2 по #553)
    # Строки с поставленным статусом (выполнено, снято, возвращено) в дедупе участвуют
    # намеренно: пункт ревизии, совпавший со снятым, — тот же пункт, и дописать его
    # открытым значило бы отменить снятие (№366; Sonnet, критика 2 круга 1 по коду).
    existing = [_dedup_view(line, owner)
                for s_, e_ in (_section_spans(lines) or [(start, end)])
                for line in lines[s_ + 1:e_] if _key(line)]
    # сравнение — в одном каноне с обеих сторон (полное имя владельца из
    # двух слов → первое слово), в минутки пункт идёт как есть (DS r2 по #545)
    new_lines = [f"- [ ] {item} {mark}" for item in fresh
                 if not any(_same_item(_dedup_view(item, owner), line) for line in existing)]
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
           lang: str = "ru", dropped: list[str] | None = None,
           extra_participants: set[str] | None = None) -> int:
    """Дописать восстановленные поручения ревизии в минутки этой встречи.
    Возвращает число дописанных; нет ревизии, минуток или пунктов — 0.
    `dropped` — список для строк раздела, которые мост выбросил (см.
    recovered_items); вызывающий пишет их в свой лог. `extra_participants`
    — верные имена меток из «## Исправления имён» ревизии, когда файлы не
    перештампованы (без права правки графа): иначе пункт с верным именем
    получал бы «⚠ не участник» по старой шапке (DS r2 I2 по #548)."""
    text, lossy = read_review(review)
    items = _drop_mangled(recovered_items(text, dropped=dropped), dropped, "пункт ревизии", lossy)
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
    if extra_participants:
        participants = participants | action_items.participants_set(sorted(extra_participants), owner)
    # Снимок ДО чтения и две попытки (rewrite_file): минутки правят и
    # пересборка, и mcp «Минутки», и редактор — запись без гейта затирала бы
    # их версию своей (аудит зон 12.09, зона 4); проиграли дважды — LostRace.
    # Минутки читаются СТРОГО: их текст переписывается целиком, и замена
    # нечитаемого байта записала бы «�» в файл человека навсегда — как у
    # перештамповки имён (№263). Не в UTF-8 — MangledFile, а не тихий 0:
    # ноль вызывающий печатает как «пунктов не извлечено», хотя пункты
    # извлечены и отказала запись (DS r1 I1, GLM r1 I2).
    try:
        return rewrite_file(
            minutes, lambda before: merge_into_minutes(before, items, participants, lang=lang, owner=owner),
            f"поручения ({len(items)}) не дописаны")
    except UnicodeDecodeError as e:
        raise MangledFile(minutes, f"поручения ({len(items)}) не дописаны", e.reason) from e


def _continuation(line: str) -> bool:
    """Строка раздела поручений — перенос предыдущего пункта: непустая, без
    маркера, не свой пункт с жирного имени и не жирная подпись. Уезжает
    вместе с пунктом, иначе в «Поручениях» остаётся сирота без исполнителя,
    а в зачёркнутом тексте теряется хвост (DS r1 I2, GLM r1 M3 по #545)."""
    return bool(line.strip()) and not _BULLET.match(line) and not _OWN_ITEM.match(line) \
        and not _BOLD_HEADING.match(line)


def withdraw_from_minutes(minutes: str, items: list[tuple[str, str]], lang: str = "ru",
                          owner: str = "", dropped: list[str] | None = None) -> tuple[str, int]:
    """Минутки, из раздела поручений которых снятые ревизией пункты переехали
    в «## Снято ревизией» (сразу за разделом поручений) с причиной, и сколько
    переехало. Пункт ищется так же, как при дедупе восстановленных: тот же
    исполнитель и то же дело (в любую сторону вложения), владелец — в одном
    каноне. Один пункт ревизии снимает ровно одну строку минуток: подходит
    к нескольким («подготовить отчёт» против «…по бюджету» и «…по срокам»)
    — не гадаем, ничего не снимаем и говорим об этом в `dropped` (DS r1
    Critical по #545: съеденное поручение невидимо, лишнее — видно и снимается
    кликом). Пункт, чей статус поставил человек или контроль (выполнено,
    снято, возвращено — task_line.settled), узнаётся, но не снимается: это факт,
    а не пересказ модели (№366). Ничего не нашлось — минутки те же (№238)."""
    lang = (lang or "ru").strip().lower()[:2]
    if not items:
        return minutes, 0
    lines = minutes.split("\n")
    bounds = _section_bounds(lines)
    if bounds is None:
        return minutes, 0
    start, end = bounds
    body = lines[start + 1:end]
    # Сравниваем со всеми пунктами, включая поставленные, а отказываемся снимать уже
    # после: отсеянный до сравнения поставленный пункт отдавал своё снятие соседнему
    # открытому («подготовить отчёт» уезжало с «…по бюджету», Opus I2 круга 1 по коду).
    cand = [i for i, ln in enumerate(body) if _key(ln) and not _continuation(ln)]
    views = {i: _dedup_view(body[i], owner) for i in cand}
    taken: dict[int, str] = {}                      # строка минуток → причина снятия
    for item, why in items:
        hits = [i for i in cand if _matches_withdrawn(item, views[i])]
        if len(hits) > 1:
            # точное совпадение ключа перевешивает пересказ; два точных — гадание, кроме
            # одного случая: среди одинаковых строк ровно одна открыта. Поставленную снимать
            # нельзя, так что открытая — единственный допустимый ход, а не догадка (Sonnet I2
            # круга 2 по коду: такой дубль до правки снимался, после — нет)
            exact = [i for i in hits if _key(item) == _key(views[i])]
            if len(exact) > 1:
                open_exact = [i for i in exact if not task_line.settled(body[i])]
                exact = open_exact if len(open_exact) == 1 else exact
            hits = exact if len(exact) == 1 else hits
        if len(hits) == 1 and task_line.settled(body[hits[0]]):
            if dropped is not None:
                dropped.append(f"снятие «{item}» относится к пункту со статусом — оставлен")
        elif len(hits) == 1:
            taken.setdefault(hits[0], why)          # две причины на одну строку — первая
        elif hits and dropped is not None:
            dropped.append(f"снятие «{item}» подходит к {len(hits)} пунктам минуток — не гадаем, оставлены")
    if not taken:
        return minutes, 0
    mark = WITHDRAWN_MARKS.get(lang, WITHDRAWN_MARKS["ru"])
    moved: list[str] = []
    keep: list[str] = []
    i = 0
    while i < len(body):
        line = body[i]
        if i not in taken:
            keep.append(line)
            i += 1
            continue
        why = taken[i]
        m = _ITEM.match(line)
        text = _CHECKBOX.sub("", m.group("text") if m else line.strip(), count=1).strip()
        i += 1
        # переносы пункта — с ним; одна пустая строка перед ОТСТУПНЫМ переносом
        # хвост не обрывает (DS r2 M4), а абзац прозы после пустой строки —
        # не хвост, остаётся в разделе (DS r3 M5): как в markdown, где
        # продолжение пункта после пустой строки обязано быть с отступом
        while i < len(body):
            j = i
            if not body[j].strip():
                j += 1
                if j >= len(body) or not body[j].strip() or not body[j][:1].isspace() \
                        or not _continuation(body[j]):
                    break
            elif not _continuation(body[j]):
                break
            text = f"{text} {body[j].strip()}"
            i = j + 1
        moved.append(f"- ~~{text}~~ _({mark}{': ' + why if why else ''})_")
    section = keep
    while section and not section[-1].strip():
        section.pop()
    rest = lines[end:]
    while rest and not rest[0].strip():
        rest.pop(0)
    title = WITHDRAWN_TITLE.get(lang, WITHDRAWN_TITLE["ru"])
    # раздел уже есть (вторая ревизия, повтор обработки) — дописываем в него без дублей
    at = next((i for i, ln in enumerate(rest) if _WITHDRAWN_TITLE_RE.match(ln)), None)
    if at is not None:
        stop = at + 1
        while stop < len(rest) and not _HEADING.match(rest[stop]):
            stop += 1
        # ключи — только своего раздела: строка «Открытых вопросов» с тем же
        # текстом не должна глушить перенос (GLM r1 M6 по #545)
        old_keys = {_key(ln) for ln in rest[at + 1:stop] if _key(ln)}
        fresh = [m for m in moved if _key(m) not in old_keys]
        block = rest[at + 1:stop]
        while block and not block[-1].strip():
            block.pop()
        rest = rest[:at + 1] + block + fresh + [""] + rest[stop:]
        merged = lines[:start + 1] + section + [""] + rest
    else:
        merged = lines[:start + 1] + section + ["", title] + moved + ([""] if rest else []) + rest
    return "\n".join(merged), len(moved)


def _matches_withdrawn(item: str, view: str) -> bool:
    """Пункт минуток (view — в каноне владельца) — тот, что ревизия снимает:
    тот же ключ или тот же исполнитель и то же дело в любую сторону вложения;
    исполнитель в другом падеже («**Сергею**» против «**Сергей**») — тот же
    человек (_same_assignee: только падежные формы, без таблицы уменьшительных —
    «Слава» и «Ярослав» для снятия разные люди, ложный пункт тогда остаётся,
    это безопасное направление отказа), сам текст минуток этим не правится.
    Вложение — от двух общих значимых слов, как у _same_item: одно общее
    слово («позвонить») снимало бы любой пункт этого человека (DS r1
    Critical, GLM r1 I1 по #545); однословное дело с тем же словом
    проходит по Жаккару (1.0)."""
    if _same_item(item, view) or _same_item(view, item):
        return True
    na, wa = _split(item)
    nb, wb = _split(view)
    if not (wa and wb) or not _same_assignee(na, nb):
        return False
    common = len(wa & wb)
    if common >= 2 and (common == len(wa) or common == len(wb)):
        return True
    return common / len(wa | wb) >= SIMILAR


def withdraw(review: pathlib.Path, transcript: pathlib.Path, owner: str = "",
             lang: str = "ru", dropped: list[str] | None = None) -> int:
    """Перенести снятые ревизией поручения из задач минуток в «Снято ревизией».
    Возвращает число перенесённых; нет ревизии, минуток или пунктов — 0."""
    text, lossy = read_review(review)
    items = _drop_mangled(withdrawn_items(text, dropped=dropped), dropped, "снятый пункт ревизии", lossy)
    if not items:
        return 0
    # Пункт ревизии — в том же каноне, что строки минуток (_dedup_view: канон
    # владельца И полное имя из двух слов → первое слово): иначе у владельца
    # «Иван Орлов» ключи расходились на фамилию, и снятие не срабатывало
    # никогда (DS r2 Critical, GLM r2 I1 по #545)
    items = [(_dedup_view(item, owner), why) for item, why in items]
    minutes = minutes_path(transcript)
    if not minutes.is_file():
        return 0
    # гейт потери обновления и повтор — как у bridge; строки «подходит к N
    # пунктам» собираются с последней попытки, чтобы повтор их не удваивал
    tries: list[list[str]] = []

    def transform(before: str) -> tuple[str, int]:
        tries.append([])
        return withdraw_from_minutes(before, items, lang=lang, owner=owner, dropped=tries[-1])

    try:
        # Строго, как у bridge: минутки переписываются целиком, и «�» вместо
        # нечитаемого байта остался бы в файле человека навсегда (№263);
        # отказ — MangledFile, чтобы лог не выдал его за «снимать нечего»
        return rewrite_file(minutes, transform, f"снятые ({len(items)}) не перенесены")
    except UnicodeDecodeError as e:
        raise MangledFile(minutes, f"снятые ({len(items)}) не перенесены", e.reason) from e
    finally:
        if dropped is not None and tries:
            dropped.extend(tries[-1])
