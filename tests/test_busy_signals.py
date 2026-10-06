"""Координация тяжеловесов: flock-лок мутатора и сигналы занятости
(ночь 23→24.08: мутатор делил модель с досье — 35 ReadTimeout по 300 с;
круг-1 по PR #399: pid+mtime-велосипед заменён flock по образцу live_gate)."""
import json
import os
import pathlib
import signal
import subprocess
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


def test_two_mutators_hold_the_lock_together(tmp_path):
    """Замок разделяемый (№444 B): доли `--jobs` и соседние прогоны держат его
    вместе, и «мутация идёт», пока держит хоть один."""
    first = busy_signals.MutationLock(tmp_path)
    second = busy_signals.MutationLock(tmp_path)
    assert first.acquire()
    assert second.acquire()
    assert busy_signals.mutation_running(tmp_path)
    first.release()
    assert busy_signals.mutation_running(tmp_path), "второй держатель ещё жив"
    second.release()
    assert not busy_signals.mutation_running(tmp_path)


def test_killed_holder_leaves_no_lock(tmp_path):
    """Держатель в отдельном процессе, убитый `kill -9`, замок не оставляет: его
    снимает ядро, сердцебиение не нужно."""
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import pathlib, sys, time; sys.path.insert(0, sys.argv[2]); import busy_signals; "
         "lock = busy_signals.MutationLock(pathlib.Path(sys.argv[1])); assert lock.acquire(); "
         "print('held', flush=True); time.sleep(60)",
         str(tmp_path), str(REPO / "src")],
        stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert busy_signals.mutation_running(tmp_path)
    finally:
        holder.send_signal(signal.SIGKILL)
        holder.wait()
        holder.stdout.close()
    assert not busy_signals.mutation_running(tmp_path)


def test_mutator_guard_does_not_count_other_mutators(tmp_path):
    """Гвард старта мутатора (`count_mutation=False`) другого мутатора не видит,
    а запись и ночь видит; прочие читатели считают мутацию, как прежде."""
    import fcntl
    import live_gate
    lock = busy_signals.MutationLock(tmp_path)
    assert lock.acquire()
    try:
        assert busy_signals.machine_busy(tmp_path) == ["мутация тестов"]
        assert busy_signals.machine_busy(tmp_path, count_mutation=False) == []
        night = tmp_path / "logs" / "nightly.json"
        night.write_text(json.dumps({"state": "running"}), encoding="utf-8")
        daemon = live_gate.lock_path(tmp_path)
        daemon.parent.mkdir(parents=True, exist_ok=True)
        with daemon.open("a") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            busy = busy_signals.machine_busy(tmp_path, count_mutation=False)
            assert busy[0] == "живая запись"
            assert busy[1].startswith("ночной цикл (")
            assert str(night) in busy[1], "причина ночного цикла называет файл статуса"
            assert busy_signals.machine_busy(tmp_path) == [*busy, "мутация тестов"]
    finally:
        lock.release()


def test_machine_busy_names_the_meeting_stage_in_words(tmp_path):
    """Стадия разбора встречи — словами: сырое имя стадии читателю ничего не говорит."""
    d = tmp_path / "logs" / "meeting-status"
    d.mkdir(parents=True)
    (d / "встреча.json").write_text(
        json.dumps({"state": "processing", "stage": "transcribe", "updated_at": time.time()}),
        encoding="utf-8")
    assert busy_signals.machine_busy(tmp_path) == ["разбор встречи (transcribe)"]


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


def test_a_fresh_night_reason_names_the_status_file_and_its_age(tmp_path):
    """Идущая ночь называет файл статуса и возраст: иначе брошенный `running`
    до `NIGHT_STALE_S` не отличить от живой ночи, и человек ждёт зря (№622 B2)."""
    path = tmp_path / "logs" / "nightly.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "running"}), encoding="utf-8")
    os.utime(path, (time.time() - 90 * 60, time.time() - 90 * 60))
    assert busy_signals.machine_busy(tmp_path) == \
        [f"ночной цикл ({path}, обновлён 90 мин назад)"]


def test_a_status_from_the_future_has_no_negative_age(tmp_path):
    """Время правки в будущем (сбитые часы) не даёт отрицательного возраста:
    причина говорит «обновлён 0 мин назад», а не «-5» (№622 B2, часть 1)."""
    path = tmp_path / "logs" / "nightly.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"state": "running"}), encoding="utf-8")
    future = time.time() + 5 * 60
    os.utime(path, (future, future))
    reason = busy_signals.night_busy_reason(tmp_path)
    assert reason is not None, "будущее время правки — ночь всё ещё идёт"
    assert "обновлён 0 мин назад" in reason, reason
