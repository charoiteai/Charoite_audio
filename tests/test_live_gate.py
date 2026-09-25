"""Живая встреча важнее фона: гейт по локу демона (src/live_gate.py).

Факт 18.08: пересборка 18-часовой записи держала тяжёлую модель, подсказки
живой встречи 45 минут падали с 503. Здесь проверяется сам признак (лок
демона занят = встреча идёт) и правило ожидания — без сна и без демона.
"""
from __future__ import annotations

import fcntl
import pathlib
import re
import shutil
import subprocess
import sys
import time

import pytest

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


@pytest.mark.parametrize("cmdline, is_daemon", [
    ("/opt/venv/bin/python3 /home/u/charoite/src/daemon.py", True),
    ("python src/daemon.py --flag", True),
    ("/x/.venv/bin/python3 -u /y/src/daemon.py", True),
    ("/x/Python.app/Contents/MacOS/Python src/daemon.py", True),   # python фреймворка macOS
    ("vim src/daemon.py", False),                                   # редактор с открытым файлом (GLM M4)
    ("less /tmp/src/daemon.py.bak", False),
    ("python -m pylint src/daemon.py", False),                      # скрипт не первый аргумент (GLM M6)
    ("claude -p посмотри src/daemon.py", False),                    # сессия ассистента с путём в промпте
])
def test_daemon_process_pattern_tells_the_daemon_from_its_readers(cmdline, is_daemon):
    """Один шаблон на статус MCP и сторож миграции: свой шаблон миграции
    («src/daemon\\.py$») принимал редактор за демона и откладывал её навсегда."""
    assert bool(re.search(live_gate.DAEMON_PROCESS, cmdline)) is is_daemon


def test_daemon_process_asks_pgrep_with_the_shared_pattern():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="4242 python\n", stderr="")
    assert live_gate.daemon_process(run=run) == "4242 python"
    assert seen == [["pgrep", "-fl", live_gate.DAEMON_PROCESS]]


def test_daemon_process_without_pgrep_is_not_a_meeting(capsys):
    """Нет pgrep — судить не по чему: пусто и предупреждение, не вечное «ждём»."""
    def run(cmd, **kw):
        raise FileNotFoundError(2, "нет такого файла", "pgrep")
    assert live_gate.daemon_process(run=run) == ""
    assert "pgrep недоступен" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("pgrep") is None, reason="нет pgrep в системе")
def test_daemon_process_finds_a_running_daemon_through_real_pgrep(tmp_path):
    """Шаблон понимает настоящий pgrep (ERE), а не только re: поддельный демон
    `<python> <tmp>/src/daemon.py` виден, пока жив."""
    script = tmp_path / "src" / "daemon.py"
    script.parent.mkdir()
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        deadline = time.monotonic() + 5
        found = ""
        while str(proc.pid) not in found and time.monotonic() < deadline:
            found = live_gate.daemon_process()
            time.sleep(0.05)
        assert str(proc.pid) in found
    finally:
        proc.kill()
        proc.wait()
    assert str(proc.pid) not in live_gate.daemon_process(), "процесс умер — не демон"


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


def test_deadline_second_itself_is_still_night(monkeypatch):
    """Граница `>`: ровно в секунду дедлайна ночь ещё идёт — мутация Gt→GtE
    срывала последнюю тему на секунду раньше (мутационный прогон 21.08)."""
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "1000")
    assert live_gate.night_is_over(now=lambda: 1000.0) is False
    assert live_gate.night_is_over(now=lambda: 1000.001) is True


# --- ночное окно: потолок и конец ночи у владельца гейта (№338, круги 3–6) -----

@pytest.mark.parametrize("until, now, expected", [
    (None, 1000.0, 3600.0),        # потолка ночи нет — час
    ("1100", 1000.0, 100.0),       # ночь кончится через 100 с — ждём не дольше
    ("99999", 1000.0, 3600.0),     # до конца ночи дольше часа — всё равно час
    ("900", 1000.0, 0.0),          # ночь уже вышла — не ждём вовсе
    ("завтра", 1000.0, 3600.0),    # мусор в переменной — не падаем, ждём час
])
def test_night_wait_cap_is_always_a_finite_number(monkeypatch, until, now, expected):
    if until is None:
        monkeypatch.delenv(live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    else:
        monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, until)
    cap = live_gate.night_wait_cap(now=lambda: now)
    assert isinstance(cap, float) and cap == expected


def test_night_wait_cap_reads_the_real_clock_by_default(monkeypatch):
    """Ветка часов по умолчанию — без подмены: ночь кончается через 100 с по
    настоящим часам, потолок обязан быть около 100, а не часом (круг 6, GLM:
    ни один тест не исполнял часы функции, и `now()` → 0 проходил всё)."""
    import time
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, str(time.time() + 100))
    assert 95 <= live_gate.night_wait_cap() <= 100


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1, True])
def test_wait_while_live_refuses_a_cap_that_is_not_seconds(tmp_path, bad):
    """inf, nan, отрицательное и bool — «ждать вечно» или «упасть в логе» под
    видом числа; гейт отказывает сам, не надеясь на вызывающих (круг 5, DS)."""
    with pytest.raises(ValueError):
        live_gate.wait_while_live(tmp_path, lambda m: None, cap=bad, alive=lambda r: False)


def test_wait_while_live_refuses_an_integer_bigger_than_float(tmp_path):
    """Целое больше максимума float (10**400) роняло math.isfinite
    OverflowError вместо обещанного ValueError у гейта (круг 7 по №338)."""
    with pytest.raises(ValueError):
        live_gate.wait_while_live(tmp_path, lambda m: None, cap=10**400, alive=lambda r: False)


@pytest.mark.parametrize("ok", [None, 0, 0.0, 180, 3600.0])
def test_wait_while_live_takes_none_and_finite_seconds(tmp_path, ok):
    assert live_gate.wait_while_live(tmp_path, lambda m: None, cap=ok, alive=lambda r: False) is False


def test_night_window_is_open_when_no_meeting_and_the_night_goes_on(tmp_path, monkeypatch):
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "1100")
    caps = []
    real = live_gate.wait_while_live
    monkeypatch.setattr(live_gate, "wait_while_live",
                        lambda *a, **k: caps.append(k.get("cap")) or real(*a, **k))
    assert live_gate.night_window_open(tmp_path, "проба", lambda m: None,
                                       clock=lambda: 1000.0, alive=lambda r: False) is True
    assert caps == [100.0], "гейт ждёт с потолком из остатка ночи, а не константой"


def test_night_window_is_closed_when_the_night_is_over(tmp_path, monkeypatch):
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "900")
    assert live_gate.night_window_open(tmp_path, "проба", lambda m: None,
                                       clock=lambda: 1000.0, alive=lambda r: False) is False


def test_night_window_checks_the_night_after_waiting_for_the_meeting(tmp_path, monkeypatch):
    """Встреча шла, ожидание вытолкнуло за конец ночи — окно закрыто. Проверка
    конца ночи ДО ожидания пропустила бы шаг в облако уже утром."""
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "1100")
    стенные = [1000.0]
    встреча = iter([True, True, False])
    def sleep(sec):
        стенные[0] += 200.0          # ждали встречу — ночь тем временем кончилась
    assert live_gate.night_window_open(
        tmp_path, "проба", lambda m: None, clock=lambda: стенные[0],
        alive=lambda r: next(встреча), sleep=sleep, now=lambda: 0.0) is False
