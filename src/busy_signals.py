"""Кто занимает машину: общие сигналы для ночи, мутатора и прочих тяжеловесов.

Ночь 23→24.08: мутатор делил локальную модель со встречей и ночным циклом —
досье поймали 35 ReadTimeout по 300 с, прогон оборвали руками. Здесь общий
словарь занятости: живая запись, разбор встреч, мутация тестов, ночной цикл.

Лок мутации — fcntl.flock по образцу live_gate.daemon.lock (круг-1 по
PR #399, DeepSeek: pid+mtime+STALE-велосипед дал три дыры — неэксклюзивный
захват, протухание на долгом базовом прогоне, pid-reuse; flock закрывает
весь класс: эксклюзивность атомарна, смерть процесса освобождает ядром).
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import live_gate  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

LOCK_REL = pathlib.Path("logs") / "mutation.lock"
NIGHTLY_REL = pathlib.Path("logs") / "nightly.json"
#: nightly.json со state=running старше этого — брошенный (ребут посреди ночи)
NIGHT_STALE_S = 8 * 3600


def live_recording(root: pathlib.Path) -> bool:
    """Идёт ли живая запись встречи (лок демона)."""
    try:
        return bool(live_gate.daemon_alive(root))
    except Exception:  # noqa: BLE001 — сигнал занятости не смеет ронять
        return False


def night_running(root: pathlib.Path) -> bool:
    """Идёт ли ночной цикл (logs/nightly.json, state=running, свежий)."""
    path = root / NIGHTLY_REL
    try:
        st = path.stat()
        if time.time() - st.st_mtime > NIGHT_STALE_S:
            return False
        state = json.loads(path.read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError):
        return False
    return state == "running"


def mutation_running(root: pathlib.Path) -> bool:
    """Держит ли кто-то лок мутатора — как live_gate.daemon_alive.

    «Мутация идёт» — только когда flock честно отказал из-за чужого лока;
    нет файла или прав — судить не по чему, ночь вставать не должна.
    """
    try:
        f = (root / LOCK_REL).open("r")
    except OSError:
        return False
    with f:
        try:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def machine_busy(root: pathlib.Path) -> list[str]:
    """Чем занята машина, глазами тяжёлого процесса перед стартом."""
    busy: list[str] = []
    if live_recording(root):
        busy.append("живая запись")
    try:
        busy += list(MeetingStatusStore(root).busy())
    except Exception:  # noqa: BLE001
        pass
    if night_running(root):
        busy.append("ночной цикл")
    if mutation_running(root):
        busy.append("мутация тестов")
    return busy


class MutationLock:
    """Эксклюзивный flock на время прогона мутатора.

    fd живёт в объекте весь прогон: закрытие (или смерть процесса —
    kill -9, ребут) освобождает лок ядром, сердцебиение не нужно.
    """

    def __init__(self, root: pathlib.Path):
        self.path = root / LOCK_REL
        self._f = None

    def acquire(self) -> bool:
        """True — лок наш; False — держит другой мутатор (не стартуем)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = self.path.open("a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            return False
        os.chmod(self.path, 0o600)   # политика приватных каталогов, как у демона
        f.seek(0); f.truncate()
        f.write(f"{os.getpid()} {int(time.time())}\n")
        f.flush()
        self._f = f
        return True

    def release(self) -> None:
        if self._f is not None:
            try:
                self._f.close()   # закрытие fd снимает flock
            finally:
                self._f = None
