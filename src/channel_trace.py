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
from collections import Counter
from typing import Callable

import live_sidecar

SIDECAR_KEY = "channel_events"
LINES_MAX = 6            # строк на канал в хвост/нить за запись; дальше — итог
NAMES = {"blackhole": "системный звук (собеседники)", "mic": "ваш микрофон"}
ABSENT = {"blackhole": "без собеседников", "mic": "без вашего голоса"}


def _hm(ts: float | None) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "?"


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
        return f"⚠️ {name} пропал в {_hm(ev.stopped_at or ev.at)} — дальше запись {absent}"
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
        self._lines: Counter[str] = Counter()
        self.persist_failed = False

    def on_event(self, ev) -> None:
        self.events.append(ev.as_dict())
        self._persist()
        text = render(ev)
        if text is None:
            return
        if self._lines[ev.label] >= LINES_MAX:
            return                       # флап: дальше только сайдкар и итог
        self._lines[ev.label] += 1
        self._say(text, _hm(ev.at))

    def _persist(self) -> None:
        # ключ пишется целиком на каждое событие: список короткий, а крах между
        # событиями не теряет уже записанного (M3 DS входного круга)
        try:
            ok = live_sidecar.remember(self.live, SIDECAR_KEY,
                                       json.dumps(self.events, ensure_ascii=False), self.bare)
        except Exception:  # noqa: BLE001 — след не роняет запись
            ok = False
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

    def episodes(self) -> list[tuple[str, float | None, float | None, bool]]:
        """Эпизоды (label, start, end, closed) из событий: lost открывает,
        back/end закрывают, gap — сразу закрытый. Смена фазы того же эпизода
        событием не является (хаб не эмитит), второго lost подряд не будет."""
        out: list[tuple[str, float | None, float | None, bool]] = []
        open_: dict[str, int] = {}
        for e in self.events:
            label, kind = e["label"], e["kind"]
            if kind == "lost":
                if label in open_:
                    continue
                open_[label] = len(out)
                out.append((label, e["stopped_at"] or e["at"], None, False))
            elif kind in ("back", "end"):
                i = open_.pop(label, None)
                if i is None:
                    out.append((label, e["stopped_at"], e["at"], kind == "back"))
                else:
                    lbl, start, _, _ = out[i]
                    out[i] = (lbl, start, e["at"], kind == "back")
            elif kind == "gap":
                out.append((label, e["stopped_at"], e["at"], True))
        return out

    def summary(self) -> str | None:
        """Одна строка итога: интервалы без канала и их сумма; открытый эпизод
        — «до конца записи». Нет эпизодов — None."""
        eps = self.episodes()
        if not eps:
            return None
        parts = []
        for label in dict.fromkeys(e[0] for e in eps):
            mine = [e for e in eps if e[0] == label]
            spans, total = [], 0.0
            for _, start, end, _closed in mine:
                if start is not None and end is not None:
                    total += max(0.0, end - start)
                spans.append(f"{_hm(start)}–{_hm(end) if end is not None else 'до конца записи'}")
            shown = ", ".join(spans[:8]) + (f" и ещё {len(spans) - 8}" if len(spans) > 8 else "")
            parts.append(f"{ABSENT.get(label, label)} {shown} (эпизодов {len(mine)}, всего {_dur(total)})")
        return "📋 запись неполная: " + "; ".join(parts)

    def close(self) -> str | None:
        """Итог при остановке — в хвост и нить, один раз. Зовётся из демона ДО
        спавна пересборки: хвост переносится regex-ом, и строка должна лежать в
        файле к тому моменту (Important GLM входного круга)."""
        text = self.summary()
        if text:
            self._say(text, "")
        return text
