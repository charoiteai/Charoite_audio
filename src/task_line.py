"""Строка поручения: одна грамматика статуса на всех читателей и писателей (№366).

Поручение в минутках — пункт списка с чекбоксом. Форм статуса пять:
  «[ ]»          — открыто;
  «[x]», «[X]»   — выполнено;
  «[-]»          — снято контролем задач (отмена в формате плагина Obsidian Tasks);
  «[ ]» с пометкой контроля «_(снято …)_» — возвращено человеком: контроль такую
                   строку больше не снимает;
  другой символ  — «[/]» (в работе), «[>]», «[!]» у плагина Tasks и любые свои: статус
                   поставил человек, какой — не знаем, поэтому не трогаем.
Пункт узнаётся за любым маркером списка, который узнаёт хоть один переписчик
(«- * + • – — ⁃ ‣ ▪», «1.», «1)»), с любыми пробелами после маркера и без маркера.

Всё, что переписывает раздел поручений (normalize, пометка «не участник», мост
ревизии, разовая правка fix_action_items), обязано узнавать все формы и не
менять статус, поставленный человеком или контролем. Пока грамматику знала только
часть переписчиков, normalize превращал «- [-] **Коля** — …» в «- [ ] [-] Коля — …»:
снятое снова открыто, а жирное имя потеряно. Замер 24.09: разовая правка на
боевом графе переоткрыла бы 574 снятых строки в 179 файлах. Пока у normalize
оставался свой регэксп «уже чекбокс», та же порча ждала «+ [x]», «-  [x]» и «[/]»
(Opus C1 и I1, Sonnet M1 круга 1 по коду; на пяти боевых графах 24.09 таких строк
нет ни одной из 965). Поэтому вопрос «есть ли у строки статус» задаётся только
здесь, а status_changes проверяет результат преобразования на выходе.

Первый шаг №366 — состояние чекбокса и пометка контроля; второй — разбор строки в
запись (parse), вывод обратно (render) и ключ личности поручения (key). Слияние
статусов — следующий шаг (decisions/2026-09-24-stroka-poruchenija-odin-hozjain).

Поля в формате плагина Obsidian Tasks в теле строки: «📅 ГГГГ-ММ-ДД» (срок),
«✅ ГГГГ-ММ-ДД» (выполнено), «❌ ГГГГ-ММ-ДД» (снято). Их ставит код или человек
плагином, поэтому личность поручения от них не зависит: «сделать X» и «сделать X
📅 2026-10-01» — одно поручение, и ключ у них один. Каноничная строка:
«- [c] **Исполнитель** текст _(снято …)_ 📅 … ❌ … ✅ …» — поля в хвосте и в порядке
плагина: он читает поля только с конца строки.

Формы, которые знает модуль, перечислены в tests/fixtures/task_lines.json — общей
таблице для тестов всех клиентов (Python, Swift, Kotlin).
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

# Фрагменты регэкспа — переписчики вставляют их в свои шаблоны строки, а не
# перечисляют маркеры и состояния сами. Маркер пункта списка — объединение маркеров
# всех переписчиков; пробелы после него у каждого свои.
# Знаки — данными: по ним тест проверяет, что у каждого есть строка в общей таблице.
BULLETS = "-*+•–—⁃‣▪"
MARKER = r"(?:[" + BULLETS + r"]|\d+[.)])"
# Чекбокс любого состояния: один любой символ в скобках, как у Obsidian.
BOX = r"\[[^\]]\]"
_STATE = re.compile(r"^\s*(?:" + MARKER + r"\s*)?\[([^\]])\]")
# Подпись порчи: пустой чекбокс, а сразу за ним второй — «- [ ] [x] Коля — …». Её
# оставляет переписчик, не узнавший статус, каким бы регэкспом он ни промахнулся.
_MANGLED = re.compile(r"^\s*(?:" + MARKER + r"\s*)?\[ \]\s*" + BOX)
# Пометка контроля задач в хвосте строки: «_(снято по сроку 24.09)_»,
# «_(снято: нет исполнителя 24.09)_», «_(снято: давно, срок не разобран 24.09)_».
# Снятие ревизией («_(снято ревизией: …)_») — другая форма: задачи не было, и эта
# пометка её не ловит.
CONTROL_MARK = re.compile(r"\s*_\(снято(?: по сроку|:) [^)]*\)_")

# Префикс пункта с ящиком и тот же префикс в виде, который узнают вкладки «Задачи»
# (`^\s*[-*] \[( |x|X)\] +` на Mac и iPhone).
_PREFIX = re.compile(r"^(\s*)(?:" + MARKER + r"\s*)?\[([^\]])\]\s*")
_CANON = re.compile(r"^\s*[-*] \[[^\]]\] ")

OPEN, DONE, CLOSED, RETURNED, OTHER = "open", "done", "closed", "returned", "other"
SETTLED = frozenset({DONE, CLOSED, RETURNED, OTHER})


def status(line: str) -> str | None:
    """Статус пункта-поручения; None — у строки нет чекбокса."""
    m = _STATE.match(line)
    if not m:
        return None
    box = m.group(1)
    if box in ("x", "X"):
        return DONE
    if box == "-":
        return CLOSED
    if box == " ":
        return RETURNED if CONTROL_MARK.search(line) else OPEN
    return OTHER


def canonical(line: str) -> str:
    """Префикс пункта с ящиком — к виду вкладки «- [c] »; отступ, ящик и тело как были.

    «1. [ ] …», «+ [x] …», «-  [ ] …» вкладки не видят вовсе: пока normalize пропускал такие
    строки целиком, открытая задача пропадала из «Задач» (Opus M1 круга 2 по коду). Строка
    без ящика и уже каноническая возвращаются как есть."""
    if _CANON.match(line):
        return line
    m = _PREFIX.match(line)
    if not m:
        return line
    rest = line[m.end():]
    return f"{m.group(1)}- [{m.group(2)}]" + (f" {rest}" if rest else "")


def settled(line: str) -> bool:
    """Статус поставил человек или контроль: выполнено, снято, возвращено или свой
    символ. Переписчики такую строку не трогают."""
    return status(line) in SETTLED


def status_changes(before: str, after: str) -> list[tuple[int, str, str]]:
    """Строки, где преобразование изменило или потеряло статус: (номер с 1, до, после).

    Для преобразований один к одному, как normalize: строка i результата — это строка i
    входа. Другое число строк — ValueError, а не догадка о сопоставлении. Статус,
    который узнала грамматика, обязан дожить до записи, а подпись порчи «- [ ] [x]» —
    не появиться там, где её не было. Это проверка результата, а не доверие регэкспу
    каждого переписчика (Opus I3 круга 1 по коду)."""
    old, new = before.split("\n"), after.split("\n")
    if len(old) != len(new):
        raise ValueError(f"преобразование не один к одному: строк {len(old)} → {len(new)}")
    out = []
    for i, (a, b) in enumerate(zip(old, new), 1):
        was = status(a)
        if (was is not None and status(b) != was) or (_MANGLED.match(b) and not _MANGLED.match(a)):
            out.append((i, a, b))
    return out


# Поля плагина Obsidian Tasks: знак, необязательный селектор эмодзи U+FE0F (его
# добавляют клавиатуры телефонов) и дата. Порядок словаря — порядок плагина в хвосте.
FIELDS = {"due": "📅", "cancelled": "❌", "done": "✅"}
_FIELD = re.compile(r"\s*(" + "|".join(FIELDS.values()) + r")️? *(\d{4}-\d{2}-\d{2})(?!\d)")
# Поле в самом конце тела: разбор снимает поля только с хвоста. Ключ срезает _FIELD где
# угодно — личности поручения дата не принадлежит нигде.
_TAIL = re.compile(_FIELD.pattern.removesuffix(r"(?!\d)") + r"\s*$")
_KIND = {sign: kind for kind, sign in FIELDS.items()}
# Разбор: отступ, маркер, ящик, тело. Тот же префикс, что у _STATE и _PREFIX.
_LINE = re.compile(r"^(?P<indent>\s*)(?:(?P<marker>" + MARKER + r")\s*)?\[(?P<box>[^\]])\]\s*(?P<body>.*)$")
# Исполнитель — жирное в начале тела, за которым пробел или конец. «**Коля**: …» сюда
# не попадает и остаётся текстом целиком: иначе render вставил бы пробел перед двоеточием.
_ASSIGNEE = re.compile(r"^\*\*(?P<name>[^*]+)\*\*(?:\s+|$)")
# Начало строки, которое ключ срезает: маркер и ящик, если они есть.
_HEAD = re.compile(r"^\s*(?:" + MARKER + r"\s*)?(?:" + BOX + r"\s*)?")


@dataclass
class TaskLine:
    """Пункт поручения, разобранный на части. status — как у status(); fields —
    {"due" | "done" | "cancelled": дата}; control — пометка контроля «_(снято …)_»
    без окружающих пробелов или None."""
    indent: str
    marker: str
    box: str
    status: str
    assignee: str | None
    text: str
    fields: dict[str, datetime.date] = field(default_factory=dict)
    control: str | None = None


def parse(line: str) -> TaskLine | None:
    """Строка → запись; строка без ящика — не поручение, None.

    Поля — только хвост строки, как у плагина: он тоже читает поля с конца. Всё, что
    полем не стало, остаётся в тексте дословно: дата посреди текста («перенести с
    📅 2026-10-01 на …»), второе поле того же рода, поле с невозможной датой
    («📅 2026-02-30») и всё левее них. Иначе render терял бы даты и склеивал слова
    (Opus C1 круга 1 по PR #621). Здесь расходимся с плагином намеренно: при дубле
    рода он срезает оба и берёт левое, а невозможную дату пропускает и читает поля
    левее. Разбор здесь на дубле и невозможной дате останавливается, и остаток идёт в
    текст: render обязан вернуть строку без потерь (Opus I1 и M2 круга 2 по PR #621)."""
    m = _LINE.match(line)
    if not m:
        return None
    body = m.group("body")
    mark = CONTROL_MARK.search(body)
    control = None
    if mark:
        control = mark.group().strip()
        body = body[:mark.start()] + body[mark.end():]
    fields: dict[str, datetime.date] = {}
    while (f := _TAIL.search(body)) and _KIND[f.group(1)] not in fields:
        try:
            fields[_KIND[f.group(1)]] = datetime.date.fromisoformat(f.group(2))
        except ValueError:
            break
        body = body[:f.start()]
    body = body.strip()
    who = _ASSIGNEE.match(body)
    return TaskLine(indent=m.group("indent"), marker=m.group("marker") or "", box=m.group("box"),
                    status=status(line), assignee=who.group("name") if who else None,
                    text=body[who.end():] if who else body, fields=fields, control=control)


def render(rec: TaskLine) -> str:
    """Запись → строка в каноничной форме: «- [c] », жирный исполнитель, текст, пометка
    контроля, поля в порядке плагина. Для каноничной строки render(parse(x)) == x."""
    parts = [f"**{rec.assignee}**"] if rec.assignee else []
    parts += [p for p in (rec.text, rec.control) if p]
    parts += [f"{sign} {rec.fields[kind].isoformat()}" for kind, sign in FIELDS.items() if kind in rec.fields]
    return f"{rec.indent}- [{rec.box}]" + "".join(f" {p}" for p in parts)


def key(line: str) -> str:
    """Личность поручения для дедупа и поиска: слова исполнителя и текста в нижнем
    регистре — без маркера, ящика, полей с датами, пометки контроля, жирного и
    пунктуации. Годится и для строки без ящика (пересказ ревизии, хвост после имени).
    Одна задача с полями и без, в любом статусе — один ключ."""
    text = CONTROL_MARK.sub(" ", line)
    text = _FIELD.sub(" ", text)
    text = _HEAD.sub("", text)
    return " ".join(re.findall(r"[^\W_]+", text.lower()))
