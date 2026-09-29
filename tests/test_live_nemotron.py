"""Живой поток Nemotron в тени (№478, PR A2): движок `--stream`, дверь долгого процесса,
тень в демоне.

Три слоя и сквозной гейт.
- Сторона движка: цикл `run_stream` на поддельной модели — блоки, перенос нечётного
  байта, фронт после блока, финал; «открытый» сегмент — в целых кадрах.
- Дверь `foreign_python.spawn_stream` на настоящих процессах: рукопожатие, строки
  протокола, отказ кодом, потолок и отмена рукопожатия, чистое окружение, журнал 0600.
- Тень на поддельной двери: исход каждого чанка, разрыв оси, отставание, потолок
  ожидания, остановка — и инвариант «строка на каждый принятый чанк».
- Гейт «хаб → ребёнок → журнал»: настоящий хаб, настоящий цикл движка в процессе с
  поддельной моделью, звук с известной сменой голоса после `start0`. Сдвиг оси на
  `start0` или на байт перекрасил бы чанки у границы.
"""
import ctypes
import importlib.abc
import json
import os
import pathlib
import stat
import sys
import threading
import time
import types

import numpy as np
import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import audio  # noqa: E402
import diarize_nemotron as dn  # noqa: E402
import foreign_python as fp  # noqa: E402
import live_nemotron as ln  # noqa: E402
from exit_codes import EXIT_ENGINE_UNAVAILABLE  # noqa: E402

SR = 16000


def _wait(pred, timeout=10.0, what="условие"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError(f"не дождались: {what}")


def _journal(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------ сторона движка

class _FakeModelStream:
    """Поддельный поток: копит поданное, выдаёт кадры с задержкой `lag` сэмплов."""

    def __init__(self, frame_s=0.08, lag=int(1.04 * SR)):
        self.fed = []
        self.frame = int(frame_s * SR)
        self.lag = lag
        self.frames_processed = 0

    def feed(self, pcm):
        self.fed.append(np.array(pcm))
        total = sum(len(p) for p in self.fed)
        new = max(0, (total - self.lag) // self.frame)
        out = [{"start": round(f * self.frame / SR, 3), "end": round((f + 1) * self.frame / SR, 3),
                "speaker": "nem0"} for f in range(self.frames_processed, new)]
        self.frames_processed = max(self.frames_processed, new)
        return out

    def close(self):
        total = sum(len(p) for p in self.fed)
        new = total // self.frame
        out = [{"start": round(f * self.frame / SR, 3), "end": round((f + 1) * self.frame / SR, 3),
                "speaker": "nem1"} for f in range(self.frames_processed, new)]
        self.frames_processed = new
        return out


def test_the_stream_loop_carries_odd_bytes_and_reports_a_front_after_every_block():
    rng = np.random.default_rng(3)
    pcm = rng.integers(-30000, 30000, SR * 3 + 123, dtype=np.int16)
    raw = pcm.astype("<i2").tobytes()
    pieces, pos = [], 0
    for size in rng.integers(1, 5000, 10_000):          # куски любой длины, в том числе нечётные
        if pos >= len(raw):
            break
        pieces.append(raw[pos:pos + int(size)])
        pos += int(size)
    pieces.append(b"")
    it = iter(pieces)
    out = []
    stream = _FakeModelStream()
    assert dn.run_stream(stream, frame_s=0.08, read=lambda n: next(it), emit=out.append, step=4000) == 0
    got = np.concatenate(stream.fed)
    assert np.array_equal(got, pcm.astype(np.float32) / 32768.0), "звук дошёл до модели без сдвига на байт"
    assert [len(p) for p in stream.fed[:-1]] == [4000] * (len(stream.fed) - 1)
    fronts = [m for m in out if m["type"] == "front"]
    assert len(fronts) == len(stream.fed) + 1, "фронт после каждого блока и финальный"
    assert fronts[-1]["final"] is True and fronts[-1]["fed"] == len(pcm)
    assert all("cpu_s" in f and "rss_mb" in f for f in fronts), "цена процесса — в каждом фронте"
    feds = [f["fed"] for f in fronts]
    assert feds == sorted(feds)


def test_a_segment_is_open_only_when_it_reaches_the_front_in_whole_frames():
    out = []
    frame_s = 0.08
    segs = [{"start": 0.0, "end": 0.24, "speaker": "nem0"},          # 3 кадра — до фронта
            {"start": 0.24, "end": 0.4, "speaker": "nem2"}]          # 5 кадров — фронт
    dn._emit_segments(segs, 5, frame_s, out.append, final=False)
    assert [(m["slot"], m["open"]) for m in out] == [(0, False), (2, True)]
    out.clear()
    dn._emit_segments(segs, 5, frame_s, out.append, final=True)
    assert [m["open"] for m in out] == [False, False], "финал закрывает всё"


def test_stream_without_the_engine_is_unavailable_and_loads_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dn, "availability", lambda path: "нет весов")
    monkeypatch.setattr(dn, "serve_stream", lambda *a: pytest.fail("поток без движка"))
    assert dn.main(["--stream", "--model", str(tmp_path)]) == EXIT_ENGINE_UNAVAILABLE
    assert capsys.readouterr().err == "нет весов\n"


def test_stream_mode_serves_the_stream_with_the_preset(tmp_path, monkeypatch):
    monkeypatch.setattr(dn, "availability", lambda path: None)
    calls = []
    monkeypatch.setattr(dn, "serve_stream", lambda model, preset: calls.append((model, preset)) or 0)
    assert dn.main(["--stream", "--model", str(tmp_path)]) == 0
    assert dn.main(["--stream", "--model", str(tmp_path), "--preset", "very_low"]) == 0
    assert calls == [(tmp_path, "low"), (tmp_path, "very_low")]


# ------------------------------------------------ дверь долгого процесса

def _child(tmp_path, body: str) -> pathlib.Path:
    script = tmp_path / "child.py"
    script.write_text("import json, os, sys, time\n" + body, encoding="utf-8")
    return script


READY = 'print(json.dumps({"type": "ready", "proto": 1, "env": os.environ.get("PYTHONPATH")}), flush=True)\n'


def _spawn(script, tmp_path, **kw):
    got, eof = [], threading.Event()
    kw.setdefault("handshake_timeout", 20.0)
    stream, out = fp.spawn_stream(sys.executable, script, [], stderr_path=tmp_path / "child.err",
                                  role="audio", on_message=kw.pop("on_message", got.append),
                                  on_eof=eof.set, **kw)
    return stream, out, got, eof


def test_the_door_hands_over_the_handshake_messages_and_the_end(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/чужой/путь")
    script = _child(tmp_path, READY + (
        'data = b""\n'
        'while True:\n'
        '    part = os.read(0, 65536)\n'
        '    if not part:\n'
        '        break\n'
        '    data += part\n'
        'print("не JSON", flush=True)\n'
        'print(json.dumps({"type": "front", "fed": len(data) // 2}), flush=True)\n'))
    stream, out, got, eof = _spawn(script, tmp_path)
    assert out.kind == fp.OK and out.payload["type"] == "ready"
    assert out.payload["env"] is None, "окружение ребёнка — чистое, PYTHONPATH родителя снят"
    stream.write(b"\x01\x00" * 1000)
    stream.close_input()
    assert eof.wait(20)
    assert got == [{"type": "front", "fed": 1000}]
    assert stream.nonjson == 1
    assert stream.finish(10).kind == fp.OK
    assert stat.S_IMODE(os.stat(tmp_path / "child.err").st_mode) == 0o600


def test_a_failing_callback_does_not_stop_the_reader(tmp_path):
    script = _child(tmp_path, READY + (
        'print(json.dumps({"type": "a"}), flush=True)\n'
        'print(json.dumps({"type": "b"}), flush=True)\n'))
    seen = []

    def on_message(m):
        seen.append(m["type"])
        if m["type"] == "a":
            raise RuntimeError("сломался обработчик")

    stream, out, _got, eof = _spawn(script, tmp_path, on_message=on_message)
    assert out.ok and eof.wait(20)
    assert seen == ["a", "b"]
    assert stream.callback_errors == 1 and "сломался обработчик" in stream.callback_error
    stream.finish(10)


def test_an_engine_that_cannot_work_is_unavailable_with_its_reason(tmp_path):
    script = _child(tmp_path, f'print("нет весов", file=sys.stderr)\nsys.exit({EXIT_ENGINE_UNAVAILABLE})\n')
    stream, out, _got, eof = _spawn(script, tmp_path)
    assert stream is None and out.kind == fp.UNAVAILABLE and out.reason == "нет весов"
    assert not eof.is_set(), "без рукопожатия обратных вызовов нет"


def test_a_crash_before_the_handshake_is_a_failure(tmp_path):
    script = _child(tmp_path, 'raise SystemExit("упал при загрузке")\n')
    stream, out, _got, _eof = _spawn(script, tmp_path)
    assert stream is None and out.kind == fp.FAILED and "упал при загрузке" in out.reason


def test_no_handshake_in_time_kills_the_child(tmp_path):
    script = _child(tmp_path, 'print(os.getpid(), file=sys.stderr, flush=True)\ntime.sleep(60)\n')
    t0 = time.monotonic()
    stream, out, _got, _eof = _spawn(script, tmp_path, handshake_timeout=1.0)
    assert stream is None and out.kind == fp.FAILED and "рукопожати" in out.reason
    assert time.monotonic() - t0 < 10
    pid = int((tmp_path / "child.err").read_text().split()[0])
    _wait(lambda: not _alive(pid), what="ребёнок убит")


def test_cancel_stops_waiting_for_the_handshake(tmp_path):
    script = _child(tmp_path, 'time.sleep(60)\n')
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    t0 = time.monotonic()
    stream, out, _got, _eof = _spawn(script, tmp_path, handshake_timeout=60.0, cancel=cancel)
    assert stream is None and "отменено" in out.reason
    assert time.monotonic() - t0 < 10


def test_the_child_log_is_private_even_if_it_existed(tmp_path):
    err = tmp_path / "child.err"
    err.write_text("старое\n")
    os.chmod(err, 0o644)
    script = _child(tmp_path, READY)
    stream, out, _got, eof = _spawn(script, tmp_path)
    assert out.ok and eof.wait(20)
    stream.finish(10)
    assert stat.S_IMODE(os.stat(err).st_mode) == 0o600


def test_a_door_failure_after_launch_kills_the_child(tmp_path, monkeypatch):
    """Нить-читатель не завелась (потоки исчерпаны) — ребёнок с моделью не остаётся без
    хозяина: убит, исход FAILED значением (выходной круг 1 по №478 A2, I1)."""
    import threads
    script = _child(tmp_path, READY + 'time.sleep(60)\n')
    real = threads.spawn

    def no_reader(target, *, name, role, **kw):
        if name == "foreign-stream-reader":
            raise RuntimeError("can't start new thread")
        return real(target, name=name, role=role, **kw)

    monkeypatch.setattr(threads, "spawn", no_reader)
    made = []
    real_popen = fp.subprocess.Popen

    def popen(*a, **k):
        made.append(real_popen(*a, **k))
        return made[-1]

    monkeypatch.setattr(fp.subprocess, "Popen", popen)
    stream, out, _got, _eof = _spawn(script, tmp_path)
    assert stream is None and out.kind == fp.FAILED and "дверь упала" in out.reason
    assert made and made[0].poll() is not None, "ребёнок пережил сбой двери"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


# ------------------------------------------------ тень на поддельной двери

READY_OK = {"type": "ready", "proto": dn.STREAM_PROTO, "sr": SR, "preset": "low",
            "frame_s": 0.08, "step": dn.STREAM_STEP}


class _FakeChild:
    """Ребёнок без процесса: копит байты, умеет «не принимать» вход."""

    def __init__(self):
        self.pid = 4242
        self.bytes = bytearray()
        self.closed = threading.Event()
        self.killed = threading.Event()
        self.writing = threading.Event()          # писатель тени дошёл до трубы
        self.gate = threading.Event()
        self.gate.set()
        self.exited = False                        # вышел сам, без убийства
        self.exit = fp.Outcome(fp.OK)
        self.nonjson = 0
        self.callback_errors = 0

    def write(self, data):
        self.writing.set()
        self.gate.wait()
        if self.killed.is_set():
            raise BrokenPipeError("убит")
        self.bytes += data

    def close_input(self):
        self.closed.set()

    def alive(self):
        return not (self.killed.is_set() or self.exited)

    def kill(self):
        self.killed.set()
        self.gate.set()

    def finish(self, timeout):
        return self.exit


class _Door:
    """Поддельная дверь: отдаёт ребёнка и запоминает обратные вызовы."""

    def __init__(self, ready=READY_OK, refuse=None, block=False, ok_after_cancel=False):
        self.child = _FakeChild()
        self.ready, self.refuse, self.block = ready, refuse, block
        self.ok_after_cancel = ok_after_cancel      # ребёнок поднялся, когда хозяин уже остановился
        self.on_message = self.on_eof = None

    def __call__(self, python, script, args, *, stderr_path, handshake_timeout, role,
                 on_message, on_eof, cancel):
        self.on_message, self.on_eof = on_message, on_eof
        if self.block or self.ok_after_cancel:
            cancel.wait(30)
        if self.block:
            return None, fp.Outcome(fp.FAILED, reason="ожидание рукопожатия отменено — убит")
        if self.refuse:
            return None, self.refuse
        return self.child, fp.Outcome(fp.OK, payload=self.ready)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _shadow(tmp_path, door=None, clock=None, memory=lambda: None):
    """Память машины по умолчанию не спрашивается: тест не зависит от давления на машине прогона."""
    says = []
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="2026-09-29_120000", say=says.append,
                   clock=clock or time.monotonic, memory=memory)
    door = door or _Door()
    sh.begin(python="python", script=tmp_path / "engine.py", args=[], errlog=tmp_path / "live.err", spawn=door)
    return sh, door, says


def _live(tmp_path, **kw):
    sh, door, says = _shadow(tmp_path, **kw)
    _wait(lambda: sh.state == ln.LIVE, what="тень живёт")
    return sh, door, says


def _placed(number, start, n, label="blackhole"):
    return audio.Placed("Собеседник", np.zeros(n, dtype=np.float32), start, (label, number))


def _chunks(path):
    return [line for line in _journal(path) if line["type"] == "chunk"]


def test_a_chunk_before_the_stream_starts_gets_its_line_at_once(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.note_chunk(_placed(0, 0, SR), "off")
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert line["outcome"] == ln.BEFORE_STREAM and line["chunk"] == 0 and line["state"] == "off"
    sh.stop()
    door.on_eof()


def test_a_chunk_is_labelled_when_the_front_passes_its_end(tmp_path):
    sh, door, _ = _live(tmp_path)
    block = np.full(SR, 0.25, dtype=np.float32)
    for k in range(4):
        sh.on_frame("blackhole", 5 * SR + k * SR, block)     # start0 = 5 с на оси хаба
    _wait(lambda: len(door.child.bytes) == 4 * SR * 2, what="звук ушёл ребёнку")
    assert bytes(door.child.bytes[:SR * 2]) == audio.pcm16(block), "ребёнку — те же байты, что в запись"
    sh.note_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces")
    assert _chunks(tmp_path / "live.jsonl") == [], "фронта ещё нет — чанк ждёт"
    door.on_message({"type": "seg", "start": 0.0, "end": 1.0, "slot": 0, "open": False})
    door.on_message({"type": "seg", "start": 1.0, "end": 2.0, "slot": 3, "open": True})
    door.on_message({"type": "front", "fed": 2 * SR, "frames": 25, "cpu_s": 1.5, "rss_mb": 900})
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert line["outcome"] == ln.LABELED and line["state"] == "pieces"
    assert line["slots"] == {"0": 0.5, "3": 0.5}, "сегменты на оси хаба: ребёнок считает от start0"
    lines = _journal(tmp_path / "live.jsonl")
    assert [x["type"] for x in lines][:3] == ["header", "ready", "start"]
    assert lines[2]["start0"] == 5 * SR
    front = next(x for x in lines if x["type"] == "front")
    assert front["front"] == 5 * SR + 2 * SR and front["cpu_s"] == 1.5
    sh.stop()
    door.on_eof()


def test_the_other_channel_is_not_the_shadows_business(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("mic", 0, np.ones(100, dtype=np.float32))
    sh.note_chunk(_placed(0, 0, SR, label="mic"), "off")
    assert door.child.bytes == b"" and _chunks(tmp_path / "live.jsonl") == []
    sh.stop()
    door.on_eof()


def test_a_gap_in_the_axis_stops_the_shadow_instead_of_shifting_labels(tmp_path):
    sh, door, says = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    sh.note_chunk(_placed(1, 0, 1600), "off")
    sh.on_frame("blackhole", 1601, np.zeros(1600, dtype=np.float32))
    assert sh.state == ln.DEAD and "разрыв оси" in sh.reason
    sh.note_chunk(_placed(2, 1600, 1600), "off")
    outcomes = [c["outcome"] for c in _chunks(tmp_path / "live.jsonl")]
    assert outcomes == [ln.DEAD_STREAM, ln.DEAD_STREAM], "ждавший и пришедший после — оба со строкой"
    _wait(lambda: door.child.killed.is_set(), what="ребёнок убит")
    _wait(lambda: says, what="строка человеку")
    assert "разрыв оси" in says[-1]
    lines = _journal(tmp_path / "live.jsonl")
    (end,) = [x for x in lines if x["type"] == "end"]
    assert "разрыв оси" in end["reason"]
    assert lines[-1]["type"] == "chunk", "чанк после конца потока — строкой после end, журнал открыт"


def test_a_child_that_falls_behind_is_stopped_not_buffered(tmp_path):
    door = _Door()
    door.child.gate.clear()                        # ребёнок не принимает вход
    sh, door, _ = _live(tmp_path, door=door)
    block = np.zeros(SR, dtype=np.float32)
    for k in range(int(ln.QUEUE_CAP_S) + 3):
        sh.on_frame("blackhole", k * SR, block)
    assert sh.state == ln.DEAD and "отстал" in sh.reason
    _wait(lambda: door.child.killed.is_set(), what="ребёнок убит")


def test_a_chunk_that_waits_too_long_gets_a_timeout_line(tmp_path):
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock)
    sh.on_frame("blackhole", 0, np.zeros(SR * 4, dtype=np.float32))
    sh.note_chunk(_placed(0, 0, SR * 3), "shed")
    clock.now += ln.PENDING_CAP_S + 1
    door.on_message({"type": "front", "fed": SR, "frames": 5})
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert line["outcome"] == ln.TIMEOUT and line["wait_s"] > ln.PENDING_CAP_S
    sh.stop()
    door.on_eof()


def test_stop_resolves_what_the_final_front_covers_and_closes_the_rest(tmp_path):
    sh, door, says = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(SR * 6, dtype=np.float32))
    sh.note_chunk(_placed(0, 0, SR * 3), "pieces")
    sh.note_chunk(_placed(1, SR * 3, SR * 3), "none")
    sh.stop()
    assert sh.state == ln.STOPPING
    _wait(lambda: door.child.closed.is_set(), what="вход ребёнка закрыт")
    door.on_message({"type": "front", "fed": SR * 6, "frames": 50, "final": True})   # 4 с
    door.on_eof()
    assert sh.state == ln.DEAD and sh.reason == "остановлен"
    got = {c["chunk"]: c["outcome"] for c in _chunks(tmp_path / "live.jsonl")}
    assert got == {0: ln.LABELED, 1: ln.STOPPED}
    assert says == [], "штатная остановка человеку не пишет"


def test_stop_while_the_model_loads_cancels_the_handshake(tmp_path):
    door = _Door(block=True)
    sh, door, says = _shadow(tmp_path, door=door)
    _wait(lambda: door.on_eof is not None, what="дверь зовётся")
    sh.note_chunk(_placed(0, 0, SR), "off")
    sh.stop()
    _wait(lambda: sh.state == ln.DEAD, what="тень закончилась")
    assert sh.reason == "остановлен до старта потока" and says == []
    assert [c["outcome"] for c in _chunks(tmp_path / "live.jsonl")] == [ln.BEFORE_STREAM]


@pytest.mark.parametrize("ready, says_what", [
    ({**READY_OK, "proto": 2}, "протокол"),
    ({**READY_OK, "sr": 48000}, "частота"),
    ({**READY_OK, "frame_s": 0}, "кадр"),
    ([1, 2], "не объект"),
])
def test_a_handshake_out_of_protocol_is_refused(tmp_path, ready, says_what):
    """Отказ по рукопожатию — смерть, а не штатный конец: человеку говорится."""
    sh, door, says = _shadow(tmp_path, door=_Door(ready=ready))
    _wait(lambda: sh.state == ln.DEAD, what="тень отказала")
    assert says_what in sh.reason
    _wait(lambda: door.child.killed.is_set(), what="ребёнок убит")
    _wait(lambda: says, what="строка человеку")


def test_an_engine_that_did_not_start_is_said_once(tmp_path):
    door = _Door(refuse=fp.Outcome(fp.UNAVAILABLE, reason="нет весов"))
    sh, door, says = _shadow(tmp_path, door=door)
    _wait(lambda: says, what="строка человеку")
    assert sh.state == ln.DEAD and "нет весов" in says[0] and len(says) == 1


def test_a_wrong_chunk_state_still_gets_its_line_and_never_raises(tmp_path):
    """Состояние вне набора — строка с меткой unknown (инвариант не зависит от вызывающего),
    сбой сказан один раз и своей нитью (выходной круг 1 по №478 A2, M1–M2)."""
    sh, door, says = _live(tmp_path)
    sh.note_chunk(_placed(0, 0, SR), "plain")
    sh.note_chunk(_placed(1, 0, SR), "plain")
    _wait(lambda: says, what="строка человеку")
    time.sleep(0.05)
    assert len(says) == 1 and "plain" in says[0], "сбой сказан один раз"
    states = [c["state"] for c in _chunks(tmp_path / "live.jsonl")]
    assert states == [ln.UNKNOWN_STATE, ln.UNKNOWN_STATE], "чанк со странным состоянием всё равно со строкой"
    sh.stop()
    door.on_eof()


class _BrokenDoor(_Door):
    def __call__(self, *a, **k):
        raise RuntimeError("дверь сломалась")


def test_a_door_that_raises_kills_the_shadow_with_an_end_line(tmp_path):
    """Исключение вне перечня двери не оставляет тень в «стартует» навсегда: смерть со
    строкой end и строкой человеку (выходной круг 1 по №478 A2, I1)."""
    sh, door, says = _shadow(tmp_path, door=_BrokenDoor())
    _wait(lambda: sh.state == ln.DEAD, what="тень умерла")
    assert "не стартовал" in sh.reason and "дверь сломалась" in sh.reason
    assert [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "end"]
    _wait(lambda: says, what="строка человеку")


def test_a_writer_that_raises_kills_the_shadow(tmp_path):
    door = _Door()

    def boom(data):
        raise ValueError("не байты")
    door.child.write = boom
    sh, door, _ = _live(tmp_path, door=door)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _wait(lambda: sh.state == ln.DEAD, what="тень умерла")
    assert "писатель упал" in sh.reason
    _wait(lambda: door.child.killed.is_set(), what="ребёнок убит")


def test_a_protocol_line_that_breaks_the_shadow_kills_it(tmp_path, monkeypatch):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))

    def broken(m, now):
        raise KeyError("frames")
    monkeypatch.setattr(sh, "_take_front_locked", broken)
    door.on_message({"type": "front", "fed": SR, "frames": 5})
    assert sh.state == ln.DEAD and "строка протокола" in sh.reason


def test_a_child_log_over_the_cap_stops_the_shadow(tmp_path, monkeypatch):
    """Библиотека, печатающая на каждый блок, не заполнит диск (выходной круг 1, M5)."""
    monkeypatch.setattr(ln, "ERRLOG_CAP_BYTES", 100)
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock)
    (tmp_path / "live.err").write_bytes(b"x" * 200)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    clock.now += ln.PRESSURE_CHECK_S
    door.on_message({"type": "front", "fed": SR, "frames": 5})
    assert sh.state == ln.DEAD and "журнал ребёнка" in sh.reason


def test_every_accepted_chunk_gets_exactly_one_line(tmp_path):
    """Инвариант тени: сколько чанков канала принято — столько строк, по одной на номер,
    при любом порядке фронтов, остановке посреди ожидания и чанках до старта."""
    rng = np.random.default_rng(11)
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock)
    step = SR // 2
    noted, pos, number = set(), 0, 0
    sh.note_chunk(_placed(number, 0, SR), "off")                      # до старта потока
    noted.add(number)
    for _ in range(300):
        sh.on_frame("blackhole", pos, np.zeros(step, dtype=np.float32))
        pos += step
        _wait(lambda: sh._queued <= 4 * step, what="писатель успевает")
        if rng.random() < 0.3:
            number += 1
            start = max(0, pos - int(rng.integers(step, 6 * step)))
            sh.note_chunk(_placed(number, start, 3 * step), str(rng.choice(ln.CHUNK_STATES)))
            noted.add(number)
        if rng.random() < 0.2:
            frames = int(max(0, pos - rng.integers(0, 4 * step)) / (0.08 * SR))
            door.on_message({"type": "front", "fed": pos, "frames": frames})
        if rng.random() < 0.05:
            clock.now += 50
    sh.stop()
    door.on_eof()
    lines = _chunks(tmp_path / "live.jsonl")
    assert sorted(c["chunk"] for c in lines) == sorted(noted), "строка на каждый чанк, без дублей"
    assert {c["outcome"] for c in lines} <= {ln.LABELED, ln.BEFORE_STREAM, ln.TIMEOUT, ln.STOPPED}


def test_the_journal_is_private_and_holds_numbers_only(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.full(SR, 0.5, dtype=np.float32))
    sh.stop()
    door.on_eof()
    path = tmp_path / "live.jsonl"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    text = path.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "python" not in text, "в журнале нет путей машины"


def _pressure(levels):
    it = iter(levels)
    return lambda: {"pressure": next(it), "swap_used_mb": 1000}


def _fronts(sh, door, clock, n):
    for k in range(n):
        clock.now += ln.PRESSURE_CHECK_S
        door.on_message({"type": "front", "fed": SR, "frames": 1 + k})


def test_memory_pressure_twice_in_a_row_stops_the_shadow(tmp_path):
    """Опыт 29.09: с потоком своп рос на 2,7–6,9 ГБ за фазу и давление доходило до 2 —
    тень обязана уступить встрече, а не копить своп."""
    clock = _Clock()
    sh, door, says = _live(tmp_path, clock=clock, memory=_pressure([1, 2, 2]))
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _fronts(sh, door, clock, 3)
    assert sh.state == ln.DEAD and "давление памяти" in sh.reason
    mem = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "mem"]
    assert [m["pressure"] for m in mem] == [1, 2, 2] and mem[0]["swap_used_mb"] == 1000
    _wait(lambda: door.child.killed.is_set(), what="ребёнок убит")
    _wait(lambda: says, what="строка человеку")


def test_a_single_spike_of_pressure_does_not_stop_it(tmp_path):
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock, memory=_pressure([2, 1, 2, 1]))
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _fronts(sh, door, clock, 4)
    assert sh.state == ln.LIVE
    sh.stop()
    door.on_eof()


def test_memory_is_asked_at_most_once_per_interval(tmp_path):
    calls = []
    clock = _Clock()

    def probe():
        calls.append(clock.now)
        return {"pressure": 1, "swap_used_mb": 0}
    sh, door, _ = _live(tmp_path, clock=clock, memory=probe)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    for k in range(10):                       # фронт каждые 0,5 с — память раз в PRESSURE_CHECK_S
        clock.now += 0.5
        door.on_message({"type": "front", "fed": SR, "frames": 1 + k})
    assert len(calls) == 1
    sh.stop()
    door.on_eof()


def test_memory_state_off_macos_is_none(monkeypatch):
    monkeypatch.setattr(ln, "_sysctl", lambda name, value: False)
    assert ln.memory_state() is None


SHADOW = {"sufler": {"live_nemotron": "shadow"}}
BOTH = {"blackhole", "mic"}


@pytest.mark.parametrize("cfg, sr, labels, pressure, says_what", [
    ({"sufler": {}}, SR, BOTH, 1, None),
    ({"sufler": {"live_nemotron": "off"}}, SR, BOTH, 1, None),
    ({"sufler": {"live_nemotron": False}}, SR, BOTH, 1, None),           # голое off в YAML
    ({"sufler": {"live_nemotron": "on"}}, SR, BOTH, 1, "неизвестен"),
    ({"sufler": {"live_nemotron": True}}, SR, BOTH, 1, "неизвестен"),    # голое on в YAML
    (SHADOW, 48000, BOTH, 1, "Гц"),
    (SHADOW, SR, {"mic"}, 1, "канала собеседников"),                      # захват только микрофона
    (SHADOW, SR, BOTH, 2, "давление памяти"),                             # машина уже в свопе
    (SHADOW, SR, BOTH, 1, "не установлено"),
])
def test_start_refuses_with_a_reason_and_leaves_nothing_behind(tmp_path, cfg, sr, labels, pressure,
                                                               says_what):
    says = []
    probe = lambda: {"pressure": pressure, "swap_used_mb": 0}   # noqa: E731
    assert ln.start(cfg, root=tmp_path, stamp="s", sr=sr, labels=labels, say=says.append,
                    memory=probe) is ln.NO_SHADOW
    if says_what is None:
        assert says == []
    else:
        assert len(says) == 1 and says_what in says[0]
    assert not list(tmp_path.rglob("nemotron_live_*")), "отказ не оставляет журнала"


# ------------------------------------------------ гейт «хаб → ребёнок → журнал»

FAKE_ENGINE = '''
import json, os, sys
sys.path.insert(0, {src!r})
import numpy as np
import diarize_nemotron as dn

FRAME = int(0.08 * dn.SAMPLE_RATE)
LAG = int(1.04 * dn.SAMPLE_RATE)


class Voices:
    """Поддельная модель: голос кадра — по громкости (тихий — слот 0, громкий — 1)."""

    def __init__(self):
        self.audio = np.zeros(0, dtype=np.float32)
        self.frames_processed = 0

    def _emit(self, upto):
        out = []
        for f in range(self.frames_processed, upto):
            level = float(np.abs(self.audio[f * FRAME:(f + 1) * FRAME]).mean())
            out.append({{"start": round(f * FRAME / dn.SAMPLE_RATE, 3),
                         "end": round((f + 1) * FRAME / dn.SAMPLE_RATE, 3),
                         "speaker": "nem%d" % (1 if level > 0.45 else 0)}})
        self.frames_processed = max(self.frames_processed, upto)
        return out

    def feed(self, pcm):
        self.audio = np.concatenate([self.audio, pcm])
        return self._emit(max(0, (len(self.audio) - LAG) // FRAME))

    def close(self):
        return self._emit(len(self.audio) // FRAME)


proto = dn._protocol_channel()
print("шум библиотеки в stdout не рвёт протокол")


def emit(m):
    proto.write(json.dumps(m) + "\\n")


emit({{"type": "ready", "proto": dn.STREAM_PROTO, "sr": dn.SAMPLE_RATE, "preset": "low",
       "frame_s": FRAME / dn.SAMPLE_RATE, "step": dn.STREAM_STEP}})
sys.exit(dn.run_stream(Voices(), frame_s=FRAME / dn.SAMPLE_RATE, read=lambda n: os.read(0, n), emit=emit))
'''


def _hub():
    cfg = {"audio": {"samplerate": SR, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                     "vad_energy_db": -200.0, "record": False, "device": "auto"},
           "log": {"recordings_dir": "recordings"}, "sufler": {"user_name": "Владелец"}}
    hub = audio.AudioHub(cfg, captures=[])
    hub._register_captures([types.SimpleNamespace(label="blackhole")])
    return hub


def test_hub_to_child_to_journal_keeps_one_axis(tmp_path, monkeypatch):
    """Первые 5 с звучат до старта потока (ось ребёнка начнётся не с нуля), голос
    меняется на 17-й секунде хаба. Каждый чанк после старта обязан получить слот
    своего голоса; сдвиг на `start0` перекрасил бы чанки 12,5–17 с, сдвиг на байт —
    все."""
    engine = tmp_path / "engine.py"
    engine.write_text(FAKE_ENGINE.format(src=str(SRC)), encoding="utf-8")
    tried = []

    class Spy(importlib.abc.MetaPathFinder):
        """mlx не попадает в процесс демона: тень только пишет в трубу и читает строки."""
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in ("mlx", "mlx_audio"):
                tried.append(name)
            return None

    for name in [m for m in sys.modules if m.split(".")[0] in ("mlx", "mlx_audio")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [Spy(), *sys.meta_path])
    hub = _hub()
    cap = types.SimpleNamespace(label="blackhole")
    change = 17 * SR
    total = 30 * SR
    voice = np.where(np.arange(total) < change, 0.3, 0.6).astype(np.float32)
    placed = []
    block = 1600
    pos = 0
    while pos < 5 * SR:                                   # звук до тени
        hub._consume(cap, voice[pos:pos + block])
        placed += hub.pull_placed()
        pos += block
    says = []
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="s", say=says.append,
                   memory=lambda: None)
    hub.add_frame_listener(sh.on_frame)
    for p in placed:
        sh.note_chunk(p, "off")
    sh.begin(python=sys.executable, script=engine, args=[], errlog=tmp_path / "live.err")
    _wait(lambda: sh.state == ln.LIVE, timeout=30, what="рукопожатие поддельного движка")
    while pos < total:
        hub._consume(cap, voice[pos:pos + block])
        for p in hub.pull_placed():
            placed.append(p)
            sh.note_chunk(p, "off")
        pos += block
        _wait(lambda: sh._queued < 2 * SR, what="ребёнок успевает")
    sh.stop()
    _wait(lambda: sh.state == ln.DEAD, timeout=30, what="поток закрыт")
    assert sh.reason == "остановлен" and says == []
    lines = _journal(tmp_path / "live.jsonl")
    start0 = next(x["start0"] for x in lines if x["type"] == "start")
    assert start0 >= 5 * SR
    chunks = {c["chunk"]: c for c in lines if c["type"] == "chunk"}
    assert sorted(chunks) == sorted(p.seq[1] for p in placed), "строка на каждый чанк"
    labelled = 0
    for p in placed:
        c = chunks[p.seq[1]]
        end = p.start + len(p.chunk)
        if p.start < start0:
            assert c["outcome"] == ln.BEFORE_STREAM
            continue
        assert c["outcome"] == ln.LABELED, c
        if end <= change:
            want = "0"
        elif p.start >= change:
            want = "1"
        else:
            continue                                       # чанк на смене голоса
        assert max(c["slots"], key=c["slots"].get) == want, (p.start / SR, c["slots"])
        assert abs(sum(c["slots"].values()) - len(p.chunk) / SR) < 0.1, "слоты покрывают чанк целиком"
        labelled += 1
    assert labelled >= 6
    assert not (tmp_path / "live.err").read_text(encoding="utf-8").count("Traceback")
    assert tried == [], f"процесс демона пытался импортировать {tried}"


# ------------------------------------------------ границы тени (мутатор диапазона)

class _Libc:
    """Поддельная libc: `sysctlbyname` пишет заданное в структуру вызывающего; нет ответа — -1."""

    def __init__(self, answers):
        self.answers = answers

    def sysctlbyname(self, name, ref, size, newp, newlen):
        value = self.answers.get(name)
        if value is None:
            return -1
        target = ref._obj
        if isinstance(target, ctypes.c_int):
            target.value = value
        else:
            target.used = value
        return 0


def test_memory_state_reads_pressure_and_swap_through_sysctl(monkeypatch):
    libc = _Libc({b"kern.memorystatus_vm_pressure_level": 2, b"vm.swapusage": 3 * 2**20 + 5})
    monkeypatch.setattr(ln.ctypes.util, "find_library", lambda name: "c")
    monkeypatch.setattr(ln.ctypes, "CDLL", lambda path: libc)
    assert ln.memory_state() == {"pressure": 2, "swap_used_mb": 3}
    libc.answers[b"vm.swapusage"] = None
    assert ln.memory_state() == {"pressure": 2, "swap_used_mb": None}, "своп не прочитан — давление всё равно"
    libc.answers[b"kern.memorystatus_vm_pressure_level"] = None
    assert ln.memory_state() is None


def test_sysctl_is_false_without_libc_or_with_a_broken_one(monkeypatch):
    monkeypatch.setattr(ln.ctypes, "CDLL", lambda path: _Libc({b"k": 1}))
    monkeypatch.setattr(ln.ctypes.util, "find_library", lambda name: None)
    assert ln._sysctl(b"k", ctypes.c_int(0)) is False
    monkeypatch.setattr(ln.ctypes.util, "find_library", lambda name: "c")
    assert ln._sysctl(b"k", ctypes.c_int(0)) is True

    def broken(path):
        raise OSError("нет библиотеки")
    monkeypatch.setattr(ln.ctypes, "CDLL", broken)
    assert ln._sysctl(b"k", ctypes.c_int(0)) is False


def test_the_handshake_check_is_empty_when_the_handshake_is_right(tmp_path):
    sh, door, _ = _live(tmp_path)
    assert sh._ready_problem(READY_OK) == ""
    sh.stop()
    door.on_eof()


def test_a_child_that_comes_up_after_the_stop_is_ended_quietly(tmp_path):
    sh, door, says = _shadow(tmp_path, door=_Door(ok_after_cancel=True))
    _wait(lambda: door.on_eof is not None, what="дверь зовётся")
    sh.stop()
    _wait(lambda: sh.state == ln.DEAD, what="тень закончилась")
    assert sh.reason == "остановлен до старта потока"
    _wait(lambda: door.child.killed.is_set(), what="неподобранный ребёнок убит")
    time.sleep(0.05)
    assert says == [], "штатная остановка человеку не пишет"


def test_the_queue_cap_is_exact_and_counts_what_the_writer_took(tmp_path):
    """Ровно QUEUE_CAP_S звука в очереди — ещё живёт, сэмпл сверх — «отстал»; блок, взятый
    писателем, из очереди вычтен в сэмплах, а не в байтах."""
    door = _Door()
    door.child.gate.clear()                        # писатель встанет на первом блоке
    sh, door, _ = _live(tmp_path, door=door)
    block = np.zeros(SR, dtype=np.float32)
    sh.on_frame("blackhole", 0, block)
    _wait(door.child.writing.is_set, what="писатель взял первый блок")
    cap = int(ln.QUEUE_CAP_S)
    for k in range(1, cap + 1):
        sh.on_frame("blackhole", k * SR, block)
    assert sh.state == ln.LIVE, "ровно потолок в очереди — ещё не отстал"
    sh.on_frame("blackhole", (cap + 1) * SR, np.zeros(1, dtype=np.float32))
    assert sh.state == ln.DEAD and "отстал" in sh.reason


def test_a_closed_input_of_the_child_stops_the_shadow(tmp_path):
    door = _Door()

    def closed(data):
        raise BrokenPipeError("труба закрыта")
    door.child.write = closed
    sh, door, _ = _live(tmp_path, door=door)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _wait(lambda: sh.state == ln.DEAD, what="тень умерла")
    assert sh.reason.startswith("вход ребёнка закрыт")


def _end(tmp_path):
    (end,) = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "end"]
    return end


def test_messages_before_the_stream_or_of_unknown_kind_are_counted_not_taken(tmp_path):
    sh, door, _ = _live(tmp_path)
    door.on_message({"type": "seg", "start": 0.0, "end": 1.0, "slot": 0})    # блоков ещё не было
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    door.on_message({"type": "hello"})
    assert sh.state == ln.LIVE
    sh.stop()
    door.on_eof()
    assert [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "seg"] == []
    counts = _end(tmp_path)["counts"]
    assert counts["message_out_of_state"] == 1 and counts["message_unknown"] == 1


@pytest.mark.parametrize("bad", [
    {"type": "seg", "end": 1.0, "slot": 0},                         # без начала
    {"type": "seg", "start": 0.0, "slot": 0},                       # без конца
    {"type": "seg", "start": 0.5, "end": 0.5, "slot": 0},           # нулевой длины
    {"type": "seg", "start": 0.0, "end": 1.0, "slot": True},        # слот — не число
    {"type": "seg", "start": 0.0, "end": 1.0, "slot": 1.0},         # слот — не целое
])
def test_a_malformed_segment_is_counted_and_not_placed(tmp_path, bad):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    door.on_message(bad)
    assert sh.state == ln.LIVE
    sh.stop()
    door.on_eof()
    assert [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "seg"] == []
    assert _end(tmp_path)["counts"]["seg_malformed"] == 1


@pytest.mark.parametrize("bad", [{"type": "front", "fed": SR}, {"type": "front", "frames": 5}])
def test_a_malformed_front_is_counted_and_moves_nothing(tmp_path, bad):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    door.on_message(bad)
    assert sh.state == ln.LIVE
    sh.stop()
    door.on_eof()
    assert [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "front"] == []
    assert _end(tmp_path)["counts"]["front_malformed"] == 1


def test_the_final_front_is_marked_final_in_the_journal(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    door.on_message({"type": "front", "fed": SR, "frames": 5})
    door.on_message({"type": "front", "fed": SR, "frames": 12, "final": True})
    fronts = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "front"]
    assert [f.get("final") for f in fronts] == [None, True]
    sh.stop()
    door.on_eof()


def test_a_chunk_ending_exactly_at_the_front_is_labelled(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(3 * SR, dtype=np.float32))
    door.on_message({"type": "seg", "start": 0.0, "end": 2.0, "slot": 4})
    sh.note_chunk(_placed(0, 0, 2 * SR), "off")                        # конец чанка — 2 с
    door.on_message({"type": "front", "fed": 2 * SR, "frames": 25})    # фронт ровно 2 с
    assert [(c["chunk"], c["outcome"], c["slots"]) for c in _chunks(tmp_path / "live.jsonl")] == \
        [(0, ln.LABELED, {"4": 2.0})]
    sh.note_chunk(_placed(1, SR, SR), "off")                           # конец ровно на пройденном фронте
    assert [c["outcome"] for c in _chunks(tmp_path / "live.jsonl")] == [ln.LABELED, ln.LABELED], \
        "метка уже готова — строка сразу, без ожидания"
    sh.stop()
    door.on_eof()


def test_a_segment_touching_the_chunk_gives_it_no_slot(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(3 * SR, dtype=np.float32))
    door.on_message({"type": "seg", "start": 0.0, "end": 1.0, "slot": 0})
    door.on_message({"type": "seg", "start": 1.0, "end": 2.0, "slot": 3})
    sh.note_chunk(_placed(0, SR, SR), "off")
    door.on_message({"type": "front", "fed": 2 * SR, "frames": 25})
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert line["slots"] == {"3": 1.0}
    sh.stop()
    door.on_eof()


def test_segments_behind_the_keep_window_are_dropped_and_late_chunks_say_so(tmp_path, monkeypatch):
    """Окно сегментов — SEG_KEEP_S за фронтом: чанк от пола окна размечается тем, что осталось;
    начавшийся раньше пола — «поздно», а не метка по неполным сегментам."""
    monkeypatch.setattr(ln, "SEG_KEEP_S", 2.0)
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(6 * SR, dtype=np.float32))
    for start, end, slot in ((0.0, 1.0, 0), (1.0, 3.0, 1), (3.0, 5.0, 2)):
        door.on_message({"type": "seg", "start": start, "end": end, "slot": slot})
    door.on_message({"type": "front", "fed": 6 * SR, "frames": 75})    # фронт 6 с → пол окна 4 с
    sh.note_chunk(_placed(0, 4 * SR, SR), "off")                        # от пола — третий сегмент
    sh.note_chunk(_placed(1, 3 * SR, SR), "off")                        # раньше пола
    got = {c["chunk"]: c for c in _chunks(tmp_path / "live.jsonl")}
    assert got[0]["outcome"] == ln.LABELED and got[0]["slots"] == {"2": 1.0}
    assert got[1]["outcome"] == ln.LATE
    assert sh.state == ln.LIVE
    sh.stop()
    door.on_eof()


def test_the_numbers_of_a_chunk_line_are_rounded_seconds_of_its_clock(tmp_path):
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock)
    sh.on_frame("blackhole", 0, np.zeros(4 * SR, dtype=np.float32))
    door.on_message({"type": "front", "fed": 2 * SR, "frames": 25})           # фронт 2 с
    sh.note_chunk(_placed(0, SR, SR + SR // 2), "pieces")                    # конец 2,5 с — за фронтом 0,5 с
    clock.now += 1.25
    door.on_message({"type": "front", "fed": 3 * SR, "frames": 50})           # фронт 4 с
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["behind_s"], line["wait_s"], line["t"]) == (0.5, 1.25, 1.25)
    sh.stop()
    door.on_eof()


def test_a_chunk_waits_exactly_the_cap_and_times_out_after_it(tmp_path):
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock)
    sh.on_frame("blackhole", 0, np.zeros(4 * SR, dtype=np.float32))
    sh.note_chunk(_placed(0, 0, 3 * SR), "shed")
    clock.now += ln.PENDING_CAP_S
    door.on_message({"type": "front", "fed": SR, "frames": 5})
    assert _chunks(tmp_path / "live.jsonl") == [], "ровно потолок ожидания — ещё ждёт"
    clock.now += 0.001
    door.on_message({"type": "front", "fed": SR, "frames": 6})
    assert [c["outcome"] for c in _chunks(tmp_path / "live.jsonl")] == [ln.TIMEOUT]
    sh.stop()
    door.on_eof()


def test_more_chunks_waiting_than_the_cap_times_out_the_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(ln, "PENDING_MAX", 2)
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(4 * SR, dtype=np.float32))
    for k in range(2):
        sh.note_chunk(_placed(k, k * SR, SR), "off")
    assert _chunks(tmp_path / "live.jsonl") == [], "ровно потолок ожидающих — все ждут"
    sh.note_chunk(_placed(2, 2 * SR, SR), "off")
    assert [(c["chunk"], c["outcome"]) for c in _chunks(tmp_path / "live.jsonl")] == [(0, ln.TIMEOUT)]
    sh.stop()
    door.on_eof()


def test_the_end_line_counts_chunks_by_outcome(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.note_chunk(_placed(0, 0, SR), "off")                                  # до потока
    sh.on_frame("blackhole", 0, np.zeros(2 * SR, dtype=np.float32))
    sh.note_chunk(_placed(1, 0, SR), "off")
    door.on_message({"type": "front", "fed": SR, "frames": 25})
    sh.stop()
    door.on_eof()
    counts = _end(tmp_path)["counts"]
    assert counts["chunk_before_stream"] == 1 and counts["chunk_labeled"] == 1


def test_a_child_that_stays_after_the_stop_is_killed_after_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(ln, "STOP_GRACE_S", 0.05)
    sh, door, _ = _live(tmp_path)
    sh.stop()
    _wait(door.child.killed.is_set, what="ребёнок убит после отсрочки")
    door.on_eof()
    assert _end(tmp_path)["counts"]["killed_after_grace"] == 1


def test_a_child_that_left_by_itself_is_not_killed_after_the_grace(tmp_path, monkeypatch):
    monkeypatch.setattr(ln, "STOP_GRACE_S", 0.05)
    sh, door, _ = _live(tmp_path)
    door.child.exited = True
    sh.stop()
    time.sleep(0.3)
    assert not door.child.killed.is_set()
    door.on_eof()
    assert "killed_after_grace" not in _end(tmp_path)["counts"]


@pytest.mark.parametrize("exit_, why", [
    (fp.Outcome(fp.FAILED, reason="код 3"), "код 3"),
    (fp.Outcome(fp.OK), "вышел"),
])
def test_a_child_closing_its_output_while_live_kills_the_shadow_with_its_exit(tmp_path, exit_, why):
    sh, door, says = _live(tmp_path)
    door.child.exit = exit_
    door.child.nonjson = 2
    door.on_eof()
    assert sh.state == ln.DEAD and sh.reason == f"ребёнок закрыл вывод ({why})"
    end = _end(tmp_path)
    assert end["exit"] == exit_.kind and end["counts"]["nonjson"] == 2
    _wait(lambda: says, what="строка человеку")


def test_the_journal_keeps_its_text_readable(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.stop()
    door.on_eof()
    assert '"reason": "остановлен"' in (tmp_path / "live.jsonl").read_text(encoding="utf-8")


def test_a_journal_that_cannot_be_written_stops_the_shadow(tmp_path):
    sh, door, _ = _live(tmp_path)

    class Full:
        def write(self, text):
            raise OSError("диск полон")
    real, sh._journal = sh._journal, Full()
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))          # строка start
    real.close()
    assert sh.state == ln.DEAD and sh.reason.startswith("журнал тени не пишется")


def test_a_fault_is_said_once_however_often_it_repeats(tmp_path):
    sh, door, says = _live(tmp_path)
    sh.note_chunk(_placed(0, 0, SR), "plain")
    _wait(lambda: says, what="строка после первого сбоя")
    sh.note_chunk(_placed(1, 0, SR), "plain")
    sh.note_chunk(_placed(2, 0, SR), "plain")
    time.sleep(0.1)
    assert len(says) == 1
    sh.stop()
    door.on_eof()


def test_bare_on_in_yaml_is_named_on(tmp_path):
    says = []
    assert ln.start({"sufler": {"live_nemotron": True}}, root=tmp_path, stamp="s", sr=SR, labels=BOTH,
                    say=says.append, memory=lambda: None) is ln.NO_SHADOW
    assert "'on'" in says[0]


def test_start_raises_the_shadow_with_its_journal_and_the_stream_args(tmp_path, monkeypatch):
    monkeypatch.setattr(ln.diarize_nemotron, "engine_interpreter", lambda setting, root: ("/py", None))
    begun = []
    monkeypatch.setattr(ln.Shadow, "begin", lambda self, **kw: begun.append(kw))
    (tmp_path / "logs").mkdir()
    says = []
    stamp = "2026-09-29_120000"
    sh = ln.start(SHADOW, root=tmp_path, stamp=stamp, sr=SR, labels=BOTH, say=says.append,
                  memory=lambda: None)
    assert isinstance(sh, ln.Shadow)
    assert (tmp_path / "logs" / f"nemotron_live_{stamp}.jsonl").exists()
    (kw,) = begun
    assert kw["python"] == "/py" and kw["script"] == ln.diarize_nemotron.SCRIPT
    assert kw["errlog"] == tmp_path / "logs" / f"nemotron_live_{stamp}.err"
    assert kw["args"][:2] == ["--stream", "--model"] and kw["args"][-2:] == ["--preset", ln.PRESET]
    assert says == [f"поток Nemotron: тень включена, журнал nemotron_live_{stamp}.jsonl"]


def test_no_shadow_answers_every_call_and_touches_nothing():
    hub = types.SimpleNamespace(add_frame_listener=lambda fn: pytest.fail("нет тени — нет слушателя"))
    ln.NO_SHADOW.attach(hub)
    ln.NO_SHADOW.on_frame("blackhole", 0, np.zeros(10, dtype=np.float32))
    ln.NO_SHADOW.note_chunk(_placed(0, 0, 10), "off")
    ln.NO_SHADOW.stop()


def test_a_shadow_listens_to_the_frames_of_the_hub(tmp_path):
    got = []
    sh, door, _ = _live(tmp_path)
    sh.attach(types.SimpleNamespace(add_frame_listener=got.append))
    assert got == [sh.on_frame]
    sh.stop()
    door.on_eof()


def test_the_diarized_state_names_what_the_tracker_did():
    assert ln.diarized_state(True, [object()]) == "split_failed"
    assert ln.diarized_state(False, None) == "none"
    assert ln.diarized_state(False, []) == "pieces"
    assert {ln.diarized_state(f, j) for f in (False, True) for j in (None, [])} <= set(ln.CHUNK_STATES)


def test_pcm16_keeps_the_sign_and_clips_to_full_scale():
    raw = audio.pcm16(np.array([-2.0, -0.5, 0.0, 0.5, 2.0], dtype=np.float32))
    assert np.frombuffer(raw, dtype="<i2").tolist() == [-32767, -16383, 0, 16383, 32767]
