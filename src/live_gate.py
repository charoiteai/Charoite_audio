"""Живая встреча важнее фона: один общий признак «суфлёр сейчас слушает».

Факт 18.08: пересборка 18-часовой записи держала тяжёлую модель промптами по
12-13 тыс. токенов, а Ollama с MLX-раннером на занятой модели отвечает
503 за четверть секунды вместо очереди — подсказки живой встречи 45 минут
подряд падали с `[LLM: 503 …]` прямо в панель. Ни пересборка, ни ночной
цикл, ни разбор графа не знали, что рядом идёт встреча.

Признак — лок демона `logs/daemon.lock`: демон живёт ровно столько, сколько
идёт запись (приложение запускает `daemon.py` на «Старт» и гасит на «Стоп»),
лок берётся при старте и отпускается смертью процесса. Фоновые процессы
проверяют его неблокирующим flock: лок держится — встреча идёт, фон ждёт.

Гейт — пауза, а не отказ: работа продолжается с того же места, как только
лок отпущен. Потолок ожидания — по вкусу вызывающего: пересборка ждёт
сколько угодно (её никто не ждёт), ночной цикл — с потолком, чтобы утренняя
встреча не сорвала всю ночь.
"""
from __future__ import annotations

import fcntl
import pathlib
import time
from typing import Callable

LOCK_NAME = "daemon.lock"


def lock_path(root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(root) / "logs" / LOCK_NAME


def daemon_alive(root: pathlib.Path) -> bool:
    """Держит ли кто-то лок демона — то есть идёт ли сейчас живая встреча.

    «Встреча идёт» — только когда flock честно отказал ИЗ-ЗА ЧУЖОГО лока
    (BlockingIOError). Нет файла, нет прав, том без flock (SMB/NFS) — судить
    не по чему, и фон вставать не должен: ревью 18.08 ×2 — `except OSError:
    return True` превращал права 0400 или сетевой корень в вечное «уступаю».
    Проверяем разделяемым локом (LOCK_SH): с эксклюзивным локом демона он
    конфликтует, с такими же проверяющими — нет.
    """
    try:
        f = lock_path(root).open("r")   # flock не требует записи
    except OSError:
        return False
    with f:
        try:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True       # лок держит демон — встреча идёт
        except OSError:
            return False      # flock не поддержан — не блокируем фон
        fcntl.flock(f, fcntl.LOCK_UN)
        return False          # взяли — демона нет


def wait_while_live(root: pathlib.Path, log: Callable[[str], None] = print, *,
                    what: str = "фон", poll: float = 20.0, cap: float | None = None,
                    sleep=time.sleep, now=time.monotonic,
                    alive: Callable[[pathlib.Path], bool] | None = None) -> bool:
    """Подождать, пока живая встреча закончится. Возвращает True, если ждали.

    cap=None — ждать сколько понадобится; число — потолок в секундах, после
    которого идём работать в тесноте (об этом говорит лог, не код возврата).
    """
    is_alive = alive or daemon_alive
    if not is_alive(root):
        return False
    started = now()
    log(f"{what}: идёт живая встреча — уступаю модель, жду"
        + (f" до {int(cap // 60)} мин" if cap else ""))
    while is_alive(root):
        if cap is not None and now() - started >= cap:
            log(f"{what}: встреча всё ещё идёт после {int(cap // 60)} мин — иду работать в тесноте")
            return True
        sleep(poll)
    log(f"{what}: встреча закончилась — продолжаю (ждал {int((now() - started) // 60)} мин)")
    return True
