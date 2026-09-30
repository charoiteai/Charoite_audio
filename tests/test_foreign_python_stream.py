"""Дверь долгого процесса `foreign_python.spawn_stream` (№478, PR A2): исходы и отказы,
которые не видел ни один тест до мутатора диапазона, — на настоящих процессах.

Рукопожатие, строки протокола, отмена и журнал 0600 держит `test_live_nemotron.py`;
здесь — `pid` и «жив», вход после закрытия, исход `finish` кодом выхода и потолком,
отказы запуска, закрытый вывод без рукопожатия, потолок рукопожатия включительно и
ребёнок, которому на рукопожатие нужно больше шага опроса.
"""
import os
import pathlib
import sys
import threading
import time

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import foreign_python as fp  # noqa: E402
from exit_codes import EXIT_ENGINE_UNAVAILABLE  # noqa: E402

READY = 'print(json.dumps({"type": "ready", "pid": os.getpid()}), flush=True)\n'
WAIT_EOF = 'sys.stdin.buffer.read()\n'


def _child(tmp_path, body: str) -> pathlib.Path:
    script = tmp_path / "child.py"
    script.write_text("import json, os, sys, time\n" + body, encoding="utf-8")
    return script


def _spawn(script, tmp_path, python=sys.executable, **kw):
    kw.setdefault("handshake_timeout", 20.0)
    kw.setdefault("stderr_path", tmp_path / "child.err")
    return fp.spawn_stream(python, script, [], role="audio", on_message=lambda m: None,
                           on_eof=lambda: None, **kw)


def test_the_stream_knows_the_pid_of_its_child_and_whether_it_lives(tmp_path):
    stream, out = _spawn(_child(tmp_path, READY + WAIT_EOF), tmp_path)
    assert out.kind == fp.OK
    assert stream.pid == out.payload["pid"]
    assert stream.alive() is True
    stream.close_input()
    assert stream.finish(10).kind == fp.OK
    assert stream.alive() is False


def test_writing_after_the_input_is_closed_is_a_broken_pipe_and_closing_twice_is_quiet(tmp_path):
    stream, out = _spawn(_child(tmp_path, READY + WAIT_EOF), tmp_path)
    assert out.ok
    stream.close_input()
    stream.close_input()
    with pytest.raises(BrokenPipeError):
        stream.write(b"\x00\x00")
    stream.finish(10)


@pytest.mark.parametrize("tail, kind, reason", [
    ("sys.exit(3)\n", fp.FAILED, "код 3: без вывода"),
    ("sys.stderr.write('Traceback\\n  ...\\nRuntimeError: фронт впереди\\n\\n'); sys.exit(1)\n",
     fp.FAILED, "код 1: RuntimeError: фронт впереди"),
    (f"sys.exit({EXIT_ENGINE_UNAVAILABLE})\n", fp.UNAVAILABLE, "движок недоступен"),
    (f"sys.stderr.write('нет весов\\n'); sys.exit({EXIT_ENGINE_UNAVAILABLE})\n", fp.UNAVAILABLE, "нет весов"),
])
def test_finish_tells_the_exit_of_the_child(tmp_path, tail, kind, reason):
    """Причина выхода после рукопожатия — последняя непустая строка журнала ребёнка, тем
    же правилом, что до рукопожатия (выходной круг фикса A2 №478, M1)."""
    stream, out = _spawn(_child(tmp_path, READY + WAIT_EOF + tail), tmp_path)
    assert out.ok
    stream.close_input()
    assert stream.finish(10) == fp.Outcome(kind, reason=reason)


def test_a_child_that_does_not_leave_by_the_ceiling_is_killed(tmp_path):
    stream, out = _spawn(_child(tmp_path, READY + "time.sleep(60)\n"), tmp_path)
    assert out.ok
    stream.close_input()
    got = stream.finish(0.3)
    assert got.kind == fp.FAILED and got.reason == "не вышел за 0 с — убит"
    assert stream.alive() is False


def test_refusals_before_the_child_runs(tmp_path):
    script = _child(tmp_path, READY)
    stream, out = _spawn(script, tmp_path, stderr_path=tmp_path / "нет" / "child.err")
    assert stream is None and out.kind == fp.FAILED and "журнал ребёнка не открылся" in out.reason
    stream, out = _spawn(script, tmp_path, python=tmp_path / "нет-python")
    assert stream is None and out.reason == f"нет интерпретатора {tmp_path / 'нет-python'}"
    stream, out = _spawn(script, tmp_path, python=tmp_path)             # каталог — не запускается
    assert stream is None and out.kind == fp.FAILED and out.reason.startswith("не запустился: ")


def test_a_child_that_closes_its_output_without_a_handshake_and_stays_is_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "EXIT_WAIT_S", 0.3)
    script = _child(tmp_path, 'os.close(1)\nprint(os.getpid(), file=sys.stderr, flush=True)\ntime.sleep(60)\n')
    stream, out = _spawn(script, tmp_path)
    assert stream is None and out == fp.Outcome(fp.FAILED, reason="закрыл вывод без рукопожатия — убит")
    pid = int((tmp_path / "child.err").read_text().split()[0])
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_child_without_its_log_fails_with_its_code_and_no_output(tmp_path):
    """Журнал ребёнка пропал (удалён) — причина всё равно кодом, а не падением двери."""
    err = tmp_path / "child.err"
    script = _child(tmp_path, f"os.remove({str(err)!r})\nsys.exit(3)\n")
    stream, out = _spawn(script, tmp_path, stderr_path=err)
    assert stream is None and out == fp.Outcome(fp.FAILED, reason="код 3: без вывода")


def test_the_handshake_ceiling_is_inclusive(tmp_path):
    """Часы стоят ровно на потолке — это уже потолок, а не «ещё чуть-чуть»."""
    ticks = iter([0.0] + [1.0] * 10_000)
    stream, out = _spawn(_child(tmp_path, "time.sleep(60)\n"), tmp_path, handshake_timeout=1.0,
                         clock=lambda: next(ticks))
    assert stream is None and out == fp.Outcome(fp.FAILED, reason="нет рукопожатия за 1 с — убит")


def test_a_handshake_slower_than_the_poll_step_is_waited_for(tmp_path):
    """Модель грузится дольше шага опроса — рукопожатие всё равно ждут до потолка."""
    stream, out = _spawn(_child(tmp_path, f"time.sleep({fp.HANDSHAKE_POLL_S * 3})\n" + READY + WAIT_EOF),
                         tmp_path)
    assert out.kind == fp.OK
    stream.close_input()
    stream.finish(10)


def test_a_given_up_stream_calls_back_no_one(tmp_path, monkeypatch):
    """Потолок и отмена бросают поток: ребёнок убит, и его поздние строки хозяину не
    доходят — исход уже отдан."""
    made = []

    class Spy(fp.StreamProcess):
        def __init__(self, proc, stderr_path=None):
            super().__init__(proc, stderr_path)
            made.append(self)

    monkeypatch.setattr(fp, "StreamProcess", Spy)
    cancel = threading.Event()
    cancel.set()
    stream, out = _spawn(_child(tmp_path, "time.sleep(60)\n"), tmp_path, cancel=cancel)
    assert stream is None and "отменено" in out.reason
    assert made and made[0]._abandoned is True
    calls = []
    made[0]._deliver(calls.append, "поздняя строка")
    assert calls == []
    t0 = time.monotonic()
    stream, out = _spawn(_child(tmp_path, "time.sleep(60)\n"), tmp_path, handshake_timeout=0.5)
    assert stream is None and "рукопожати" in out.reason and time.monotonic() - t0 < 10
    assert made[1]._abandoned is True
    ticks = iter([0.0])                     # часы ломаются на первой проверке — читатель уже идёт
    stream, out = _spawn(_child(tmp_path, "time.sleep(60)\n"), tmp_path, clock=lambda: next(ticks))
    assert stream is None and out.reason.startswith("дверь упала после запуска: StopIteration")
    assert made[2]._abandoned is True


def test_kill_nowait_kills_without_reaping(tmp_path):
    """SIGKILL без ожидания: ребёнок умирает, а `finish` потом собирает его выход."""
    stream, out = _spawn(_child(tmp_path, READY + "time.sleep(60)\n"), tmp_path)
    assert out.ok
    stream.kill_nowait()
    got = stream.finish(10)
    assert got.kind == fp.FAILED and got.reason == f"код {-9}: без вывода"
    assert stream.alive() is False


# ------------------------------------------------ ребёнок не переживает родителя (№533)

#: Родитель — настоящий процесс: заводит ребёнка через дверь, печатает его pid и выходит
#: названным способом. Ребёнок держит рукопожатие и спит, не читая вход, — как модель,
#: повисшая в `feed`: EOF его не будит, уйти он может только убитым.
PARENT = '''
import gc, json, os, pathlib, signal, sys, threading, time
sys.path.insert(0, {src!r})
import foreign_python as fp
how, child, err = sys.argv[1], sys.argv[2], sys.argv[3]
stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())    # как у демона: SIGTERM — штатный стоп
stream, out = fp.spawn_stream(sys.executable, pathlib.Path(child), [], stderr_path=pathlib.Path(err),
                              handshake_timeout=20.0, role="audio",
                              on_message=lambda m: None, on_eof=lambda: None)
print(stream.pid, flush=True)
if how == "raise":
    raise RuntimeError("слой не завёлся")
if how == "sigterm":
    stop.wait(30)
if how == "dropped":
    del stream
    gc.collect()
sys.exit(0)
'''

HUNG = READY + 'time.sleep(60)\n'


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.parametrize("how", ["exit", "raise", "sigterm", "dropped"])
def test_a_hung_child_dies_with_its_parent_however_the_parent_leaves(tmp_path, how):
    """Выход родителя мимо `finally` хозяина (исключение до `try`, `sys.exit`, SIGTERM с
    обработчиком, хозяин бросил ссылку) — уборка двери при выходе убивает ребёнка."""
    import subprocess
    parent = tmp_path / "parent.py"
    parent.write_text(PARENT.format(src=str(SRC)), encoding="utf-8")
    child = _child(tmp_path, HUNG)
    proc = subprocess.Popen([sys.executable, str(parent), how, str(child), str(tmp_path / "child.err")],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    pid = None
    try:
        pid = int(proc.stdout.readline())
        if how == "sigterm":
            proc.send_signal(15)
        proc.wait(20)
        deadline = time.monotonic() + 2.0
        while not _gone(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert _gone(pid), f"ребёнок пережил родителя ({how})"
    finally:
        proc.kill()
        if pid is not None and not _gone(pid):
            os.kill(pid, 9)


def test_the_registry_keeps_a_child_whose_owner_dropped_it(tmp_path, monkeypatch):
    """Реестр держит `Popen` сильной ссылкой: хозяин бросил ребёнка, сборка мусора
    прошла — уборка при выходе его всё равно видит."""
    import gc
    import subprocess
    monkeypatch.setattr(fp, "_exiting", False)      # уборка ниже взводит «выходим» — вернуть после теста
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pid = proc.pid
    try:
        fp._adopt(proc)
        del proc
        gc.collect()
        assert pid in fp._children
        fp._kill_children()
        fp._children[pid].wait(10)
    finally:
        if not _gone(pid):
            os.kill(pid, 9)
        fp._children.pop(pid, None)


def test_the_registry_forgets_children_that_left(tmp_path):
    import subprocess
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(10)
    fp._adopt(gone)
    stays = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        fp._adopt(stays)
        assert gone.pid not in fp._children and stays.pid in fp._children
    finally:
        stays.kill()
        stays.wait(10)
        fp._children.pop(stays.pid, None)


def test_a_failed_adoption_kills_the_child_and_fails_the_door(tmp_path, monkeypatch):
    """Регистрация ребёнка — внутри границы двери: её сбой убивает ребёнка, исход FAILED."""
    def boom(proc):
        (tmp_path / "pid").write_text(str(proc.pid))
        raise RuntimeError("реестр сломан")
    monkeypatch.setattr(fp, "_adopt", boom)
    stream, out = _spawn(_child(tmp_path, HUNG), tmp_path)
    assert stream is None and out.kind == fp.FAILED and "реестр сломан" in out.reason
    pid = int((tmp_path / "pid").read_text())
    deadline = time.monotonic() + 5
    while not _gone(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _gone(pid)


def test_the_exit_hook_does_not_wait_for_a_held_registry(tmp_path, monkeypatch):
    """Нить, застрявшая с замком реестра на выходе, не держит уборку: потолок — секунда."""
    import subprocess
    monkeypatch.setattr(fp, "_exiting", False)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    release = threading.Event()
    holder = threading.Thread(target=lambda: (fp._children_lock.acquire(), release.wait(10),
                                              fp._children_lock.release()))
    try:
        fp._children[proc.pid] = proc
        holder.start()
        time.sleep(0.05)
        t0 = time.monotonic()
        fp._kill_children()
        took = time.monotonic() - t0
        assert took < 1.5, f"уборка ждала {took:.2f} с"
        assert proc.wait(5) is not None
    finally:
        release.set()
        holder.join(5)
        if proc.poll() is None:
            proc.kill()
        fp._children.pop(proc.pid, None)


#: Родитель, чей долгий ребёнок заводится нитью-демоном уже после уборки при выходе:
#: хук, зарегистрированный ДО импорта двери, выполняется после её уборки (atexit — LIFO)
#: и держит процесс, пока нить доходит до `spawn_stream`.
LATE_PARENT = """
import atexit, pathlib, sys, threading, time
exit_began = threading.Event()
atexit.register(lambda: (exit_began.set(), time.sleep(1.0)))
sys.path.insert(0, {src!r})
import foreign_python as fp
child, err = sys.argv[1], sys.argv[2]

def late():
    exit_began.wait(10)
    stream, out = fp.spawn_stream(sys.executable, pathlib.Path(child), [], stderr_path=pathlib.Path(err),
                                  handshake_timeout=20.0, role="audio",
                                  on_message=lambda m: None, on_eof=lambda: None)
    pathlib.Path(child + ".out").write_text(out.kind + " " + out.reason)
threading.Thread(target=late, daemon=True).start()
raise RuntimeError("слой не завёлся")
"""


def test_a_child_started_after_the_exit_hook_is_killed_by_the_door(tmp_path):
    """После уборки при выходе дверь новых детей не выдаёт: ребёнок, запущенный нитью-демоном
    позже снимка реестра, убит своей же границей, а не забыт (финальный Opus по №533, M1)."""
    import subprocess
    parent = tmp_path / "parent.py"
    parent.write_text(LATE_PARENT.format(src=str(SRC)), encoding="utf-8")
    child = _child(tmp_path, 'open(sys.argv[0] + ".pid", "w").write(str(os.getpid()))\n' + HUNG)
    pid_file = pathlib.Path(str(child) + ".pid")
    proc = subprocess.run([sys.executable, str(parent), str(child), str(tmp_path / "child.err")],
                          capture_output=True, timeout=30)
    assert proc.returncode != 0
    out_file = pathlib.Path(str(child) + ".out")
    assert out_file.exists(), "нить не дошла до конца запуска ребёнка — опыт не состоялся"
    outcome = out_file.read_text()
    time.sleep(0.3)
    if pid_file.exists():
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3.0
        while not _gone(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        if not _gone(pid):
            os.kill(pid, 9)
            raise AssertionError(f"ребёнок пережил родителя; исход двери: {outcome}")
    assert "выходит" in outcome, f"дверь выдала ребёнка на выходе: {outcome}"


def test_the_door_refuses_new_children_once_the_process_is_leaving(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "_exiting", True)
    child = _child(tmp_path, 'open(sys.argv[0] + ".pid", "w").write(str(os.getpid()))\n' + HUNG)
    stream, out = _spawn(child, tmp_path)
    assert stream is None and out.kind == fp.FAILED and "выходит" in out.reason
    time.sleep(1.0)                          # запущенный ребёнок успел бы записать свой pid
    assert not pathlib.Path(str(child) + ".pid").exists(), "на выходе дверь всё-таки подняла ребёнка"


def test_adoption_is_refused_once_the_process_is_leaving(monkeypatch):
    """Вторая линия: ребёнок, проскочивший проверку до `Popen` (нить шла параллельно уборке),
    не ложится в снятый реестр — `_adopt` бросает, и граница двери его убивает."""
    import subprocess
    monkeypatch.setattr(fp, "_exiting", True)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with pytest.raises(RuntimeError, match="выходит"):
            fp._adopt(proc)
        assert proc.pid not in fp._children
    finally:
        proc.kill()
        proc.wait(10)
