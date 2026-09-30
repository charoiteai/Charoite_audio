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


def test_the_registry_keeps_a_child_whose_owner_dropped_it(tmp_path):
    """Реестр держит `Popen` сильной ссылкой: хозяин бросил ребёнка, сборка мусора
    прошла — уборка при выходе его всё равно видит."""
    import gc
    import subprocess
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
