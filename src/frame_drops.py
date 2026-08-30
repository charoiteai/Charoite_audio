"""Счётчик потерянных кадров быстрого триггера — тихая деградация вслух.

`fast_trigger_loop` кладёт кадры системного канала в очередь стрима gigastt
и при переполнении молча роняет их: под CPU-голоданием (RTF стрима > 1)
детект вопросов угасал без единого признака, а статус «стрим подключён»
оставался правдивым (аудит 30.08, DS I2). Метр считает дропы в окне и
отдаёт сообщение раз на окно, когда их больше порога — вызывающий шлёт
его как ошибку статуса; сам метр ничего не эмитит и без часов работает.
"""
from __future__ import annotations

import time
from typing import Callable

WINDOW_S = 60.0
THRESHOLD = 50          # ~50 кадров по 100 мс = 5 с звука за минуту


class DropMeter:
    def __init__(self, *, window_s: float = WINDOW_S, threshold: int = THRESHOLD,
                 now: Callable[[], float] = time.monotonic):
        self.window_s = window_s
        self.threshold = threshold
        self._now = now
        self._start = now()
        self._count = 0
        self._reported = False
        self.total = 0

    def dropped(self) -> str | None:
        """Ещё один кадр потерян. Строка — когда пора сказать об этом вслух."""
        t = self._now()
        if t - self._start >= self.window_s:
            self._start, self._count, self._reported = t, 0, False
        self._count += 1
        self.total += 1
        if self._count >= self.threshold and not self._reported:
            self._reported = True
            return (f"⚡ быстрый триггер отстаёт: потеряно {self._count} кадров за "
                    f"{int(self.window_s)} с — стрим не успевает, детект вопросов деградирует")
        return None
