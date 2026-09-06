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
RECOVERED_HEAD = re.compile(r"^\s*(?:#{1,6}\s*|\*\*\s*)?восстановленные поручения\s*[:：.]?\s*\**\s*$", re.IGNORECASE)
RECOVERED_WORD = re.compile(r"восстановленн\w* поручени", re.IGNORECASE)
MARKS = {"ru": "(из ревизии)", "en": "(from the review)", "zh": "（来自审阅）"}
SECTION_TITLE = {"ru": "## Поручения", "en": "## Action items", "zh": "## 行动项"}
_HEADING = re.compile(r"^\s*#{1,6}\s")
_BOLD_HEADING = re.compile(r"^\s*\*\*[^*]+\*\*\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+•–—⁃‣▪]|\d+[.)])\s+")
_CHECKBOX = re.compile(r"^\s*\[[ xX]\]\s*")
_ITEM = re.compile(r"^\s*(?:[-*+•–—⁃‣▪]|\d+[.)])\s*(?:\[[ xX]\]\s*)?(?P<text>\S.*)$")
_EMPTY_ITEM = re.compile(r"^(?:нет|none|无|—|-)\.?$", re.IGNORECASE)
_SECTION_WORD = re.compile(r"поручени|action item|行动项", re.IGNORECASE)


def recovered_items(review: str) -> list[str]:
    """Пункты раздела «## Восстановленные поручения» ревизии без маркеров
    и чекбоксов; «нет» и пустые строки — не пункты; раздела нет — пусто."""
    items: list[str] = []
    inside = False
    for line in (review or "").split("\n"):
        if RECOVERED_HEAD.match(line):
            inside = True
            continue
        if inside and (_HEADING.match(line) or _BOLD_HEADING.match(line)):
            break
        if not inside:
            continue
        if not line.strip():
            continue
        if not _BULLET.match(line):
            # перенос строки внутри пункта — продолжение, не новый пункт
            # (GLM r1 M2 / DS r1 I2 по #518); «нет» и строка до первого
            # пункта — мимо
            if items and not _EMPTY_ITEM.match(line.strip().strip("*")):
                items[-1] = (items[-1] + " " + line.strip()).strip()
            continue
        m = _ITEM.match(line)
        if not m:
            continue
        text = m.group("text").strip()
        if not text or _EMPTY_ITEM.match(text):
            continue
        items.append(text)
    return items


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
    return len(wa & wb) / len(wa | wb) >= SIMILAR


def _empty_line(line: str) -> bool:
    """«нет», «- нет», «- [ ] нет» — честно пустой раздел, не пункт
    (чекбокс срезается, GLM r1 I1 по #518)."""
    if not line.strip():
        return False                       # пустые строки считает хвост раздела
    bare = _CHECKBOX.sub("", line.strip().lstrip("-*•–— ").strip()).strip().strip("*").strip()
    return bool(_EMPTY_ITEM.match(bare))


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
            if _HEADING.match(line) and _SECTION_WORD.search(line):
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
        if _key(item) and not any(_same_item(item, other) for other in fresh):
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
           lang: str = "ru") -> int:
    """Дописать восстановленные поручения ревизии в минутки этой встречи.
    Возвращает число дописанных; нет ревизии, минуток или пунктов — 0."""
    try:
        text = review.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    items = recovered_items(text)
    if not items:
        return 0
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
