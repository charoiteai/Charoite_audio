"""След события канала в документах встречи (№234).

Хаб отдаёт структурное событие `ChannelEvent` (пропал / вернулся / дыра / не
вернулся до конца) из одной точки; здесь — единственный владелец следа:

- **сайдкар** `.md.live.json`, ключ `channel_events` (JSON-список событий) —
  долговечная запись; пишется в момент события через `live_sidecar.remember`
  (слияние, не дамп), читатели — пересборка, архив, будущие потребители;
- **хвост «Ко-мышления» стенограммы** (`Transcript.note`) — человеческая строка
  с истинными границами; хвост не входит в хеш речи производных
  (`transcript.speech_of`), переживает пересборку (переносится regex-ом) и не
  попадает в тезисы архива (у него свой знак);
- **нить встречи** (`Thread.add_system`) — та же строка на экране, без дедупа.

Свёртки по порогу длительности нет: короткий по отчёту эпизод — это реальная
дыра ≥ порога сторожа (критика DS и GLM входного круга). Свёртка — по счёту:
после `LINES_MAX` строк на канал в хвост и нить идёт только итог; события в
сайдкаре — все. Итог при остановке (`close`) закрывает картину: интервалы без
канала и их сумма, открытые эпизоды — «до конца записи».
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from collections import Counter
from typing import Callable, NamedTuple

import live_sidecar

SIDECAR_KEY = "channel_events"
LINES_MAX = 6            # ЭПИЗОДОВ на канал в хвост/нить за запись; дальше — итог
#                          (счётчик по событиям съедал лимит за три флап-цикла —
#                          Minor GLM и DS выходного круга)


class Episode(NamedTuple):
    label: str
    start: float | None
    end: float | None            # None — открыт (канал не вернулся, записи ещё идёт)
    revived: bool                # True — канал вернулся (back/gap); False — закрыт остановкой
NAMES = {"blackhole": "системный звук (собеседники)", "mic": "ваш микрофон"}
ABSENT = {"blackhole": "без собеседников", "mic": "без вашего голоса"}


def _hm(ts: float | None) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "время неизвестно"


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"около {seconds} с"
    return f"{seconds // 60} мин"


def render(ev) -> str | None:
    """Человеческая строка события; None — строки нет (закрытие эпизода идёт
    в итог). Формулировка — по каналу и фазе: «пропал» только о канале, который
    жил (Important GLM входного круга)."""
    name = NAMES.get(ev.label, ev.label)
    absent = ABSENT.get(ev.label, "без канала")
    if ev.kind == "lost":
        if not ev.died:
            return f"⚠️ {name} не захвачен с начала записи — {absent}: {ev.reason}"
        # момент крика подставлять вместо границы нельзя — он позже на 66–96 с;
        # без кадров граница неизвестна и так и говорится (Minor DS)
        when = _hm(ev.stopped_at) if ev.stopped_at else "время неизвестно"
        return f"⚠️ {name} пропал ({when}) — дальше запись {absent}"
    if ev.kind == "back":
        return f"✅ {name} снова пишется с {_hm(ev.at)}; {absent} было {_dur(ev.silent_s)}"
    if ev.kind == "gap":
        return (f"⚠️ {name}: пробел в записи {_hm(ev.stopped_at)}–{_hm(ev.at)} "
                f"({_dur(ev.silent_s)}), поток перезапущен")
    return None


class ChannelTrace:
    """Один владелец следа на запись. `note` — `Transcript.note`, `thread_add` —
    `Thread.add_system(text, at)`; обе подстановки — необязательны."""

    def __init__(self, live: pathlib.Path, note: Callable[[str], None] | None = None,
                 thread_add: Callable[[str, str], bool] | None = None,
                 bare: str | None = None) -> None:
        self.live = live
        self.bare = bare                 # посекундный штамп — сайдкар без угадывания (как у ретитла)
        self._note = note
        self._thread_add = thread_add
        self.events: list[dict] = []
        self._episodes_shown: Counter[str] = Counter()   # эпизодов на канал уже в документах
        self._open_shown: set[str] = set()               # открытые эпизоды, чей lost показан
        self.persist_failed = False

    def on_event(self, ev) -> None:
        self.events.append(ev.as_dict())
        self._persist()
        text = render(ev)
        if text is None:
            return
        # потолок — по эпизодам: `back` показывается, если показан его `lost`;
        # начало нового эпизода (lost/gap) сверх потолка — только сайдкар и итог
        if ev.kind == "back":
            if ev.label not in self._open_shown:
                return
            self._open_shown.discard(ev.label)
        else:
            if self._episodes_shown[ev.label] >= LINES_MAX:
                return
            self._episodes_shown[ev.label] += 1
            if ev.kind == "lost":
                self._open_shown.add(ev.label)
        self._say(text, _hm(ev.at))

    def _persist(self) -> None:
        # ключ пишется целиком на каждое событие: список короткий, а крах между
        # событиями не теряет уже записанного (M3 DS входного круга)
        try:
            ok = live_sidecar.remember(self.live, SIDECAR_KEY,
                                       json.dumps(self.events, ensure_ascii=False), self.bare)
        except Exception:  # noqa: BLE001 — след не роняет запись
            ok = False
        if not ok and not self.persist_failed:
            # отказ долговечной записи — не молча: сайдкар и есть носитель следа
            # для пересборки; итог тоже скажет об этом (Important GLM выходного круга)
            print(f"след канала: сайдкар {self.live.name} не пишется — события только в "
                  "хвосте и нити", file=sys.stderr)
        if not ok:
            self.persist_failed = True

    def _say(self, text: str, at: str) -> None:
        for fn, args in ((self._note, (text,)), (self._thread_add, (text, at))):
            if fn is None:
                continue
            try:
                fn(*args)
            except Exception:  # noqa: BLE001
                pass

    def episodes(self) -> list[Episode]:
        """Эпизоды из событий: lost открывает, back/end закрывают, gap — сразу
        закрытый. Смена фазы того же эпизода событием не является (хаб не эмитит).
        Окна одного канала, наложившиеся друг на друга (дыра, отсчитанная от
        одного и того же настоящего кадра несколькими gap), сливаются в одно —
        иначе сумма считала бы одну тишину дважды (Important DS выходного круга)."""
        out: list[Episode] = []
        open_: dict[str, int] = {}
        for e in self.events:
            label, kind = e["label"], e["kind"]
            if kind == "lost":
                if label in open_:
                    continue
                open_[label] = len(out)
                out.append(Episode(label, e["stopped_at"] or e["at"], None, False))
            elif kind in ("back", "end"):
                i = open_.pop(label, None)
                if i is None:
                    out.append(Episode(label, e["stopped_at"], e["at"], kind == "back"))
                else:
                    out[i] = out[i]._replace(end=e["at"], revived=kind == "back")
            elif kind == "gap":
                out.append(Episode(label, e["stopped_at"], e["at"], True))
        return _merge_windows(out)

    def summary(self) -> str | None:
        """Одна строка итога: интервалы без канала и их сумма; открытый эпизод
        — «до конца записи». Нет эпизодов — None."""
        eps = self.episodes()
        if not eps:
            return None
        parts = []
        for label in dict.fromkeys(e.label for e in eps):
            mine = [e for e in eps if e.label == label]
            spans, total = [], 0.0
            for ep in mine:
                if ep.start is not None and ep.end is not None:
                    total += max(0.0, ep.end - ep.start)
                spans.append(f"{_hm(ep.start)}–{_hm(ep.end) if ep.end is not None else 'до конца записи'}")
            shown = ", ".join(spans[:8]) + (f" и ещё {len(spans) - 8}" if len(spans) > 8 else "")
            parts.append(f"{ABSENT.get(label, label)} {shown} (эпизодов {len(mine)}, всего {_dur(total)})")
        text = "📋 запись неполная: " + "; ".join(parts)
        if self.persist_failed:
            text += " · в сайдкар след не лёг"
        return text

    def close(self) -> str | None:
        """Итог при остановке — в хвост и нить, один раз. Зовётся из демона ДО
        спавна пересборки: хвост переносится regex-ом, и строка должна лежать в
        файле к тому моменту (Important GLM входного круга)."""
        text = self.summary()
        if text:
            self._say(text, "")
        return text


def _merge_windows(eps: list[Episode]) -> list[Episode]:
    """Слить наложившиеся окна одного канала (порядок событий сохраняется)."""
    out: list[Episode] = []
    for ep in eps:
        prev = next((o for o in reversed(out) if o.label == ep.label), None)
        if (prev is not None and ep.start is not None and prev.start is not None
                and prev.end is not None and ep.start <= prev.end):
            merged = prev._replace(end=None if ep.end is None else max(prev.end, ep.end),
                                   revived=ep.revived)
            out[out.index(prev)] = merged
            continue
        out.append(ep)
    return out
