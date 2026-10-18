#!/usr/bin/env python3
"""Пускать ли ночь на этом слоте: go, skip или defer <причина>.

Одна попытка в 04:15 теряла ночь целиком, если ноутбук в это время спал
закрытым: launchd пропущенный календарный запуск после пробуждения не
повторяет. Поэтому у ночи несколько слотов — вечерние 19:00, 20:00, 21:00 и
утренний 04:15, — а этот гейт не даёт второму слоту переделать ночь, которая
уже прошла успешно.

Чего здесь сознательно нет. «Идёт ли сейчас ночь» гейт не спрашивает: пока
агент работает, launchd вторую копию той же метки не запускает, а отложенного
старта после выхода не бывает (опыт 18.10). Занятость машины тоже не его
дело — её ждёт wait_for_idle внутри ночи, с потолком. Пять кругов ревью
подряд находили Critical ровно в таких проверках.

Решение:
  skip  — последняя ночь «ok» и закончилась после начала этой ночи;
  defer — вечерний слот, питание от батареи, заряд ниже порога: утренний
          слот ещё будет. Утренний слот — последний и не откладывается;
  go    — во всех остальных случаях, в том числе при любом сбое: лишний
          прогон дешевле пропущенной ночи.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

NIGHT_START_HOUR = 19      # первый вечерний слот: с него считается «эта ночь»
EVENING_FROM_HOUR = 12     # слоты после полудня — вечерние, до — утренний
LOW_BATTERY_PCT = 30


def night_start(now: dt.datetime) -> dt.datetime:
    """Начало ночи, к которой относится момент now: 19:00 сегодня после
    полудня, иначе 19:00 вчера."""
    day = now.date() if now.hour >= EVENING_FROM_HOUR else now.date() - dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(NIGHT_START_HOUR))


def parse_power(text: str) -> tuple[bool, int] | None:
    """Вывод `pmset -g batt` → (от батареи, заряд %); None, если не разобрать.
    Мак без батареи даёт строку без процентов — это «от сети»."""
    on_battery = "'Battery Power'" in text
    if not on_battery and "'AC Power'" not in text:
        return None
    m = re.search(r"(\d{1,3})%", text)
    if m is None:
        return (False, 100) if not on_battery else None
    return on_battery, int(m.group(1))


def read_power() -> tuple[bool, int] | None:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                             text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_power(out)


def decide(now: dt.datetime, status: dict | None,
           power: tuple[bool, int] | None) -> str:
    """Чистое решение гейта; сбои чтения приходят сюда как None."""
    if isinstance(status, dict) and status.get("state") == "ok":
        try:
            finished = dt.datetime.strptime(str(status.get("finished")), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            finished = None
        if finished is not None and finished >= night_start(now):
            return "skip"
    if now.hour >= EVENING_FROM_HOUR and power is not None:
        on_battery, pct = power
        if on_battery and pct < LOW_BATTERY_PCT:
            return f"defer battery {pct}%"
    return "go"


def read_status(root: pathlib.Path) -> dict | None:
    try:
        data = json.loads((root / "logs" / "nightly.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["decide"])
    ap.add_argument("--root", required=True, help="корень данных (там logs/nightly.json)")
    args = ap.parse_args(argv)
    print(decide(dt.datetime.now(), read_status(pathlib.Path(args.root)), read_power()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
