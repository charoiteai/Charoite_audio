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
# Пометка «не участник» (flag_outsiders) — тоже не трогаем: иначе следующий
# проход normalize (пересборка, повторный «Протокол») вернул бы строке
# чекбокс, и задача снова ушла бы отсутствующему.
_OUTSIDER_LINE = re.compile(r"^\s*[-*] ⚠ (?:не участник|not a participant|非与会者)\b")


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
            if line.strip() and _BULLET.match(line) and not _CHECKBOX.match(line) \
                    and not _OUTSIDER_LINE.match(line):
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
# говорящего) или кого конвейер записал в шапку «Участники (звучали в
# разговоре)», плюс владелец. Собирательные исполнители («Команда», «Все»,
# «владелец и собеседники») — не люди, их не проверяем.
# Источник участников: у демона — Transcript.participants() (структура, а не
# текст: full() и файл рендерят метки по-разному — Critical круга 1 #510);
# у пересборки и MCP — текст файла стенограммы через participants_of.
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
# Пометка — на языке минуток (`sufler.language`): английские минутки с русской
# пометкой читались бы как сбой (Minor GLM/DS, круг 1 #510).
OUTSIDER_MARKS = {"ru": "⚠ не участник", "en": "⚠ not a participant", "zh": "⚠ 非与会者"}
OUTSIDER_MARK = OUTSIDER_MARKS["ru"]
# Падеж: «Саше»/«Саша», «Ане»/«Аня», «Дмитрию»/«Дмитрий», «Игорю»/«Игорь» —
# у имён на -а/-я склонение меняет последнюю букву, и общий 4-буквенный
# префикс это не видит («саше» ≠ «саша»); сравниваем основы без последней
# гласной/й/ь (GLM C2 / DS I1, круг 1 #510).
_SOFT_TAIL = re.compile(r"[аеёиоуыэюяьй]$")


def _stem(word: str) -> str:
    return _SOFT_TAIL.sub("", word)


def participants_set(names, owner: str = "") -> set[str]:
    """Имена в множество участников: заглушки («Собеседник 2», «Speaker 1»,
    «Я») и пустые отбрасываются, владелец добавляется. Пусто — участники
    неизвестны, и судить некого."""
    out: set[str] = set()
    for raw in names or ():
        name = str(raw or "").strip()
        if name and not _PLACEHOLDER.match(name):
            out.add(name)
    if owner and owner.strip():
        out.add(owner.strip())
    return out


def participants_of(transcript: str, owner: str = "") -> set[str]:
    """Участники из ТЕКСТА стенограммы в формате файла: шапка «Участники
    (звучали в разговоре): …» (роли в скобках срезаются) и метки говорящих
    «**Имя** [чч:мм]». Для живого объекта Transcript этот текст не годится —
    брать Transcript.participants() и participants_set()."""
    names: list[str] = []
    text = transcript or ""
    m = _PARTICIPANTS_HEAD.search(text)
    if m:
        head = re.sub(r"\s*[(（].*?[)）]", "", m.group(1))
        names += [x.strip() for x in head.split(",") if x.strip()]
    names += [lab.strip() for lab in _SPEAKER_LABEL.findall(text)]
    return participants_set(names, owner)


def _same_person(word: str, known_word: str) -> bool:
    """Одно имя в разных падежах или одно и то же слово.

    Основы без последней мягкой буквы равны («саше»/«саша» → «саш»); или одна
    основа — другая плюс только гласные («ольго» = «ольг» + «о»: «Ольгой»).
    «Марине» и «Мария» («марин» / «мари» + «н») — разные люди: голый
    4-буквенный префикс их склеивал (GLM, круг 1 #510)."""
    if word == known_word:
        return True
    a, b = _stem(word), _stem(known_word)
    if len(a) >= 2 and a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= 3 and long.startswith(short) and _VOWELS_ONLY.fullmatch(long[len(short):]) is not None


_VOWELS_ONLY = re.compile(r"[аеёиоуыэюяьй]*")
_JOINERS = frozenset({"и", "and", "&", "with"})


def _same_people(whole: str, participants: set[str]) -> bool:
    """«Никитин, Саша» — это «Саша Никитин»: тот же набор слов в любом порядке.
    Союзы не в счёт, чтобы «Дмитрий и Ольга» не сошёл за одного Дмитрия."""
    words = {w for w in re.split(r"[\s,/;]+", whole.strip().casefold()) if w} - _JOINERS
    if not words:
        return False
    for p in participants:
        pf = {w for w in p.strip().casefold().split() if w}
        if pf and pf == words:
            return True
    return False


def _is_participant(name: str, participants: set[str]) -> bool:
    n = name.strip().strip(".").casefold()
    if not n or n in _COLLECTIVE:
        return True
    first = n.split()[0]
    for p in participants:
        pf = p.strip().casefold().split()
        if not pf:
            continue
        if n == " ".join(pf):
            return True
        # первое слово исполнителя — против КАЖДОГО слова участника: в шапке
        # может стоять «Дмитрий Петров», а в поручении — «Петров» (GLM I3)
        if any(_same_person(first, w) for w in pf):
            return True
    return False


def flag_outsiders(text: str, participants: set[str], lang: str = "ru") -> str:
    """Поручение тому, кого на встрече не было, — пометка, а не задача.

    Строка «- [ ] **Имя** — …» в разделе поручений становится
    «- ⚠ не участник (Имя): **Имя** — …»: без чекбокса её не подхватит окно
    «Задачи», а читающий минутки видит, что исполнителя надо назначить заново
    или передать дальше. Остальные разделы и известные исполнители не
    трогаются. Пустой список участников — ничего не решаем."""
    if not participants:
        return text
    mark = OUTSIDER_MARKS.get((lang or "ru").strip().lower()[:2], OUTSIDER_MARK)
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
                whole = m.group(2).strip()
                # «Никитин, Саша» — сначала целиком (тот же набор слов), потом по
                # частям (GLM M5); «Дмитрий и Ольга» целиком не сойдёт за Дмитрия
                if _same_people(whole, participants):
                    strangers: list[str] = []
                else:
                    names = [x.strip() for x in _NAME_SPLIT.split(whole) if x.strip()]
                    strangers = [x for x in names if not _is_participant(x, participants)]
                if strangers:
                    line = f"{m.group(1)}- {mark} ({', '.join(strangers)}): **{m.group(2)}**{m.group(3)}"
        out.append(line)
    return "\n".join(out)
