#!/usr/bin/env python3
"""Ждём, пока машина освободится от разбора встреч.

Ночной цикл и разбор встречи делают одно и то же — гоняют локальную модель, —
и на одной машине не помещаются. 12.08 они совпали: транскрипция, ревизия
ядер и сборка досье одновременно, свободной памяти 14 ГБ из 64 при 17 ГБ уже
в компрессоре. Сервер начал выгружать и грузить модели по кругу (41 раз за
прогон), запросы стали висеть по 2-6 минут, а в 11:22 он лёг совсем — 258
тем ушли без разбора, и ночь при этом отчиталась как успешная.

Живая запись — тоже занятость (18.08): суфлёр слушает, а фон держит модель —
подсказки встречи падают. Пока лок демона занят, ждём наравне с разбором.

Ждём с потолком: пропустить ночь целиком хуже, чем поработать в тесноте.
Дождались или нет — код возврата нулевой, а сказать об этом должен лог.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import busy_signals  # noqa: E402
import live_gate  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

LIVE = "живая запись"
MUTATION = "мутация тестов"


def busy_now(store: MeetingStatusStore, root: pathlib.Path | None = None) -> list[str]:
    """Чем занята машина: разбор встреч, живая запись, мутация тестов.

    Мутатор добавлен после ночи 23→24.08: он делил модель с досье — 35
    ReadTimeout по 300 с; теперь его лок ночь ждёт наравне с разбором.
    """
    busy = list(store.busy())
    if root is not None and live_gate.daemon_alive(root):
        busy.append(LIVE)
    if root is not None and busy_signals.mutation_running(root):
        busy.append(MUTATION)
    return busy


def wait(store: MeetingStatusStore, *, timeout: float, poll: float,
         sleep=time.sleep, now=time.monotonic,
         root: pathlib.Path | None = None) -> list[str]:
    """Ждать освобождения; вернуть то, что осталось занятым к концу срока."""
    deadline = now() + timeout
    while True:
        busy = busy_now(store, root)
        if not busy:
            return []
        if now() >= deadline:
            return busy
        # Ждём не дольше, чем осталось: иначе последний сон перелетал бы
        # через дедлайн и превращал час ожидания в час с четвертью.
        sleep(min(poll, max(0.0, deadline - now())))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # корень ДАННЫХ: лок демона и статусы живут там, а не рядом с кодом
    # (вложенная установка: CHAROITE_ROOT указывает на папку данных)
    ap.add_argument("--root", default=os.environ.get("CHAROITE_ROOT") or ".",
                    help="корень данных с logs/ (по умолчанию $CHAROITE_ROOT или .)")
    ap.add_argument("--timeout", type=float, default=3600,
                    help="сколько ждать, секунд (0 — не ждать)")
    ap.add_argument("--poll", type=float, default=60)
    a = ap.parse_args()

    root = pathlib.Path(a.root).expanduser()
    store = MeetingStatusStore(root)
    busy = busy_now(store, root)
    if not busy:
        return 0

    print(f"машина занята ({', '.join(busy)}) — жду до "
          f"{int(a.timeout // 60)} мин, чтобы не делить память и модель")
    left = wait(store, timeout=a.timeout, poll=a.poll, root=root)
    if left:
        print(f"⚠️ не дождался ({', '.join(left)}) — иду работать в тесноте")
    else:
        print("машина освободилась")
    return 0


if __name__ == "__main__":
    sys.exit(main())
