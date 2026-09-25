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
📅 2026-10-01» — одно поручение, и ключ у них один. Каноничная строка (is_canonical —
одно определение на canonical, render и таблицу форм):
«- [c] **Исполнитель** текст _(снято …)_ 📅 … ❌ … ✅ …» — поля в хвосте и в порядке
плагина: он читает поля только с конца строки. Префикс «* [c] » тоже каноничный: его
видят вкладки «Задачи».

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
# Каноничный префикс для is_canonical и parse: тот же, плюс пункт без тела «- [c]» —
# его так и пишет render. «* [c]» без тела canonical переписывает в «- [c]».
_CANON_LINE = re.compile(_CANON.pattern + r"|^\s*- \[[^\]]\]$")

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


def is_canonical(line: str) -> bool:
    """Строка уже в каноничной форме: префикс вкладки «- [c] » или «* [c] », а хвост —
    пометка контроля и поля — в виде, который пишет render: пометка, затем поля в
    порядке плагина, по одному пробелу, без селектора U+FE0F и без пробелов в конце.
    Середину тела (исполнитель и текст) форма не нормализует: её пишет человек.

    Одно определение на canonical(), render и таблицу форм: для каноничной строки
    canonical(x) == x и render(parse(x)) == x, для прочих строк с ящиком
    canonical(x) == render(parse(x)). Раньше canonical знал только префикс, а render
    переписывал ещё и хвост, и тест обратимости отбирал строки по выводу самого render
    (DeepSeek C1 круга 1 по PR #621)."""
    m = _CANON_LINE.match(line)
    return bool(m) and _canonical_tail(line[m.end():])


def canonical(line: str) -> str:
    """Строка с ящиком — к каноничной форме (is_canonical); без ящика — как есть.

    Префикс — к виду вкладки «- [c] »: «1. [ ] …», «+ [x] …», «-  [ ] …» вкладки не видят
    вовсе, и пока normalize пропускал такие строки целиком, открытая задача пропадала из
    «Задач» (Opus M1 круга 2 по коду). Хвост полей — к виду render; строки без полей
    хвоста не имеют, и для них правится только префикс, тело — как было."""
    if is_canonical(line):
        return line
    m = _PREFIX.match(line)
    if not m:
        return line
    rest = line[m.end():]
    canon = _CANON_LINE.match(line)          # тело — как у parse: у каноничного префикса дословно
    if not _canonical_tail(line[canon.end():] if canon else rest):
        return render(parse(line))
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
# Поле — только в хвосте тела: и разбор, и ключ снимают поля с конца, как плагин. Дата
# посреди текста («перенести с 📅 2026-10-01 на …») — часть дела: ключ, срезавший её
# где угодно, сливал в один разные пункты (DeepSeek I3 круга 1 по PR #621).
_TAIL = re.compile(r"\s*(" + "|".join(FIELDS.values()) + r")️? *(\d{4}-\d{2}-\d{2})\s*$")
# Пометка контроля в хвосте — ровно один пробел перед ней, непустой текст левее и ничего
# после: иначе render не вернул бы строку без потерь, и она остаётся текстом.
_CONTROL_TAIL = re.compile(r"(?<=\S) " + CONTROL_MARK.pattern.removeprefix(r"\s*") + "$")
# Хвост в каноничном виде — тот, что пишет render.
_CANON_TAIL = re.compile("(?:" + _CONTROL_TAIL.pattern.removeprefix(r"(?<=\S)").removesuffix("$") + ")?"
                         + "".join(rf"(?: {sign} \d{{4}}-\d{{2}}-\d{{2}})?" for sign in FIELDS.values()))
_KIND = {sign: kind for kind, sign in FIELDS.items()}
# Разбор: отступ, маркер, ящик, тело. Тот же префикс, что у _STATE и _PREFIX.
_LINE = re.compile(r"^(?P<indent>\s*)(?:(?P<marker>" + MARKER + r")\s*)?\[(?P<box>[^\]])\]\s*(?P<body>.*)$")
# Исполнитель — жирное в начале тела, за которым пробел или таб (разделитель), или конец.
# «**Коля**: …» сюда не попадает и остаётся текстом целиком: иначе render вставил бы
# пробел перед двоеточием. Разделитель — в записи (sep): «**Б** » в конце строки и
# «**Б**\tтекст» render возвращает дословно, а исполнитель у них есть (облачная
# голова, круг 2 по PR #621). Пробелы сверх одного остаются в тексте.
_ASSIGNEE = re.compile(r"^\*\*(?P<name>[^*]+)\*\*(?P<sep>[ \t]|$)")
# Начало строки, которое ключ срезает: маркер и ящик, если они есть.
_HEAD = re.compile(r"^\s*(?:" + MARKER + r"\s*)?(?:" + BOX + r"\s*)?")


def _tail(body: str) -> tuple[str, str | None, dict[str, datetime.date]]:
    """(тело без хвоста, пометка контроля, поля): хвост снимается с конца по одному
    элементу, поле каждого рода и пометка — не больше одного раза.

    Всё, что элементом хвоста не стало, остаётся телом дословно: дата посреди текста,
    второе поле того же рода, поле с невозможной датой («📅 2026-02-30») и всё левее них
    (Opus C1 круга 1 по PR #621). Здесь расходимся с плагином намеренно: при дубле рода
    он срезает оба и берёт левое, а невозможную дату пропускает и читает поля левее.
    Разбор на них останавливается: render обязан вернуть строку без потерь (Opus I1 и
    M2 круга 2 по PR #621)."""
    fields: dict[str, datetime.date] = {}
    control = None
    while True:
        if (f := _TAIL.search(body)) and _KIND[f.group(1)] not in fields:
            try:
                fields[_KIND[f.group(1)]] = datetime.date.fromisoformat(f.group(2))
            except ValueError:
                break
            body = body[:f.start()]
        elif control is None and (c := _CONTROL_TAIL.search(body)):
            control = c.group().strip()
            body = body[:c.start()]
        else:
            break
    return body, control, fields


def _canonical_tail(body: str) -> bool:
    """Хвост тела уже в виде render. Без текста левее хвоста render не ставит пробел
    перед первым элементом — тело тогда начинается прямо с него; пустое тело — тоже
    каноничное."""
    head, _, _ = _tail(body)
    tail = body[len(head):]
    return bool(_CANON_TAIL.fullmatch(f" {tail}" if tail and not head else tail))


@dataclass
class TaskLine:
    """Пункт поручения, разобранный на части. marker — знак списка; с пробелом за ним
    («- », «* »), если префикс строки уже каноничный «- [c] »: только тогда render
    сохраняет «* » и пробел за ящиком пустого пункта.
    status — как у status(); text — дословно, со всеми пробелами, кроме разделителя после
    исполнителя; sep — сам разделитель: пробел, таб или "" (исполнитель в конце тела, или
    исполнителя нет); fields — {"due" | "done" | "cancelled": дата}; control — пометка
    контроля «_(снято …)_» из хвоста, без окружающих пробелов, или None."""
    indent: str
    marker: str
    box: str
    status: str
    assignee: str | None
    text: str
    fields: dict[str, datetime.date] = field(default_factory=dict)
    control: str | None = None
    sep: str = ""


def _parts(body: str) -> tuple[str | None, str, str, str | None, dict[str, datetime.date]]:
    """(исполнитель, разделитель, текст, пометка контроля, поля) тела — одна грамматика на
    parse и key."""
    head, control, fields = _tail(body)
    who = _ASSIGNEE.match(head)
    if not who:
        return None, "", head, control, fields
    return who.group("name"), who.group("sep"), head[who.end():], control, fields


def parse(line: str) -> TaskLine | None:
    """Строка → запись; строка без ящика — не поручение, None. Хвост — как у _tail."""
    m = _LINE.match(line)
    if not m:
        return None
    # у каноничного префикса тело — дословно после «] »: render вернёт и лишние пробелы
    canon = _CANON_LINE.match(line)
    marker = canon.group().lstrip()[0] + " " * canon.group().endswith(" ") if canon else m.group("marker") or ""
    who, sep, text, control, fields = _parts(line[canon.end():] if canon else m.group("body"))
    return TaskLine(indent=m.group("indent"), marker=marker, box=m.group("box"), status=status(line),
                    assignee=who, text=text, fields=fields, control=control, sep=sep)


def render(rec: TaskLine) -> str:
    """Запись → строка в каноничной форме (is_canonical): префикс «- [c] » («* [c] », если
    он у строки уже был), жирный исполнитель, текст, пометка контроля, поля в порядке
    плагина. render(parse(x)) == x для строки в каноничной форме, для прочих строк с
    ящиком render(parse(x)) == canonical(x)."""
    # исполнитель, его разделитель и текст — одна часть: разделитель записи, а не пробел render
    head = (f"**{rec.assignee}**{rec.sep}" if rec.assignee else "") + rec.text
    parts = [p for p in (head, rec.control) if p]
    parts += [f"{sign} {rec.fields[kind].isoformat()}" for kind, sign in FIELDS.items() if kind in rec.fields]
    marker = rec.marker if rec.marker == "* " else "- "
    # пустой пункт «- [ ] » — пробел за ящиком, если он был у каноничного префикса
    body = "".join(f" {p}" for p in parts) or (" " if rec.marker in ("- ", "* ") else "")
    return f"{rec.indent}{marker}[{rec.box}]" + body


def key(line: str) -> str:
    """Личность поручения для дедупа и поиска: слова исполнителя и текста записи в нижнем
    регистре — без маркера, ящика, полей хвоста, пометки контроля, жирного и пунктуации.
    Годится и для строки без ящика (пересказ ревизии, хвост после имени): тело той же
    грамматикой, что у parse. Одна задача с полями и без, в любом статусе — один ключ.

    Префикс порчи «- [ ] [x] …» снимается целиком, по её же подписи _MANGLED: иначе «x»
    второго ящика оставалось в ключе (DeepSeek I2 круга 1 по PR #621). Пометка контроля
    не в хвосте остаётся текстом записи, а из ключа уходит, как и раньше.

    Пункт без слов («- [ ] 📅 2026-10-01») — пустой ключ: личности у него нет, и мост его
    не сравнивает и не снимает. Считать его пунктом по разбору значило бы, что пустой ключ
    совпадает с любым другим пустым (DeepSeek M1 круга 2 по PR #621 — отклонено)."""
    line = CONTROL_MARK.sub(" ", line)       # первой, как было: пометка где угодно — не слова дела
    head = _MANGLED.match(line) or _HEAD.match(line)
    who, _, text, _, _ = _parts(line[head.end():])
    return " ".join(re.findall(r"[^\W_]+", f"{who or ''} {text}".lower()))


def fields(line: str) -> dict[str, datetime.date]:
    """Поля хвоста строки — с ящиком и без: пересказ ревизии тоже может нести срок."""
    return _tail(line[_HEAD.match(line).end():])[2]
