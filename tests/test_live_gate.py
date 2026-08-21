"""Живая встреча важнее фона: гейт по локу демона (src/live_gate.py).

Факт 18.08: пересборка 18-часовой записи держала тяжёлую модель, подсказки
живой встречи 45 минут падали с 503. Здесь проверяется сам признак (лок
демона занят = встреча идёт) и правило ожидания — без сна и без демона.
"""
from __future__ import annotations

import fcntl
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import live_gate  # noqa: E402


def test_no_lock_file_means_no_meeting(tmp_path):
    assert live_gate.daemon_alive(tmp_path) is False


def test_free_lock_means_no_meeting(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "daemon.lock").write_text("")
    assert live_gate.daemon_alive(tmp_path) is False


def test_held_lock_means_meeting_is_live(tmp_path):
    (tmp_path / "logs").mkdir()
    lock = (tmp_path / "logs" / "daemon.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # как daemon.main()
    try:
        assert live_gate.daemon_alive(tmp_path) is True
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    assert live_gate.daemon_alive(tmp_path) is False, "лок отпущен — встреча кончилась"


def test_unreadable_or_odd_lock_file_is_not_a_meeting(tmp_path):
    """Права 0000, каталог вместо файла — судить не по чему: фон не должен
    вставать навечно (ревью 18.08: любая OSError считалась «встреча идёт»)."""
    (tmp_path / "logs").mkdir()
    lock = tmp_path / "logs" / "daemon.lock"
    lock.write_text("")
    lock.chmod(0)
    try:
        assert live_gate.daemon_alive(tmp_path) is False
    finally:
        lock.chmod(0o600)
    lock.unlink()
    lock.mkdir()
    assert live_gate.daemon_alive(tmp_path) is False


def test_checker_does_not_evict_another_checker(tmp_path):
    """Два проверяющих (пересборка и ночь) не мешают друг другу: разделяемый лок."""
    (tmp_path / "logs").mkdir()
    lock = (tmp_path / "logs" / "daemon.lock")
    lock.write_text("")
    other = lock.open("r")
    fcntl.flock(other, fcntl.LOCK_SH | fcntl.LOCK_NB)   # сосед сейчас проверяет
    try:
        assert live_gate.daemon_alive(tmp_path) is False
    finally:
        fcntl.flock(other, fcntl.LOCK_UN)
        other.close()


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def test_free_machine_is_not_waited_for(tmp_path):
    c = Clock()
    said: list[str] = []
    assert live_gate.wait_while_live(tmp_path, said.append, sleep=c.sleep, now=c.now,
                                     alive=lambda r: False) is False
    assert c.slept == [] and said == []


def test_waits_until_meeting_ends_then_continues(tmp_path):
    c = Clock()
    said: list[str] = []
    answers = iter([True, True, True, False])   # первый опрос — «ждать ли», дальше — цикл
    waited = live_gate.wait_while_live(tmp_path, said.append, what="разбор", poll=20,
                                       sleep=c.sleep, now=c.now, alive=lambda r: next(answers))
    assert waited is True
    assert c.slept == [20, 20], "ждём опросом, а не одним длинным сном"
    assert any("уступаю" in m for m in said) and any("продолжаю" in m for m in said)


def test_cap_lets_night_go_to_work_in_cramped_conditions(tmp_path):
    """Ночной цикл ждёт с потолком: утренняя встреча не должна съесть ночь."""
    c = Clock()
    said: list[str] = []
    waited = live_gate.wait_while_live(tmp_path, said.append, what="досье", poll=60, cap=180,
                                       sleep=c.sleep, now=c.now, alive=lambda r: True)
    assert waited is True
    assert sum(c.slept) == 180
    assert any("в тесноте" in m for m in said), "о работе в тесноте должен сказать лог"


def test_no_deadline_means_night_never_ends(monkeypatch):
    """Переменной нет — потолка нет: ручной прогон не должен обрываться."""
    monkeypatch.delenv(live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    assert live_gate.night_is_over() is False


def test_night_is_over_after_the_deadline(monkeypatch):
    """Ночная работа обязана кончаться ночью (21.08: прогон 04:16 → 11:36)."""
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "1000")
    assert live_gate.night_is_over(now=lambda: 1001.0) is True
    assert live_gate.night_is_over(now=lambda: 999.0) is False


def test_garbage_deadline_does_not_break_the_run(monkeypatch):
    """Мусор в переменной — не повод рвать ночь на середине."""
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "завтра")
    assert live_gate.night_is_over() is False
