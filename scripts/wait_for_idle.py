#!/usr/bin/env python3
"""Ждём, пока машина освободится от разбора встреч.

Ночной цикл и разбор встречи делают одно и то же — гоняют локальную модель, —
и на одной машине не помещаются. 12.08 они совпали: транскрипция, ревизия
ядер и сборка досье одновременно, свободной памяти 14 ГБ из 64 при 17 ГБ уже
в компрессоре. Сервер начал выгружать и грузить модели по кругу (41 раз за
прогон), запросы стали висеть по 2-6 минут, а в 11:22 он лёг совсем — 258
тем ушли без разбора, и ночь при этом отчиталась как успешная.

Ждём с потолком: пропустить ночь целиком хуже, чем поработать в тесноте.
Дождались или нет — код возврата нулевой, а сказать об этом должен лог.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from meeting_processing import MeetingStatusStore  # noqa: E402


def wait(store: MeetingStatusStore, *, timeout: float, poll: float,
         sleep=time.sleep, now=time.monotonic) -> list[str]:
    """Ждать освобождения; вернуть то, что осталось занятым к концу срока."""
    deadline = now() + timeout
    while True:
        busy = store.busy()
        if not busy:
            return []
        if now() >= deadline:
            return busy
        # Ждём не дольше, чем осталось: иначе последний сон перелетал бы
        # через дедлайн и превращал час ожидания в час с четвертью.
        sleep(min(poll, max(0.0, deadline - now())))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="корень с logs/meeting-status")
    ap.add_argument("--timeout", type=float, default=3600,
                    help="сколько ждать, секунд (0 — не ждать)")
    ap.add_argument("--poll", type=float, default=60)
    a = ap.parse_args()

    store = MeetingStatusStore(pathlib.Path(a.root).expanduser())
    busy = store.busy()
    if not busy:
        return 0

    print(f"разбор встречи идёт ({', '.join(busy)}) — жду до "
          f"{int(a.timeout // 60)} мин, чтобы не делить память")
    left = wait(store, timeout=a.timeout, poll=a.poll)
    if left:
        print(f"⚠️ не дождался ({', '.join(left)}) — иду работать в тесноте")
    else:
        print("машина освободилась")
    return 0


if __name__ == "__main__":
    sys.exit(main())
