"""Поручения из минуток — в формат, который видит приложение.

Промпт просит модель писать поручение чекбоксом («- [ ] **Имя** — что — срок»),
и модель честно выдаёт имя, суть и срок. Но оборачивает всё в свой markdown:

    *   **- **Дмитрий** — подготовить демонстрацию. — **Срок: завтра**.**

Скобок `[ ]` здесь уже нет, и regex окна «Задачи»
(`^\\s*[-*] \\[( |x|X)\\] +(.+)$`) такую строку не находит. Замер по рабочему
графу: 138 файлов минуток и саммари, чекбоксы нашлись в двух. То есть окно
задач стояло пустым при том, что поручения исправно формулировались на
каждой встрече.

Просить модель ещё строже — ненадёжно: она и так «соблюдает» формат, как
умеет, и на длинной генерации срывается снова. Формат приводится к нужному
детерминированно, уже после генерации.
"""
from __future__ import annotations

import re

# Заголовок раздела поручений на всех трёх языках продукта.
_SECTION = re.compile(
    r"^\s*(?:[*_#]*\s*)?(?:поручени|action item|行动项)\w*\s*[:：]?\s*[*_]*\s*$",
    re.IGNORECASE,
)
# Любой другой заголовок — конец раздела: markdown-заголовок или жирный
# «**Решения:**».
_OTHER_SECTION = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*]+:\*\*\s*$)")
# Голый заголовок «Открытые вопросы:» — тоже конец раздела (минутки 31.08
# пишут так): строка без отступа, не пункт, двоеточие в конце.
_BARE_HEADING = re.compile(r"^\S[^:\n]*[:：]\s*$")
# …но закрывает раздел она ТОЛЬКО как известная секция минуток — контракт с
# собственным промптом (участники, темы, решения, открытые вопросы, риски…),
# а не любая строка нужной формы. Это третий заход на эту границу: сначала
# раздел рвало «  Срок:» с отступом (DS I1), после починки отступом — «Срок:»
# без отступа, «Имя:» и жёсткий перенос «…следующие\nсроки:» (GLM I1). Форму
# внутренностей пункта не предскажешь, а список секций своего же промпта
# конечен. Цена: незнакомая секция не закроет раздел и её пункты уйдут в
# чекбоксы — мусор в окне виден и дёшев, потерянное поручение невидимо.
_KNOWN_BARE_SECTION = re.compile(
    r"^(?:участник|тем[аы]|решени|открыт|вопрос|риск|дат[аы]|саммари|итог|"
    r"participant|topic|decision|open question|question|risk|date|summary|"
    r"参与者|主题|决定|决议|待决|开放|风险|日期|摘要)",
    re.IGNORECASE,
)
# Строка-пункт: маркер списка в начале (включая типографские тире, которыми
# модель иногда открывает пункт).
_BULLET = re.compile(r"^\s*(?:[-*+•–—⁃‣▪]|\d+[.)])\s+")
# Уже правильный чекбокс — не трогаем.
_CHECKBOX = re.compile(r"^\s*[-*] \[[ xX]\] ")


def normalize(text: str) -> str:
    """Привести пункты раздела «Поручения» к виду «- [ ] …».

    Остальной текст не трогаем: минутки читает и человек, и переписывать их
    целиком ради формата — плохая сделка.
    """
    lines = text.split("\n")
    out: list[str] = []
    inside = False
    for line in lines:
        if _SECTION.match(line):
            inside = True
            out.append(line)
            continue
        if inside:
            # Раздел кончился на следующем заголовке: markdown/жирном — по
            # форме, голом — только по известному имени секции.
            if (_OTHER_SECTION.match(line)
                    or (_BARE_HEADING.match(line)
                        and _KNOWN_BARE_SECTION.match(line))) \
                    and not _BULLET.match(line):
                inside = False
                out.append(line)
                continue
            if line.strip() and _BULLET.match(line) and not _CHECKBOX.match(line):
                out.append(_to_checkbox(line))
                continue
        out.append(line)
    return "\n".join(out)


def _to_checkbox(line: str) -> str:
    """«*   **- **Дмитрий** — сделать. — **Срок: завтра**.**» → «- [ ] **Дмитрий** — сделать — до завтра»."""
    body = _BULLET.sub("", line).strip()
    # Модель часто оборачивает пункт целиком в ** … ** и дублирует маркер.
    body = re.sub(r"^\*{1,3}\s*", "", body)
    body = re.sub(r"\s*\*{1,3}$", "", body)
    body = _BULLET.sub("", body).strip()
    body = re.sub(r"^\*{1,3}\s*", "", body)
    # Хвостовая точка после «Срок: …**.**» — мусор от вложенных звёздочек.
    body = re.sub(r"[.\s*]+$", "", body).strip()
    # Снятие обёртки съедает открывающие звёздочки имени: остаётся
    # «Дмитрий** — сделать». Восстанавливаем парность, иначе имя перестаёт быть
    # жирным, а по нему человек и находит в списке своё.
    if not body.startswith("**"):
        m = re.match(r"^([^*]{1,60}?)\*\*\s*(.*)$", body)
        if m:
            body = f"**{m.group(1).strip()}** {m.group(2).lstrip()}".strip()
    # Имя без единой звёздочки — выделяем сами.
    if not body.startswith("**"):
        m = re.match(r"^([А-ЯЁA-Z][\wЁё]*(?:\s+и\s+[А-ЯЁA-Z][\wЁё]*)*)\s*[—\-–:]\s*(.+)$", body)
        if m:
            body = f"**{m.group(1)}** — {m.group(2)}"
    # «Срок: 25.07» читается как «— до 25.07», но «Срок: до следующего
    # релиза» превратилось бы в «до до следующего релиза». Предлог добавляем
    # только там, где дальше идёт дата, и не трогаем формулировку модели.
    def _deadline(m: "re.Match[str]") -> str:
        rest = m.group(1).strip()
        needs_prep = bool(re.match(r"[\d]", rest)) and not rest.lower().startswith("до")
        return f" — до {rest}" if needs_prep else f" — {rest}"

    # Тире перед «Срок:» обязательно: это разделитель хвоста пункта. Без него
    # переписывалась живая речь в середине строки — «обсудить срок: завтра
    # решаем» превращалось в «обсудить — завтра решаем» (advisory DS по #462;
    # normalize теперь бежит по черновику, который читает человек). Цена:
    # «…демо. Срок: завтра» без тире остаётся как есть — читаемо, просто
    # без причёсывания.
    body = re.sub(r"\s+[—\-–]\s*\*{0,2}(?:Срок|Due|期限)\s*[:：]\s*(.+)$",
                  _deadline, body, flags=re.IGNORECASE)
    # Страховка непарных ** — ПОСЛЕДНЕЙ и безусловно: восстановление парности
    # само создаёт нечёт («Проверить **договор** — …» → «**Проверить**
    # договор** — …», GLM M9 по #464), а префиксное условие его пропускало.
    # Считать раньше _deadline нельзя: «— **Срок: …» держит нечёт до снятия.
    # Висячие ** в живом документе хуже, чем нежирная строка, — снимаем все.
    if body.count("**") % 2 == 1:
        body = body.replace("**", "")
    return f"- [ ] {body}"


# ---- исполнитель поручения — участник встречи --------------------------------
# 05.09 минутки приписали поручение Саше Никитину, которого на встрече не было
# (его лишь упомянули: «это к Саше Никитину вопрос»), и пример по валютам —
# Дмитрию вместо Ани. Промпт просит имена из разговора, но модель охотно
# назначает того, о ком говорили. Кому можно: тому, кто говорил (метка
# говорящего в стенограмме) или кого конвейер записал в шапку «Участники
# (звучали в разговоре)», плюс владелец. Собирательные исполнители
# («Команда», «Все», «владелец и собеседники») — не люди, их не проверяем.
# Ограничение: сверка по имени, а не по фамилии — «Саша Никитин» при
# участнике «Саша» пройдёт; фамилий в шапке стенограммы обычно нет.
_PARTICIPANTS_HEAD = re.compile(r"^(?:Участники|Participants|参会者)[^:：]*[:：]\s*(.+)$", re.M)
_SPEAKER_LABEL = re.compile(r"^\*\*([^*\n]{1,60})\*\*\s*\[\d{2}:\d{2}", re.M)
_PLACEHOLDER = re.compile(r"^(?:собеседник|speaker|说话人|发言人|я|me)\b", re.I)
_COLLECTIVE = frozenset({
    "команда", "все", "всем", "коллеги", "участники", "владелец", "владелец и собеседники",
    "team", "all", "everyone", "owner", "participants", "全体", "团队", "所有人",
})
_ASSIGNEE_LINE = re.compile(r"^(\s*)[-*] \[[ xX]\] \*\*([^*\n]{1,80})\*\*(.*)$")
_NAME_SPLIT = re.compile(r"\s*(?:,|/|;|\s+и\s+|\s+and\s+|\s+&\s+)\s*", re.I)
OUTSIDER_MARK = "⚠ не участник"


def participants_of(transcript: str, owner: str = "") -> set[str]:
    """Кто был на встрече по стенограмме: шапка «Участники (звучали в
    разговоре)» + метки говорящих + владелец. Метки-заглушки («Собеседник 2»,
    «Speaker 1», «Я») и роли в скобках не в счёт. Пусто — участники неизвестны,
    и судить некого."""
    names: set[str] = set()
    text = transcript or ""
    m = _PARTICIPANTS_HEAD.search(text)
    if m:
        head = re.sub(r"\s*[(（].*?[)）]", "", m.group(1))
        names |= {x.strip() for x in head.split(",") if x.strip()}
    for lab in _SPEAKER_LABEL.findall(text):
        lab = lab.strip()
        if lab and not _PLACEHOLDER.match(lab):
            names.add(lab)
    if owner and owner.strip():
        names.add(owner.strip())
    return names


def _is_participant(name: str, participants: set[str]) -> bool:
    n = name.strip().strip(".").casefold()
    if not n or n in _COLLECTIVE:
        return True
    first = n.split()[0]
    for p in participants:
        pf = p.strip().casefold().split()
        if not pf:
            continue
        # полное имя, первое имя или падежная форма («Ольге» ↔ «Ольга»):
        # общий префикс из четырёх букв, как у speaker_names.PREFIX
        if n == " ".join(pf) or first == pf[0]:
            return True
        if len(first) >= 4 and len(pf[0]) >= 4 and first[:4] == pf[0][:4]:
            return True
    return False


def flag_outsiders(text: str, participants: set[str]) -> str:
    """Поручение тому, кого на встрече не было, — пометка, а не задача.

    Строка «- [ ] **Имя** — …» в разделе поручений становится
    «- ⚠ не участник (Имя): **Имя** — …»: без чекбокса её не подхватит окно
    «Задачи», а читающий минутки видит, что исполнителя надо назначить заново
    или передать дальше. Остальные разделы и известные исполнители не
    трогаются. Пустой список участников — ничего не решаем."""
    if not participants:
        return text
    out: list[str] = []
    inside = False
    for line in text.split("\n"):
        if _SECTION.match(line):
            inside = True
            out.append(line)
            continue
        if inside and ((_OTHER_SECTION.match(line)
                        or (_BARE_HEADING.match(line) and _KNOWN_BARE_SECTION.match(line)))
                       and not _BULLET.match(line)):
            inside = False
        if inside:
            m = _ASSIGNEE_LINE.match(line)
            if m:
                names = [x for x in _NAME_SPLIT.split(m.group(2)) if x.strip()]
                strangers = [x.strip() for x in names if not _is_participant(x, participants)]
                if strangers:
                    line = f"{m.group(1)}- {OUTSIDER_MARK} ({', '.join(strangers)}): **{m.group(2)}**{m.group(3)}"
        out.append(line)
    return "\n".join(out)

