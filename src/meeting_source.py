"""Источник производной встречи: речь плюс факты о записи (№316/№317).

До этого у производной (минутки, разбор, тезисы) был ровно один вход — строка
текста, и каждый потребитель сам решал, что в неё подмешать: `_fit`
сворачивал, черновик демона резал окно, `debrief_excerpt` брал голову и
хвост, `speech_of` отрезал секцию, `fact_check` сверял «речь», паспорт
хешировал «речь». Факт о записи (канал собеседников пропадал на 40 минут)
доходил до моделей либо как речь (MCP-минутки читали весь файл с хвостом),
либо никак (все остальные), и протокол выходил внешне полным.

Здесь один объект `MeetingSource(speech, recording_note)`:
- `speech` — только сказанное (`transcript.speech_of`): его режут свёртки и
  окна, по нему сверяет цитаты `fact_check`;
- `recording_note` — оговорка о неполной записи по сайдкару
  (`channel_trace.recording_note`): в промпт идёт отдельным блоком снаружи
  стенограммы (`LLM.recording_block`), после всех обрезок; в документ —
  механической строкой (`with_note`), не поручением модели;
- `canon()`/`sha()` — то, что хеширует паспорт производной: минутки без
  оговорки при появившейся оговорке становятся STALE ровно один раз;
- `began_at` — начало встречи по штампу файла, строка шапки «Дата/время»
  минуток (№738). Вне `canon()`: момент выводится из того же имени, что и ключ
  встречи, и паспорт от него не зависит.

Входной круг DS и GLM (19.09) сошёлся на этом объекте: «файл как вход не
существует, есть то, что владелец отдал как речь, и то, что он же пометил как
факт о записи».
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import NamedTuple

import action_items
import channel_trace
import fact_check
import live_sidecar
import meeting_stamp
import privacy
import transcript
import transcript_origin


@dataclasses.dataclass(frozen=True)
class MeetingSource:
    speech: str
    recording_note: str | None = None
    # не часть источника: равенство и хеш — по речи и оговорке (№738)
    began_at: str | None = dataclasses.field(default=None, compare=False)

    def canon(self) -> str:
        """Каноническое тело источника для хеша паспорта."""
        return self.speech + (("\n\n" + self.recording_note) if self.recording_note else "")

    def sha(self) -> str:
        return live_sidecar.sha(self.canon())

    def matches(self, stored) -> bool:
        """Собрана ли производная по ЭТОМУ источнику: сравнение хеша из паспорта
        — только здесь. Второй читатель `minutes_source_sha256` в пересборке
        сравнивал с голой речью и после появления оговорки не совпадал никогда
        (Critical GLM / Important DS выходного круга по №317)."""
        stored = live_sidecar.valid_sha(stored)
        return stored is not None and stored == self.sha()


def of(live: pathlib.Path, text: str) -> MeetingSource:
    """Источник по файлу стенограммы и его сайдкару — единственный способ
    получить и речь, и оговорку; прямое чтение файла у потребителя производных
    источником не считается. Сайдкар — только прямой (`live_sidecar.sidecar_for`
    без штампа): ретро-обход передавал сюда посекундный штамп `bare`, а после
    наката темы сайдкар под ним не существует — оговорка терялась, и два
    писателя одной производной считали разные хеши (Critical DS и GLM
    выходного круга по №317). Штамп нужен только писателю до переезда пары
    (`ChannelTrace`) — у читателей его нет."""
    return MeetingSource(transcript.speech_of(text), channel_trace.recording_note(live),
                         began_at=began_of(live))

def live(speech: str, note: str | None, *, began_at: str | None) -> MeetingSource:
    """Источник живого пути: речь из `Transcript.full()`, оговорка из
    `ChannelTrace.summary()` того же процесса, начало — `began_of(tr.path)`.
    Начало обязательным словом: умолчание None молча оставило бы живой путь
    без даты (Haiku I4 входа №738)."""
    return MeetingSource(speech, note, began_at=began_at)


def began_of(live: pathlib.Path) -> str | None:
    """Значение поля «Дата/время» минуток по имени главного файла встречи.

    Штамп живой записи — начало: «ГГГГ-ММ-ДД ЧЧ:ММ». У импорта штамп взят из
    времени файла записи (конец встречи или копирования, `transcribe_file`),
    поэтому только дата, без ложной точности (Sonnet I5 и DS I5 входа №738).
    Формат ГГГГ-ММ-ДД, не ДД.ММ: до сверки фактов шапку снимает `without_date`,
    но читатели файла (граф, разбор) видят её в тексте как есть.
    None — имя не наше: шапку тогда не трогаем."""
    moment = meeting_stamp.meeting_start(live.stem)
    if moment is None:
        return None
    if transcript_origin.SIDECAR_KEY in (live_sidecar.read_direct(live) or {}):
        return f"{moment:%Y-%m-%d}"
    return f"{moment:%Y-%m-%d %H:%M}"


#: подпись поля даты, которую пишет код, по языку минуток
DATE_LABEL = {"ru": "**Дата/время:**", "en": "**Date/time:**", "zh": "**日期/时间：**"}
#: ключи поля даты в шапке: свои подписи и короткие, которые писала модель
#: (MCP-промпт просил «дата»); ключ — метка без звёзд и двоеточия, строчными
DATE_KEYS = frozenset({"дата/время", "дата и время", "дата", "date/time", "date and time", "date",
                       "日期/时间", "日期和时间", "日期"})
#: голые метки (без звёзд), которые делят строку шапки на поля, — закрытый
#: набор, как список `.ignored` в приложении (выход №738 out2, Sonnet I1/I3 =
#: Haiku C1: открытая грамматика принимала прозу «Итог: ок» за поле)
HEAD_KEYS = DATE_KEYS | {"участники", "participants", "参会人", "参与者"}
# Снимается только ПОЛЕ даты — метка и значение до следующей метки или «|» —
# в строке, где оно первое; прочие поля строки остаются. Выделенная метка
# «**М:**» / «**М**:» (и «*М:*», «__М:__») — где угодно в строке (старый шаблон
# писал «**Дата/время:** … **Участники:** …»), голая — из `HEAD_KEYS`, в начале
# куска или после «,» / «;». Границу шапки своей грамматикой не угадываем
# (выход №738 out3: три круга подряд — новые формы шапки): смотрим строки под
# заголовком до первого раздела по предикату поручений `action_items.ends_section`.
_BOLD_LABEL = re.compile(r"(?P<m>\*{1,3}|__)(?P<label>[^*_\n]{1,40}?)[ \t]*(?:[:：][ \t]*(?P=m)|(?P=m)[ \t]*[:：])")
_PLAIN_LABEL = re.compile(
    r"(?:^|(?<=[,;]))[ \t]*(?P<label>"
    + "|".join(re.escape(k) for k in sorted(HEAD_KEYS, key=len, reverse=True))
    + r")(?![\w/])[ \t]*[:：]", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+")
# украшение перед меткой: эмодзи или значок («📅 **Дата:**», выход out2 DS M1)
_DECOR = re.compile(r"^[ \t]*[^\w\s*#<>|\[\]()\-+]{1,2}[ \t]+")
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]")
_COMMENT = re.compile(r"^[ \t]*<!--.*-->[ \t]*$")
# кусок без метки после «|» уходит с датой, только если это время или число
# («| 20:00»); прочий текст остаётся («| Место: офис», выход out3 Haiku C1)
_DATEISH = re.compile(r"[\s\d:.,/\-–—]*\d[\s\d:.,/\-–—]*")


def _key(label: str) -> str:
    return " ".join(label.lower().split())


def _fields(chunk: str) -> list[tuple[str | None, str]]:
    """Поля куска строки: (ключ метки, текст поля с меткой). Текст до первой
    метки — с ключом None: это не поле."""
    bold = [(m.start(), m.end(), _key(m["label"])) for m in _BOLD_LABEL.finditer(chunk)]
    marks = [(a, k) for a, _b, k in bold]
    marks += [(m.start("label"), _key(m["label"])) for m in _PLAIN_LABEL.finditer(chunk)
              if not any(a <= m.start("label") < b for a, b, _k in bold)]
    marks.sort()
    out: list[tuple[str | None, str]] = []
    if not marks or marks[0][0] > 0:
        out.append((None, chunk[:marks[0][0] if marks else len(chunk)]))
    for i, (at, key) in enumerate(marks):
        out.append((key, chunk[at:marks[i + 1][0] if i + 1 < len(marks) else len(chunk)]))
    return out


def _body(line: str) -> tuple[str, str]:
    """(маркер пункта или украшение, текст строки после него)."""
    m = _LIST_ITEM.match(line) or _DECOR.match(line)
    return (m.group(0), line[m.end():]) if m else ("", line.lstrip())


def _under_title(lines: list[str]) -> int:
    """Начало шапки: под заголовком «# » (или «#» любого уровня, если он первая
    строка содержания, — «## Черновик минуток»), а без заголовка — со строки,
    где первое поле из `HEAD_KEYS` («Протокол встречи» прозой у черновиков).
    Комментарии и проза над ними — преамбула (выход out2, DS C1 = Haiku C2).
    Ничего из этого — перед первой строкой содержания."""
    first = None
    for j, line in enumerate(lines):
        if not line.strip() or _COMMENT.match(line):
            continue
        if line.lstrip().startswith("# ") or (first is None and _HEADING.match(line)):
            return j + 1
        if _HEADING.match(line):
            break
        if _fields(_body(line)[1])[0][0] in HEAD_KEYS:
            return j
        first = j if first is None else first
    return 0 if first is None else first


def _without_date_field(line: str) -> str | None:
    """Строка без поля даты; None — первое поле строки не дата, строку не трогаем."""
    marker, body = _body(line)
    chunks = [_fields(c) for c in body.split("|")]
    if chunks[0][0][0] not in DATE_KEYS:
        return None
    kept = []
    for c in chunks:
        part = "".join(t for k, t in c if (k is None and not _DATEISH.fullmatch(t))
                       or (k is not None and k not in DATE_KEYS))
        part = part.strip().rstrip(",;").strip()
        if part:
            kept.append(part)
    return marker + " | ".join(kept) if kept else ""


def _strip_date(doc: str) -> tuple[str, int | None]:
    """(документ без поля даты, номер строки первого снятого поля или None).
    Смотрим строки под заголовком до первого раздела; строка без полей уходит,
    строка, где первое поле не дата, — те же байты."""
    lines = doc.split("\n")
    start = _under_title(lines)
    out = lines[:start]
    at = None
    for j in range(start, len(lines)):
        line = lines[j]
        bare = _without_date_field(line) if line.strip() else None
        if bare is None:
            if action_items.ends_section(line):
                return "\n".join(out + lines[j:]), at
            out.append(line)
            continue
        at = len(out) if at is None else at
        if bare:
            out.append(bare)
    return "\n".join(out), at


def without_date(doc: str) -> str:
    """Снять поле даты из шапки — и написанное моделью, и строку `with_date`."""
    return _strip_date(doc)[0]


def with_date(doc: str, began_at: str | None, lang: str = "ru", *, at: int | None = None) -> str:
    """Строка «Дата/время» от кода — на место снятого поля даты (`at` — место,
    если поле сняли раньше, до сверки фактов), иначе под заголовком. Повтор
    даёт те же байты. Без начала — документ как есть: дату модели не трогаем,
    если своей нет."""
    if not began_at:
        return doc
    doc, found = _strip_date(doc)
    lines = doc.split("\n")
    at = found if found is not None else _under_title(lines) if at is None else min(at, len(lines))
    line = f"{DATE_LABEL.get(lang, DATE_LABEL['ru'])} {began_at}"
    return "\n".join(lines[:at] + [line] + lines[at:])


def with_note(doc: str, note: str | None) -> str:
    """Механически дописать оговорку в конец документа: поручение модели
    «отметь пропуск в Рисках» непроверяемо, а протокол без строки внешне
    неотличим от полного (критика GLM входного круга). Уже есть — не дублировать."""
    # дедуп — по каноническому тексту, не по маркеру: модель, пересказавшая
    # оговорку своими словами с тем же значком, не должна отменять машинную
    # строку с интервалами и суммой (критика DS выходного круга)
    if not note or note in doc:
        return doc
    return doc.rstrip() + "\n\n" + note + "\n"


def without_note(doc: str, note: str | None) -> str:
    """Снять машинную строку оговорки (ровно её текст, не пересказ модели).
    Вход минуток снимает её до шагов, чтобы повторный проход по готовому
    документу ставил сноску и оговорку в тот же порядок и давал те же байты."""
    if not note or note not in doc:
        return doc
    return "\n".join(x for x in doc.split("\n") if x != note)


def finish_minutes(doc: str, source: MeetingSource, *, owner: str,
                   participants: set[str], lang: str = "ru") -> str:
    """Механические шаги минуток после модели — один вход для всех путей
    записи (черновик и «Протокол» демона, MCP, пересборка). Каждый путь
    держал свою цепочку: черновик и MCP шли без сверки фактов, пересборка
    без скраба пути машины, порядок шагов расходился (№732).

    Порядок — контракт: поле даты модели снимается первым (№738: дата —
    машинный факт, не утверждение модели; сверка не должна ни ругаться на
    выдуманную дату, которую код заменит, ни читать штамп «2026-11-03» как
    «11.03»), затем сверка со стенограммой по тому множеству, что видела
    модель (речь + оговорка), затем чекбоксы окна «Задачи», затем владелец
    одним написанием и пометка «не участник» (`finalize_assignees`), затем
    строка даты из штампа на место снятого поля (шаги между ними правят
    разделы и хвост, не шапку), затем строка оговорки, последней — скраб пути
    машины: хеш паспорта и отметка автотекста снимаются с этих байтов. Все
    шаги идемпотентны: повторный вызов по готовому документу его не меняет:
    прежняя оговорка и строка даты снимаются до шагов, прежнюю сноску
    снимает `annotate`. Начала нет (имя не наше) — поле модели не трогаем."""
    doc = without_note(doc, source.recording_note)
    at = None
    if source.began_at:
        doc, at = _strip_date(doc)
    doc = fact_check.annotate(doc, source.canon())
    doc = action_items.normalize(doc)
    doc = action_items.finalize_assignees(doc, participants, owner, lang=lang)
    doc = with_date(doc, source.began_at, lang, at=at)
    doc = with_note(doc, source.recording_note)
    doc, _n = privacy.scrub_local_paths(doc)
    return doc


def content_of(text: str) -> str:
    """Содержание встречи для ИЗВЛЕЧЕНИЯ (узлы графа, факты памяти): речь и
    живые тезисы контура, без следа записи в хвосте — строк события канала и
    итога «запись неполная». Факт о записи не тема встречи и не факт памяти
    (Important DS выходного круга по №317). Фильтр — только по хвосту
    «Ко-мышления»: границу речь/хвост владеет `transcript`, строка речи с
    похожим началом (правка руками) не трогается (Important DS круга 2)."""
    cut = text.find(transcript.NOTES_HEAD)
    if cut < 0:
        return text
    return text[:cut] + without_trace(text[cut:])


def without_trace(text: str) -> str:
    """Текст без строк следа записи — для документов, где речи нет (минутки в
    промпт): строка итога дописана в них механически и в извлечении читалась
    бы как тема встречи."""
    text = fact_check.without_footnote(text)
    kept = "\n".join(line for line in text.splitlines() if not channel_trace.is_trace_line(line))
    return kept + ("\n" if text.endswith("\n") else "")


def capped(text: str, limit: int, what: str = "документа") -> str:
    """Документ в промпт с потолком: голова и хвост с честной пометкой, как у
    `debrief_excerpt`. Потолок — свойство «документ в промпте», не места вызова
    (Minor GLM круга 2)."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n\n[… середина {what} опущена …]\n\n" + text[-half:]


def minutes_block(mpath: pathlib.Path, limit: int) -> str:
    """Минутки для промпта (извлечение графа, разбор): один хелпер ставит метку
    `[МИНУТКИ]`, снимает след записи (строка итога дописана в документ
    механически и в извлечении читалась бы как тема — Critical DS круга 2) и
    держит потолок. Файла нет — пусто."""
    if not mpath.exists():
        return ""
    body = capped(without_trace(mpath.read_text(encoding="utf-8")), limit, "минуток")
    return "[МИНУТКИ]\n" + body + "\n\n"


def note_in_tail(text: str) -> str | None:
    """Оговорка из уже записанного хвоста стенограммы (копия в архиве без
    сайдкара): последняя строка итога в хвосте «Ко-мышления», тем же правилом,
    каким её узнаёт `is_trace_line`. Последняя, не первая: пересборка
    дописывает свежий итог ниже строки демона (Minor DS и GLM круга 3)."""
    cut = text.find(transcript.NOTES_HEAD)
    if cut < 0:
        return None
    for line in reversed(text[cut:].splitlines()):
        body = channel_trace._bare_line(line)
        if body.startswith(channel_trace.SUMMARY_MARK):
            return body
    return None


class DebriefParts(NamedTuple):
    speech_limit: int
    cothinking_block: str
    minutes_block: str


def debrief_parts(mpath: pathlib.Path, cothinking: list[str], *, total: int, speech_min: int,
                  minutes_cap: int, cothinking_cap: int) -> DebriefParts:
    """Распределить один бюджет тела промпта разбора: речь не меньше
    `speech_min`, потом живые тезисы (до `cothinking_cap`), потом минутки —
    из остатка. Сумма блоков не превышает `total` по построению: пол у речи
    больше не четвёртый потолок поверх бюджета (Important DS и GLM круга 3)."""
    cothinking_block = (("<ко_мышление>\n" + capped("\n".join(cothinking), cothinking_cap, "тезисов")
                         + "\n</ко_мышление>\nЭто заметки модели по ходу встречи, не речь участников.\n\n")
                        if cothinking else "")
    minutes_limit = min(minutes_cap, max(0, total - speech_min - len(cothinking_block) - 80))
    block = minutes_block(mpath, minutes_limit) if minutes_limit >= 200 else ""
    speech_limit = max(speech_min, total - len(cothinking_block) - len(block))
    return DebriefParts(speech_limit, cothinking_block, block)
