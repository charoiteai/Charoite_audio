"""Хелпер файловых локов (партия D-П6): проба и захват — живыми flock.

flock различает open file descriptions, поэтому конфликт честно
воспроизводится двумя open() одного файла в одном процессе.
"""
import errno
import fcntl
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import file_locks  # noqa: E402


def test_probe_free_lock_and_leaves_it_free(tmp_path):
    path = tmp_path / "x.lock"
    path.write_text("", encoding="utf-8")
    with path.open("r") as f:
        assert file_locks.held_by_someone(f) is False
    # проба ничего не оставила: эксклюзив берётся с первой попытки
    with path.open("w") as g:
        fcntl.flock(g, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_probe_sees_a_foreign_exclusive(tmp_path):
    path = tmp_path / "x.lock"
    owner = path.open("w")
    fcntl.flock(owner, fcntl.LOCK_EX)
    try:
        with path.open("r") as f:
            assert file_locks.held_by_someone(f) is True
    finally:
        owner.close()


def test_acquire_free_lock_first_try(tmp_path):
    path = tmp_path / "x.lock"
    calls = []
    with path.open("w") as f:
        assert file_locks.acquire_exclusive(f, sleep=calls.append) is True
        assert calls == []          # ни одного ожидания
        # лок реально наш: чужая проба видит занятость
        with path.open("r") as g:
            assert file_locks.held_by_someone(g) is True


def test_acquire_busy_lock_retries_then_gives_up(tmp_path):
    path = tmp_path / "x.lock"
    owner = path.open("w")
    fcntl.flock(owner, fcntl.LOCK_EX)
    try:
        pauses = []
        with path.open("w") as f:
            ok = file_locks.acquire_exclusive(
                f, attempts=5, pause=0.2, sleep=pauses.append)
        assert ok is False
        assert pauses == [0.2] * 4   # после последней попытки не ждём
    finally:
        owner.close()


def test_acquire_wins_when_freed_between_attempts(tmp_path):
    path = tmp_path / "x.lock"
    owner = path.open("w")
    fcntl.flock(owner, fcntl.LOCK_EX)
    with path.open("w") as f:
        # владелец отпускает во время первой паузы — вторая попытка берёт
        assert file_locks.acquire_exclusive(
            f, sleep=lambda _s: owner.close()) is True


def test_unsupported_fs_fails_fast_by_default(monkeypatch, tmp_path):
    def no_flock(fd, op):
        raise OSError(errno.ENOLCK, "no locks")
    monkeypatch.setattr(file_locks.fcntl, "flock", no_flock)
    pauses = []
    with (tmp_path / "x.lock").open("w") as f:
        assert file_locks.acquire_exclusive(f, sleep=pauses.append) is False
    assert pauses == []             # дефолт: ФС без flock — отказ без ретраев
    with (tmp_path / "x.lock").open("r") as f:
        assert file_locks.held_by_someone(f) is False   # фон не встаёт


def test_daemon_semantics_retries_any_oserror(monkeypatch, tmp_path):
    def no_flock(fd, op):
        raise OSError(errno.ENOLCK, "no locks")
    monkeypatch.setattr(file_locks.fcntl, "flock", no_flock)
    pauses = []
    with (tmp_path / "x.lock").open("w") as f:
        ok = file_locks.acquire_exclusive(
            f, attempts=5, pause=0.1, busy=(OSError,), sleep=pauses.append)
    assert ok is False
    assert pauses == [0.1] * 4      # демон ретраит любую OSError — как раньше


def test_daemon_call_site_keeps_the_oserror_policy():
    """Ловушка будущих правок (круг-1 по #415, DS): тесты хелпера пиннят
    обе конфигурации, но убранный из daemon busy=(OSError,) они не
    заметят — политику вызова держит структурная проверка."""
    daemon = (ROOT / "src" / "daemon.py").read_text(encoding="utf-8")
    assert "busy=(OSError,)" in daemon


def test_zero_attempts_is_a_caller_error(tmp_path):
    import pytest
    with (tmp_path / "x.lock").open("w") as f:
        with pytest.raises(ValueError):
            file_locks.acquire_exclusive(f, attempts=0)
