"""Живая встреча важнее фона: гейт по локу демона (src/live_gate.py).

Факт 18.08: пересборка 18-часовой записи держала тяжёлую модель, подсказки
живой встречи 45 минут падали с 503. Здесь проверяется сам признак (лок
демона занят = встреча идёт) и правило ожидания — без сна; живой демон —
только в тесте его отказа при консольной встрече (выход 12 №725, Haiku M1).
"""
from __future__ import annotations

import fcntl
import os
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


def test_console_meeting_lock_is_seen_by_the_background(tmp_path):
    """Встреча v1 держит тот же лок, что демон: фон видит её живой (выход 2 №725, DS I2)."""
    f, outcome = live_gate.hold_meeting(tmp_path, sleep=lambda s: None)
    try:
        assert outcome == live_gate.file_locks.TAKEN and f is not None
        assert live_gate.daemon_alive(tmp_path) is True
        second, again = live_gate.hold_meeting(tmp_path, sleep=lambda s: None)
        assert (second, again) == (None, live_gate.file_locks.BUSY), "вторая встреча — отказ"
    finally:
        f.close()
    assert live_gate.daemon_alive(tmp_path) is False, "лок снят — встречи нет"


def test_console_meeting_lock_on_a_volume_without_flock(tmp_path, monkeypatch):
    """Том без flock: лок не взят, описатель закрыт, исход назван — запись идёт без него."""
    import errno

    def no_flock(fd, op):
        raise OSError(errno.ENOTSUP, "flock не поддержан")

    monkeypatch.setattr(live_gate.file_locks.fcntl, "flock", no_flock)
    assert live_gate.hold_meeting(tmp_path, sleep=lambda s: None) == (None, live_gate.file_locks.NO_FLOCK)


def test_console_entry_takes_the_meeting_lock_before_loading_models():
    """v1 берёт лок встречи до загрузки STT: иначе фон не видит запись (№725)."""
    body = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
    main_body = body[body.index("def main():"):]
    assert main_body.index("live_gate.hold_meeting(") < main_body.index("STT(cfg)")
    # описатель — в имени, живущем до конца main(): «_, outcome =» закрыл бы файл и снял лок (DS M7 выхода 3)
    assert "meeting_lock, outcome = live_gate.hold_meeting(" in main_body


def test_console_meeting_lock_without_rights_is_named_not_a_traceback(tmp_path):
    """На месте `logs/` файл: mkdir падает OSError — исход «лок не взят», а не трейсбек v1 (выход 3 №725)."""
    (tmp_path / "logs").write_text("не каталог")
    assert live_gate.hold_meeting(tmp_path, sleep=lambda s: None) == (None, live_gate.file_locks.NO_FLOCK)


def test_refused_daemon_tells_a_console_holder_from_another_daemon(tmp_path):
    """Отказ второму демону называет консоль, пока жив её признак-лок; демон-держатель
    и снятая консоль — «другое окно» без причины (выход 3 №725, DS I3; выход 4, Sonnet M3)."""
    console_text, reason = live_gate.refusal(tmp_path)
    assert reason is None, "никого — не консоль"
    f, _ = live_gate.hold_meeting(tmp_path, sleep=lambda s: None)
    try:
        text, reason = live_gate.refusal(tmp_path)
        assert reason == "console_meeting" and "консольная запись" in text
        assert "src/" not in text, "текст видит пользователь — без пути к файлу (Haiku I1)"
    finally:
        f.close()
    with live_gate.lock_path(tmp_path).open("w") as daemon:   # как daemon.main()
        fcntl.flock(daemon, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert live_gate.refusal(tmp_path) == (console_text, None), "держит демон — признака консоли нет"


def test_console_mark_does_not_outlive_its_holder(tmp_path):
    """Признак консоли — лок ядра, а не метка в файле: вышедшая консоль не оставляет его,
    и демон в миг своего захвата консолью не назван (выход 4 №725, DS C1)."""
    f, _ = live_gate.hold_meeting(tmp_path, sleep=lambda s: None)
    f.close()
    assert live_gate.console_lock_path(tmp_path).exists(), "файл признака остался — судит лок"
    with live_gate.lock_path(tmp_path).open("w") as daemon:
        fcntl.flock(daemon, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert live_gate.held_by_console(tmp_path) is False
        assert live_gate.refusal(tmp_path)[1] is None


def test_console_reason_is_not_claimed_on_a_volume_without_flock(tmp_path, monkeypatch):
    """flock отказал не из-за держателя (ENOLCK сетевого тома): консоль не названа,
    отказ не фатальный — приложение не застревает на «остановите консоль» (выход 4 №725, DS I1)."""
    import errno
    f, _ = live_gate.hold_meeting(tmp_path, sleep=lambda s: None)
    f.close()

    def no_lock(fd, op):
        raise OSError(errno.ENOLCK, "нет блокировок")

    monkeypatch.setattr(live_gate.file_locks.fcntl, "flock", no_lock)
    assert live_gate.refusal(tmp_path)[1] is None


def test_console_meeting_without_its_mark_keeps_the_meeting_lock(tmp_path):
    """Признак консоли не открылся: лок встречи всё равно взят, теряется только причина
    отказа демону; трейсбека v1 нет (выход 4 №725, Sonnet M1 = DS M2)."""
    live_gate.console_lock_path(tmp_path).mkdir(parents=True)   # на месте файла каталог
    f, outcome = live_gate.hold_meeting(tmp_path, sleep=lambda s: None)
    try:
        assert outcome == live_gate.file_locks.TAKEN and f.mark is None
        assert live_gate.daemon_alive(tmp_path) is True
        assert live_gate.refusal(tmp_path)[1] is None
    finally:
        f.close()


def test_daemon_refusal_names_the_console_reason(tmp_path):
    """Живой демон при консольной встрече отказывает строкой статуса с причиной
    `console_meeting` и выходит, не стартуя; причина, пришедшая от демона,
    обязана быть в `fatalReasons` приложения — `AutostopTests.swift` сверяет
    только свой литерал (выход 13, Haiku C1). Иначе три
    «восстанавливаю запись» (выход 3 №725). Поведением, а не сканом `daemon.py`: скан то
    пропускал обход, то падал на равном коде (выходы 8–11); захват лока одной
    функцией в `live_gate` — долг зоны живого контура (№572)."""
    import json
    root = tmp_path / "data"
    (root / "logs").mkdir(parents=True)
    console, outcome = live_gate.hold_meeting(root, sleep=lambda s: None)
    try:
        assert outcome == live_gate.file_locks.TAKEN
        run = subprocess.run([sys.executable, str(ROOT / "src" / "daemon.py")], cwd=ROOT,
                             env=dict(os.environ, CHAROITE_ROOT=str(root)),
                             capture_output=True, text=True, timeout=60)   # меньше потолка pytest (выход 13, M1)
    finally:
        console.close()
    events = []
    for line in run.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    refusals = [e for e in events if e.get("type") == "status" and e.get("error") is True]
    assert refusals and refusals[0].get("reason") == "console_meeting", (run.stdout[:400], run.stderr[-400:])
    assert run.returncode == 0 and "Traceback" not in run.stderr, (
        f"после отказа демон пошёл стартовать (выход 12, DS I3): {run.returncode}, {run.stderr[-400:]!r}")
    swift = (ROOT / "app" / "Sources" / "CharoiteApp" / "Services" / "SuflerEndOfRecording.swift").read_text(encoding="utf-8")
    m = re.search(r"fatalReasons:\s*Set<String>\s*=\s*\[([^\]]*)\]", swift)   # разбор как в test_charoite_paths.py
    assert m, "в SuflerEndOfRecording.swift нет набора fatalReasons"
    assert refusals[0]["reason"] in set(re.findall(r'"([^"]+)"', m.group(1))), (
        "причина демона неизвестна приложению — три перезапуска вместо рецепта (выход 13, Haiku C1)")


def test_rebuild_no_longer_reads_the_meeting_lock():
    """Пересборка судит о записи по заявке на сам `.pcm`, а не по локу встречи
    (№728): после автоперезапуска лок держит новая встреча, а консоль на томе
    без flock пишет без него. Прежний мост `_daemon_alive` снят."""
    import rebuild_transcript as rt

    assert not hasattr(rt, "_daemon_alive")


def test_console_mark_survives_a_reader_probe(tmp_path):
    """Признак берётся с ретраями, как лок встречи: SH-проба читателя в миг захвата
    не отнимает его на всю встречу (выход 5 №725, Sonnet I1)."""
    path = live_gate.console_lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    probe = path.open("a")
    fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def release(_seconds):
        if not probe.closed:
            probe.close()

    f, outcome = live_gate.hold_meeting(tmp_path, sleep=release)
    try:
        assert outcome == live_gate.file_locks.TAKEN and f.mark is not None
        assert live_gate.refusal(tmp_path)[1] == "console_meeting"
    finally:
        f.close()


def test_night_wait_cap_never_returns_a_negative_wait(monkeypatch):
    """Остаток бюджета ушёл в минус между проверкой и вызовом гейта — ждать
    нечего; отрицательный потолок ронял `wait_while_live` (вход in3 №621)."""
    monkeypatch.delenv(live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    assert live_gate.night_wait_cap(-5.0) == 0.0
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "мусор")   # ветка «мусор в переменной»
    assert live_gate.night_wait_cap(-5.0) == 0.0


def test_fits_is_one_arithmetic_for_every_budget():
    assert live_gate.fits(None, 0.0, 10**9, now=10**9)
    assert live_gate.fits(100, 0.0, 40, now=60)
    assert not live_gate.fits(100, 0.0, 41, now=60)


_UNTILS = [None, "12800", "30000", "11000", "10000", "мусор", "inf", "-inf", "nan"]
# Ночь до резерва хвоста при стене 10000 и резерве 1800: None — потолка нет
_NIGHT = {None: None, "12800": 1000.0, "30000": 18200.0, "11000": 0.0, "10000": 0.0,
          "мусор": None, "inf": None, "-inf": 0.0, "nan": None}


@pytest.mark.parametrize("until", _UNTILS)
@pytest.mark.parametrize("budget, spent", [(None, 0.0), (7200.0, 0.0), (7200.0, 7000.0), (100.0, 200.0)])
def test_step_left_is_the_smaller_of_the_budget_and_the_night(monkeypatch, until, budget, spent):
    """№749: остаток шага — меньшее из своего бюджета (монотонные часы шага)
    и ночи до резерва хвоста (стена); каждое значение окружения пересечено
    с «без бюджета» (выход out1 №749, DS M1)."""
    if until is None:
        monkeypatch.delenv(live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    else:
        monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, until)
    night = _NIGHT[until]
    own = None if budget is None else budget - spent
    expected = night if own is None else own if night is None else min(own, night)
    assert live_gate.night_left(1800.0, wall=lambda: 10000.0) == night
    got = live_gate.step_left(budget, 500.0, 500.0 + spent, tail_s=1800.0, wall=lambda: 10000.0)
    assert got == expected


def test_step_left_reads_the_wall_on_every_call(monkeypatch):
    """Сон машины: монотонные часы шага стоят, стена ушла — резерв хвоста
    обязан сократиться, а не остаться числом со старта (выход out1 №749)."""
    wall = [10000.0]
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, "14000")
    assert live_gate.step_left(7200.0, 0.0, 0.0, tail_s=1800.0, wall=lambda: wall[0]) == 2200.0
    wall[0] += 2000                                   # проспали 2000 с, monotonic стоит
    assert live_gate.step_left(7200.0, 0.0, 0.0, tail_s=1800.0, wall=lambda: wall[0]) == 200.0


def test_night_left_reads_the_real_clock_and_the_tail_constant(monkeypatch):
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, str(time.time() + live_gate.NIGHT_TAIL_S + 100))
    assert 95 <= live_gate.night_left() <= 100


@pytest.mark.parametrize("until", _UNTILS)
def test_every_reader_of_the_night_ceiling_parses_it_the_same(monkeypatch, until):
    """Один разбор потолка (`_until`): конец ночи, потолок ожидания и остаток
    ночи согласны на каждом значении окружения (выход out1 №749, DS M6)."""
    if until is None:
        monkeypatch.delenv(live_gate.NIGHTLY_UNTIL_ENV, raising=False)
    else:
        monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, until)
    over = live_gate.night_is_over(now=lambda: 10000.0)
    cap = live_gate.night_wait_cap(1e9, now=lambda: 10000.0)
    left = live_gate.night_left(0.0, wall=lambda: 10000.0)
    until_s = live_gate._until()
    assert over == (until_s is not None and 10000.0 > until_s)   # граница — через разбор (выход out2, Haiku M2)
    if left is None:
        assert until_s is None and cap == 1e9
    else:
        assert cap == left == max(0.0, until_s - 10000.0)


def test_meeting_wait_is_measured_by_the_ceiling_clock(tmp_path, monkeypatch):
    """Выход out2 №749 (DS I1, Haiku I1): потолок ожидания встречи — по стене,
    а отмерялся монотонными секундами, которые во сне стоят. Машина проспала
    1000 с при потолке 600 — ожидание обязано кончиться на первом же опросе."""
    wall = [1.0e9]
    monkeypatch.setenv(live_gate.NIGHTLY_UNTIL_ENV, str(wall[0] + 2400))
    sleeps = []

    def sleep(sec):
        sleeps.append(sec)
        wall[0] += 1000 if len(sleeps) == 1 else 20

    assert live_gate.night_window_open(tmp_path, "проба", lambda m: None, default=600.0,
                                       clock=lambda: wall[0], sleep=sleep,
                                       alive=lambda r: True) is True
    assert len(sleeps) == 1, sleeps
