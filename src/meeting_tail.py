"""Конец встречи по звонку: что записано после него, в стенограмму не идёт (№733).

Запись живёт дольше звонка: собеседники отключились, а микрофон пишет комнату —
разговор с семьёй, диктовку ассистенту, коллегу рядом. Пересборка писала это в
стенограмму под подписью владельца, и хвост уходил в протокол, разбор и граф.
Замер по октябрю (23.10): 21 встреча со звонком и репликами микрофона после
последней реплики собеседников — во всех 21 это разговор после звонка. Пауза до
него 0–13 минут, прощание узнаётся в 3 из 21: ни порог тишины, ни прощание
конец не отделяют, а канал собеседников — отделяет.

Правило: конец звонка — конец последнего отрезка стороны звонка; отрезок
микрофона, начатый после него, — запись после звонка. Отрезок, начатый раньше,
остаётся во встрече целиком: ответ поверх последних слов собеседника не режется
посреди слова.

Хвоста нет, когда конец звонка не доказан:
- не звонок (комната, канал собеседников молчал) — сторону звонка нечем мерить;
- канал собеседников сомнителен (`doubt`): отрезки без опоры при разметке не
  Nemotron или потеря канала, не вернувшаяся до конца, — собеседники могли
  звучать в микрофоне под подписью владельца (отказ гарнитуры 06.10: 818 с нулей
  до конца записи);
- последний отрезок стороны звонка — голос, переподписанный из-за отрезков без
  опоры, или конец лежит внутри такого отрезка: канал в этот момент мёртв, и
  «последняя реплика собеседников» — не конец звонка;
- у метки нет стороны в `chan` — граница была бы догадкой.
Нули канала собеседников до конца записи признаком не служат: они есть и у
настоящих хвостов (2 из 2 замеренных), и у отказа гарнитуры.
"""
from __future__ import annotations

from typing import Collection, NamedTuple

Segment = tuple[float, float, str]


class Split(NamedTuple):
    meeting: list[Segment]
    after: list[Segment]
    end: float | None        # конец звонка, если хвост есть
    why: str                 # строка журнала: почему хвоста нет или где конец


def split(segments: list[Segment], chan: dict[str, str], *, call: bool,
          unsupported: Collection[tuple[float, float]] = (),
          unsupported_labels: Collection[str] = (), doubt: str = "") -> Split:
    """Разделить отрезки на встречу и запись после звонка.

    `segments` и `chan` — выход `resolve_channel_segments` (метка → "mic"|"bh"),
    `unsupported` — отрезки без опоры, `unsupported_labels` — метки, которые
    переподписаны из-за них, `doubt` — причина не верить каналу собеседников
    (пусто — верим). Порядок отрезков внутри сторон сохраняется."""
    whole = Split(list(segments), [], None, "")
    if not call:
        return whole._replace(why="не звонок")
    if doubt:
        return whole._replace(why=doubt)
    unknown = sorted({spk for _, _, spk in segments if spk not in chan})
    if unknown:
        return whole._replace(why=f"метки без канала: {', '.join(unknown)}")
    relabeled = set(unsupported_labels)
    side = [(e, spk) for _, e, spk in segments if chan[spk] == "bh" or spk in relabeled]
    if not side:
        return whole._replace(why="нет реплик собеседников")
    end, last = max(side)
    if last in relabeled:
        return whole._replace(why="последний голос звонка — из отрезка без опоры")
    if any(s <= end <= e for s, e in unsupported):
        return whole._replace(why="конец звонка внутри отрезка без опоры")
    # Переподписанные голоса кончаются не позже `end` (он максимум по ним),
    # поэтому после конца звонка лежит только микрофон.
    is_after = [s >= end and chan[spk] == "mic" for s, _, spk in segments]
    after = [seg for seg, a in zip(segments, is_after) if a]
    if not after:
        return whole._replace(why="после звонка микрофон молчал")
    meeting = [seg for seg, a in zip(segments, is_after) if not a]
    return Split(meeting, after, end, f"конец звонка {end:.0f} с")
