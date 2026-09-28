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


# ---- Разделяемый захват и проба любого держателя (№444 B) --------------------
# Замок мутатора — разделяемый: мутаторов может идти несколько, и «мутация идёт»
# значит «держит хоть один». Проба разделяемым (held_by_someone) их не видит,
# поэтому читатель спрашивает held_by_anyone — пробой эксклюзивом.


def test_shared_holders_coexist_and_block_an_exclusive(tmp_path):
    path = tmp_path / "x.lock"
    with path.open("a") as a, path.open("a") as b:
        assert file_locks.acquire_shared(a, sleep=lambda _s: None) is True
        assert file_locks.acquire_shared(b, sleep=lambda _s: None) is True
        with path.open("r") as probe:
            # разделяемая проба держателей SH не видит — ровно поэтому нужна вторая
            assert file_locks.held_by_someone(probe) is False
            assert file_locks.held_by_anyone(probe, sleep=lambda _s: None) is True
        with path.open("w") as g:
            assert file_locks.acquire_exclusive(g, attempts=1) is False


def test_held_by_anyone_sees_an_exclusive_holder_too(tmp_path):
    path = tmp_path / "x.lock"
    owner = path.open("w")
    fcntl.flock(owner, fcntl.LOCK_EX)
    try:
        with path.open("r") as f:
            assert file_locks.held_by_anyone(f, sleep=lambda _s: None) is True
    finally:
        owner.close()


def test_held_by_anyone_on_a_free_lock_leaves_it_free(tmp_path):
    path = tmp_path / "x.lock"
    path.write_text("", encoding="utf-8")
    with path.open("r") as f:
        assert file_locks.held_by_anyone(f) is False
        # проба отпустила свой эксклюзив сразу: чужой берётся с первой попытки
        with path.open("w") as g:
            fcntl.flock(g, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_held_by_anyone_retries_a_momentary_refusal(monkeypatch, tmp_path):
    """Одна чужая проба держит файл микросекунды: один отказ BlockingIOError, а за
    ним — свободно. Проба обязана повторить и ответить «свободно», а не «занято»."""
    real = fcntl.flock
    calls = []

    def flock(fd, op):
        calls.append(op)
        if len(calls) == 1:
            raise BlockingIOError(errno.EWOULDBLOCK, "busy for a moment")
        return real(fd, op)
    monkeypatch.setattr(file_locks.fcntl, "flock", flock)
    pauses = []
    with (tmp_path / "x.lock").open("a") as f:
        assert file_locks.held_by_anyone(f, sleep=pauses.append) is False
    assert pauses == [0.1]
    assert calls[:2] == [fcntl.LOCK_EX | fcntl.LOCK_NB] * 2 and calls[-1] == fcntl.LOCK_UN


def test_held_by_anyone_gives_up_after_three_attempts(monkeypatch, tmp_path):
    def always_busy(fd, op):
        raise BlockingIOError(errno.EWOULDBLOCK, "held")
    monkeypatch.setattr(file_locks.fcntl, "flock", always_busy)
    pauses = []
    with (tmp_path / "x.lock").open("a") as f:
        assert file_locks.held_by_anyone(f, sleep=pauses.append) is True
    assert pauses == [0.1, 0.1]         # три попытки, после последней не ждём


def test_no_flock_is_free_for_the_probe_and_a_refusal_for_the_shared_lock(monkeypatch, tmp_path):
    def no_flock(fd, op):
        raise OSError(errno.ENOLCK, "no locks")
    monkeypatch.setattr(file_locks.fcntl, "flock", no_flock)
    pauses = []
    with (tmp_path / "x.lock").open("a") as f:
        assert file_locks.held_by_anyone(f, sleep=pauses.append) is False   # фон не встаёт
        assert file_locks.acquire_shared(f, sleep=pauses.append) is False   # отказ сразу
    assert pauses == []


def test_shared_acquire_retries_past_a_probe(tmp_path):
    """Разделяемый захват в момент чужой пробы эксклюзивом ждёт паузу и берёт лок."""
    path = tmp_path / "x.lock"
    probe = path.open("w")
    fcntl.flock(probe, fcntl.LOCK_EX)
    with path.open("a") as f:
        assert file_locks.acquire_shared(f, sleep=lambda _s: probe.close()) is True


def test_try_lock_names_a_filesystem_without_flock(monkeypatch, tmp_path):
    """Три исхода механики — значения, а не «не взят»: ФС без flock — свой исход, не
    занятость и не `None`; таблица политики в докстринге модуля читает именно его."""
    def no_flock(fd, op):
        raise OSError(errno.ENOLCK, "no locks")
    monkeypatch.setattr(file_locks.fcntl, "flock", no_flock)
    with (tmp_path / "x.lock").open("a") as f:
        got = file_locks._try_lock(f, fcntl.LOCK_EX, attempts=3, pause=0.0,
                                   busy=(BlockingIOError,), sleep=lambda _s: None)
    assert got == file_locks._NO_FLOCK and got is not None
    assert len({file_locks._TAKEN, file_locks._BUSY, file_locks._NO_FLOCK}) == 3


def test_shared_acquire_default_retries_are_spaced_against_probes(tmp_path):
    """Умолчание разделяемого захвата — пять попыток через 0,2 с: проба эксклюзивом
    держит файл микросекунды, и ретраи без паузы сгорели бы за одну пробу."""
    path = tmp_path / "x.lock"
    holder = path.open("w")
    fcntl.flock(holder, fcntl.LOCK_EX)
    pauses: list[float] = []
    with path.open("a") as f:
        assert file_locks.acquire_shared(f, sleep=pauses.append) is False
    holder.close()
    assert pauses == [0.2] * 4
