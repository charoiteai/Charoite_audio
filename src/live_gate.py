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

import math
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import file_locks  # noqa: E402
import time
from typing import Callable

LOCK_NAME = "daemon.lock"

#: До какого момента (unix-время) ночному прогону разрешено работать.
#: Выставляет `scripts/nightly.sh`; пусто — потолка нет.
NIGHTLY_UNTIL_ENV = "CHAROITE_NIGHTLY_UNTIL"


def night_is_over(now: Callable[[], float] = time.time) -> bool:
    """Вышло ли время, отведённое ночному прогону.

    Ночная работа обязана кончаться ночью. 21.08 прогон стартовал в 04:16 и
    в 11:36 всё ещё держал машину: `wait_for_idle` спрашивают один раз на
    старте, когда всё свободно, а дальше семь часов никто не смотрит на
    часы. Шаги идут по темам, и прерваться между ними ничего не стоит:
    несделанное соберётся следующей ночью, а недобранная встреча — нет.

    Пауза на живую запись (`wait_while_live`) сюда не входит: она не
    двигает потолок, потому что и она сама, и он считаются от одних часов.
    """
    raw = os.environ.get(NIGHTLY_UNTIL_ENV)
    if not raw:
        return False
    try:
        return now() > float(raw)
    except ValueError:
        return False        # мусор в переменной — не повод рвать прогон



def night_wait_cap(default: float = 3600.0, now: Callable[[], float] = time.time) -> float:
    """Сколько ждать живую встречу: не дольше, чем осталось ночи.

    Живёт здесь, у владельца `NIGHTLY_UNTIL_ENV`: переменную выставляет ночной
    прогон, читают её `night_is_over` и этот потолок — одни часы, одно место.
    Голый час ожидания игнорировал потолок и растягивал прогон за него (аудит
    ночи 26.08). Потолка нет — ждём `default`; ночь уже вышла — не ждём вовсе
    (0). None не возвращается никогда: у гейта None значит «без потолка», и это
    ровно то, от чего функция существует (круги 4–6 по №338).
    """
    raw = os.environ.get(NIGHTLY_UNTIL_ENV)
    if not raw:
        return float(default)
    try:
        left = float(raw) - now()
    except ValueError:
        return float(default)       # мусор в переменной — не повод ждать без меры
    return max(0.0, min(float(default), left))

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
        return file_locks.held_by_someone(f)   # семантика — в докстринге хелпера


#: Процесс демона в таблице процессов: интерпретатор python, его флаги, затем
#: скрипт первым не-флаговым аргументом. Приложение стартует демона как
#: `<python> src/daemon.py`. Голый «src/daemon.py» совпадал с редактором, где
#: открыт файл (GLM M4), «python -m pylint src/daemon.py» — не демон (GLM M6).
#: `[Pp]`: python фреймворка macOS виден в таблице как `…/Python.app/…/Python`.
#: Шаблон один на всех: статус MCP и сторож миграции расходились — первый
#: говорил «остановлен», второй навсегда откладывал миграцию из-за редактора.
DAEMON_PROCESS = r"[Pp]ython[^ ]*( -[^ ]+)* [^ ]*src/daemon\.py($| )"


def daemon_process(run=subprocess.run) -> str:
    """Процесс демона на этой машине — независимо от корня данных.

    Второй признак живой встречи рядом с `daemon_alive`: лок лежит в корне,
    а демон мог стартовать из другого. Возвращает строки совпадений pgrep
    (пусто — процесса нет). pgrep недоступен — пусто и предупреждение: судить
    не по чему, как у лока без файла.
    """
    try:
        r = run(["pgrep", "-fl", DAEMON_PROCESS], capture_output=True, text=True, check=False)
    except OSError as e:
        print(f"pgrep недоступен ({e}) — признак процесса демона не работает", file=sys.stderr)
        return ""
    return r.stdout.strip()


def wait_while_live(root: pathlib.Path, log: Callable[[str], None] = print, *,
                    what: str = "фон", poll: float = 20.0, cap: float | None = None,
                    sleep=time.sleep, now=time.monotonic,
                    alive: Callable[[pathlib.Path], bool] | None = None) -> bool:
    """Подождать, пока живая встреча закончится. Возвращает True, если ждали.

    cap=None — ждать сколько понадобится; число — потолок в секундах, после
    которого идём работать в тесноте (об этом говорит лог, не код возврата).
    """
    if cap is not None and not _finite_seconds(cap):
        # потолок решает, кончится ли ожидание вообще: inf, nan, отрицательное
        # и bool — это «ждать вечно» или «упасть в строке лога» под видом числа.
        # Отказ здесь, у владельца, а не надежда на каждого вызывающего (круг 5
        # по №338, DS: cap=1e9 и inf проходили все сторожа на местах вызова)
        raise ValueError(f"{what}: потолок ожидания — конечное число секунд ≥ 0 или None, а не {cap!r}")
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


def _finite_seconds(value) -> bool:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return False
    try:
        finite = math.isfinite(value)
    except OverflowError:
        return False        # 10**400 — целое, но в float не влезает: «не секунды»
    return finite and value >= 0


def night_window_open(root: pathlib.Path, what: str, log: Callable[[str], None] = print, *,
                      default: float = 3600.0, clock: Callable[[], float] = time.time,
                      **gate) -> bool:
    """Ночное окно для тяжёлого или облачного шага: дождаться конца живой
    встречи, но не дольше, чем осталось ночи, и сказать, можно ли работать.

    True — окно открыто; False — ночь вышла (в том числе пока ждали встречу),
    шаг переносится на завтра. Одна дверь вместо пары «ждать с потолком +
    проверить конец ночи» в каждом ночном скрипте: четыре копии пары давали
    четыре места, где потолок можно не передать, передать константой или
    `None`, а проверку конца ночи — поставить до ожидания (круги 3–6 по №338,
    сторож на месте вызова пропускал это по очереди). Потолок вызывающий не
    передаёт вовсе — только `default` для своей шкалы.

    `clock` — стенные часы ночи (`NIGHTLY_UNTIL_ENV` — unix-время); `gate` уходит
    в `wait_while_live` как есть (poll, sleep, now, alive) — его часы свои.
    """
    wait_while_live(root, log, what=what, cap=night_wait_cap(default, clock), **gate)
    return not night_is_over(clock)
