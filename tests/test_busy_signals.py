"""Координация тяжеловесов: flock-лок мутатора и сигналы занятости
(ночь 23→24.08: мутатор делил модель с досье — 35 ReadTimeout по 300 с;
круг-1 по PR #399: pid+mtime-велосипед заменён flock по образцу live_gate)."""
import json
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
    assert lock.acquire()
    assert busy_signals.mutation_running(tmp_path)
    lock.release()
    assert not busy_signals.mutation_running(tmp_path)
    lock.release()  # повторный release — не ошибка


def test_second_mutator_is_refused(tmp_path):
    first = busy_signals.MutationLock(tmp_path)
    assert first.acquire()
    second = busy_signals.MutationLock(tmp_path)
    # эксклюзивность атомарна: второй прогон не перезапишет чужой лок
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_lock_file_is_private(tmp_path):
    lock = busy_signals.MutationLock(tmp_path)
    assert lock.acquire()
    assert (lock.path.stat().st_mode & 0o777) == 0o600
    lock.release()


def test_night_running_reads_status(tmp_path):
    path = tmp_path / "logs" / "nightly.json"
    path.parent.mkdir(parents=True)
    assert not busy_signals.night_running(tmp_path)
    path.write_text(json.dumps({"state": "running"}), encoding="utf-8")
    assert busy_signals.night_running(tmp_path)
    path.write_text(json.dumps({"state": "ok"}), encoding="utf-8")
    assert not busy_signals.night_running(tmp_path)


def test_stale_night_status_does_not_block(tmp_path):
    # Связка с nightly.sh: mtime running обновляется на КАЖДОЙ границе
    # шага (step()), поэтому протухание NIGHT_STALE_S означает именно
    # «брошено» (ребут), а не «длинная живая ночь» (круг-2 по #399, DS).
    path = tmp_path / "logs" / "nightly.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "running"}), encoding="utf-8")
    old = time.time() - busy_signals.NIGHT_STALE_S - 60
    os.utime(path, (old, old))
    # ребут посреди ночи оставил running навсегда — мутатор не заложник
    assert not busy_signals.night_running(tmp_path)
