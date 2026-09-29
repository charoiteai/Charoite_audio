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
    ("sys.exit(3)\n", fp.FAILED, "код 3"),
    (f"sys.exit({EXIT_ENGINE_UNAVAILABLE})\n", fp.UNAVAILABLE, "движок недоступен"),
])
def test_finish_tells_the_exit_of_the_child(tmp_path, tail, kind, reason):
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
        def __init__(self, proc):
            super().__init__(proc)
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
