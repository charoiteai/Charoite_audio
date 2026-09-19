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
  оговорки при появившейся оговорке становятся STALE ровно один раз.

Входной круг DS и GLM (19.09) сошёлся на этом объекте: «файл как вход не
существует, есть то, что владелец отдал как речь, и то, что он же пометил как
факт о записи».
"""
from __future__ import annotations

import dataclasses
import pathlib

import channel_trace
import live_sidecar
import transcript


@dataclasses.dataclass(frozen=True)
class MeetingSource:
    speech: str
    recording_note: str | None = None

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
    return MeetingSource(transcript.speech_of(text), channel_trace.recording_note(live))


def content_of(text: str) -> str:
    """Содержание встречи для ИЗВЛЕЧЕНИЯ (узлы графа, факты памяти): речь и
    живые тезисы контура, без следа записи в хвосте — строк события канала и
    итога «запись неполная». Факт о записи не тема встречи и не факт памяти
    (Important DS выходного круга по №317)."""
    return "\n".join(line for line in text.splitlines()
                     if not channel_trace.is_trace_line(line)) + ("\n" if text.endswith("\n") else "")


def live(speech: str, note: str | None) -> MeetingSource:
    """Источник живого пути: речь из `Transcript.full()`, оговорка из
    `ChannelTrace.summary()` того же процесса."""
    return MeetingSource(speech, note)


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
