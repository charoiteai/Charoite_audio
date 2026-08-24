"""Координация тяжеловесов: лок мутатора и сигналы занятости (ночь 23→24.08:
мутатор делил модель с досье — 35 ReadTimeout по 300 с)."""
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import busy_signals  # noqa: E402


def test_lock_lifecycle(tmp_path):
    lock = busy_signals.MutationLock(tmp_path)
    assert not busy_signals.mutation_running(tmp_path)
    lock.acquire()
    assert busy_signals.mutation_running(tmp_path)
    lock.release()
    assert not busy_signals.mutation_running(tmp_path)
    lock.release()  # повторный release — не ошибка


def test_dead_pid_does_not_hold_the_night(tmp_path):
    path = tmp_path / "logs" / "mutation.lock"
    path.parent.mkdir(parents=True)
    # pid из времён до ребута: свежий mtime, но процесса нет
    path.write_text("999999 0\n", encoding="utf-8")
    assert not busy_signals.mutation_running(tmp_path)


def test_stale_lock_expires(tmp_path):
    lock = busy_signals.MutationLock(tmp_path)
    lock.acquire()
    old = time.time() - busy_signals.STALE_S - 60
    os.utime(lock.path, (old, old))
    # брошенный kill -9 лок (сердцебиение умерло) ночь не держит
    assert not busy_signals.mutation_running(tmp_path)


def test_unreadable_but_fresh_lock_is_busy(tmp_path):
    path = tmp_path / "logs" / "mutation.lock"
    path.parent.mkdir(parents=True)
    path.write_text("мусор\n", encoding="utf-8")
    assert busy_signals.mutation_running(tmp_path)
