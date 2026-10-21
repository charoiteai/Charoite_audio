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
    Формат ГГГГ-ММ-ДД, не ДД.ММ: сверка фактов строку не видит (вход ставит её
    после сверки), но читатели файла (граф, разбор) видят её в тексте как есть.
    None — имя не наше: шапку тогда не трогаем."""
    moment = meeting_stamp.meeting_start(live.stem)
    if moment is None:
        return None
    if transcript_origin.SIDECAR_KEY in (live_sidecar.read_direct(live) or {}):
        return f"{moment:%Y-%m-%d}"
    return f"{moment:%Y-%m-%d %H:%M}"


#: подпись поля даты, которую пишет код, по языку минуток
DATE_LABEL = {"ru": "**Дата/время:**", "en": "**Date/time:**", "zh": "**日期/时间：**"}
# Поле даты модели не снимаем: четыре круга подряд (выход №738 out1–out4) снятие
# по границе шапки теряло текст модели — то поле участников, то пункт решения.
# Строка кода встаёт сверху; худший случай — дата модели ниже верной. Модель
# дату больше не просят (встроенные шаблоны), старые минутки — мигратор (№615).
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]")
_COMMENT = re.compile(r"^[ \t]*<!--.*-->[ \t]*$")


def _without_own_date(doc: str, began_at: str) -> str:
    """Снять строку даты, которую пишет вход, — ровно её текст на любом языке,
    не поле модели: повтор даёт те же байты, а сверка фактов не читает штамп
    «2026-11-03» как «11.03»."""
    own = {f"{label} {began_at}" for label in DATE_LABEL.values()}
    return "\n".join(x for x in doc.split("\n") if x not in own)


def with_date(doc: str, began_at: str | None, lang: str = "ru") -> str:
    """Строка «Дата/время» от кода: под заголовком, если первая строка
    содержания — «#», иначе перед ней (комментарии над ней не трогаем). Без
    начала — документ как есть."""
    if not began_at:
        return doc
    lines = _without_own_date(doc, began_at).split("\n")
    at = 0
    for j, x in enumerate(lines):
        if x.strip() and not _COMMENT.match(x):
            at = j + 1 if _HEADING.match(x) else j
            break
    return "\n".join(lines[:at] + [f"{DATE_LABEL.get(lang, DATE_LABEL['ru'])} {began_at}"] + lines[at:])


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

    Порядок — контракт: сверка со стенограммой по тому множеству, что видела
    модель (речь + оговорка), затем чекбоксы окна «Задачи», затем владелец
    одним написанием и пометка «не участник» (`finalize_assignees`), затем
    строка даты из штампа (№738: дата — машинный факт, не утверждение модели;
    ставится после сверки, чтобы та не читала штамп «2026-11-03» как «11.03»),
    затем строка оговорки, последней — скраб пути машины: хеш паспорта и
    отметка автотекста снимаются с этих байтов. Все шаги идемпотентны:
    повторный вызов по готовому документу его не меняет: прежние оговорка и
    строка даты кода снимаются до шагов, прежнюю сноску снимает `annotate`."""
    doc = without_note(doc, source.recording_note)
    if source.began_at:
        doc = _without_own_date(doc, source.began_at)
    doc = fact_check.annotate(doc, source.canon())
    doc = action_items.normalize(doc)
    doc = action_items.finalize_assignees(doc, participants, owner, lang=lang)
    doc = with_date(doc, source.began_at, lang)
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
