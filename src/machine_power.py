"""Режим энергосбережения macOS — датчик справки демона (№783).

В режиме энергосбережения локальная модель подсказок работает примерно вдвое
медленнее: 28–31 ток/с против 57–70 на той же машине, p50 подсказки 14 с
против 7 с (замер №583, 22–23.10). Человек видит «медленные подсказки» и не
видит причину — демон говорит её справкой на встречу (`stt_runtime.PowerNotice`).

Читаем `pmset -g`: он печатает настройки АКТИВНОГО источника питания, поэтому
выдернутый шнур посреди встречи меняет ответ сам. Значение `powermode` на
Apple Silicon: 1 — энергосбережение. Ключ `lowpowermode` старых Intel-маков
не разбираем: продукт только для Apple Silicon (README). `pmset -g batt`
ночного гейта (`scripts/night_gate.py`) — другой вывод (источник и заряд),
не этот.
"""
from __future__ import annotations

import re
import subprocess
import sys

PMSET = "/usr/bin/pmset"
LOW_POWER_MODE = 1
TIMEOUT_S = 2.0

# Шапка, по которой вывод признаём выводом `pmset -g`: без неё пустой или чужой
# текст читался бы как «режим обычный» и снимал бы справку (DS I1 входного круга).
_HEADER = "System-wide power settings"
# Якорь по строке целиком: подстрока `powermode` сидит и в `lowpowermode`
# (Sonnet I2 входного круга).
_POWERMODE = re.compile(r"^[ \t]*powermode[ \t]+(\d+)[ \t]*$", re.M)


def parse_low_power(text: str) -> bool | None:
    """Вывод `pmset -g` → включено ли энергосбережение.

    None — вывод не узнан (нет шапки) или противоречив (две строки `powermode`
    с разными значениями): слой справки не трогаем. Узнанный вывод без строки
    `powermode` — не энергосбережение: профиль источника без ключа не должен
    оставлять поднятую справку висеть до конца встречи (DS I1 входного круга).
    """
    if not isinstance(text, str) or _HEADER not in text:
        return None
    values = {int(m) for m in _POWERMODE.findall(text)}
    if len(values) > 1:
        return None
    return values == {LOW_POWER_MODE}


def read_low_power(run=subprocess.run) -> bool | None:
    """Режим активного источника питания сейчас; None — не macOS или сбой.

    Демон зовёт это на старте записи и на каждом heartbeat (раз в 30 с):
    `pmset -g` — около 10 мс. stdin закрыт: у демона на stdin командная труба
    приложения, ребёнок её наследовать не должен (DS M1 входного круга).
    """
    if sys.platform != "darwin":
        return None
    try:
        res = run([PMSET, "-g"], capture_output=True, text=True, timeout=TIMEOUT_S,
                  stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if res.returncode != 0:
        return None
    return parse_low_power(res.stdout)
