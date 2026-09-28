"""Кто занимает машину: общие сигналы для ночи, мутатора и прочих тяжеловесов.

Ночь 23→24.08: мутатор делил локальную модель со встречей и ночным циклом —
досье поймали 35 ReadTimeout по 300 с, прогон оборвали руками. Здесь общий
словарь занятости: живая запись, разбор встреч, мутация тестов, ночной цикл.

Лок мутации — fcntl.flock по образцу live_gate.daemon.lock (круг-1 по
PR #399, DeepSeek: pid+mtime+STALE-велосипед дал три дыры — неэксклюзивный
захват, протухание на долгом базовом прогоне, pid-reuse; flock закрывает
весь класс: захват атомарен, смерть процесса освобождает ядром). С №444 B
лок разделяемый: мутаторов может идти несколько сразу (`--jobs` и соседние
прогоны), и «мутация идёт» значит «лок держит хоть один из них».
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import file_locks  # noqa: E402
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
    """Держит ли лок мутатора хоть один прогон.

    Мутаторы держат лок разделяемо, и проба разделяемым (`held_by_someone`)
    их не видит — поэтому проба эксклюзивом, `held_by_anyone`. «Мутация идёт»
    — только когда flock честно отказал; нет файла, прав или flock на томе —
    судить не по чему, ночь вставать не должна.
    """
    try:
        f = (root / LOCK_REL).open("r")
    except OSError:
        return False
    with f:
        return file_locks.held_by_anyone(f)


def machine_busy(root: pathlib.Path, *, count_mutation: bool = True) -> list[str]:
    """Чем занята машина, глазами тяжёлого процесса перед стартом.

    `count_mutation=False` — для самого мутатора: другой мутатор ему не помеха
    (замер №444 B: четыре доли разом на одном `.git` — 3,3× быстрее, выжившие те
    же), а запись, разбор встречи и ночь — помеха. Остальные читатели считают
    мутацию, как прежде."""
    busy: list[str] = []
    if live_recording(root):
        busy.append("живая запись")
    try:
        busy += list(MeetingStatusStore(root).busy())
    except Exception:  # noqa: BLE001
        pass
    if night_running(root):
        busy.append("ночной цикл")
    if count_mutation and mutation_running(root):
        busy.append("мутация тестов")
    return busy


class MutationLock:
    """Разделяемый flock на время прогона мутатора: держателей может быть
    несколько, а читатели (`mutation_running`) видят любого из них.

    fd живёт в объекте весь прогон: закрытие (или смерть процесса —
    kill -9, ребут) освобождает лок ядром, сердцебиение не нужно. pid в
    файл не пишется: держателей несколько, а строка у файла одна.
    """

    def __init__(self, root: pathlib.Path):
        self.path = root / LOCK_REL
        self._f = None

    def acquire(self) -> bool:
        """True — лок наш; False — не взят за секунду (ретраи против
        микросекундной пробы эксклюзивом кончились) или ФС без flock."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        f = self.path.open("a+")
        # Ретраи против микросекундных проб эксклюзивом (`held_by_anyone`) —
        # в хелпере (круг-2 по PR #399, DS); ФС без flock — отказ сразу.
        if not file_locks.acquire_shared(f, attempts=5, pause=0.2):
            f.close()
            return False
        os.chmod(self.path, 0o600)   # политика приватных каталогов, как у демона
        self._f = f
        return True

    def release(self) -> None:
        if self._f is not None:
            try:
                self._f.close()   # закрытие fd снимает flock
            finally:
                self._f = None
