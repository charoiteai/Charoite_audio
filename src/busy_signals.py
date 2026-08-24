"""Кто занимает машину: общие сигналы для ночи, мутатора и прочих тяжеловесов.

Ночь 23→24.08: мутатор делил локальную модель со встречей и ночным циклом —
досье поймали 35 ReadTimeout по 300 с, прогон оборвали руками. Разрозненные
проверки уже были (wait_for_idle смотрит разбор и запись), но каждый тяжёлый
процесс изобретал свои — здесь их общий словарь: живая запись, разбор
встреч, мутация тестов.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import live_gate  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

#: лок лежит в КОРНЕ ДАННЫХ (CHAROITE_ROOT), рядом с logs/nightly.json —
#: у вложенных установок корень данных и корень репо различаются, а ночь
#: смотрит от данных.
LOCK_REL = pathlib.Path("logs") / "mutation.lock"
#: лок без сердцебиения старше этого — брошенный (kill -9, ребут)
STALE_S = 30 * 60


def live_recording(root: pathlib.Path) -> bool:
    """Идёт ли живая запись встречи (лок демона)."""
    try:
        return bool(live_gate.daemon_alive(root))
    except Exception:  # noqa: BLE001 — сигнал занятости не смеет ронять
        return False


def machine_busy(root: pathlib.Path) -> list[str]:
    """Чем занята машина, глазами тяжёлого процесса перед стартом."""
    busy: list[str] = []
    if live_recording(root):
        busy.append("живая запись")
    try:
        busy += list(MeetingStatusStore(root / "data").busy())
    except Exception:  # noqa: BLE001
        pass
    return busy


def mutation_running(root: pathlib.Path) -> bool:
    """Жив ли лок мутатора (для wait_for_idle ночи)."""
    path = root / LOCK_REL
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    if time.time() - st.st_mtime > STALE_S:
        return False  # брошенный лок не должен держать ночь вечно
    try:
        pid = int(path.read_text(encoding="utf-8").strip().split()[0])
    except (ValueError, OSError, IndexError):
        return True  # нечитаемый, но свежий — считаем живым, ночь подождёт
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class MutationLock:
    """Файл-признак «идёт мутация тестов»: pid + mtime как сердцебиение."""

    def __init__(self, root: pathlib.Path):
        self.path = root / LOCK_REL

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{os.getpid()} {int(time.time())}\n",
                             encoding="utf-8")

    def beat(self) -> None:
        """Обновить mtime: длинный прогон жив, лок не брошен."""
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
