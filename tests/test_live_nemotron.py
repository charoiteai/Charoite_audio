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
        self.gate = threading.Event()
        self.gate.set()
        self.nonjson = 0
        self.callback_errors = 0

    def write(self, data):
        self.gate.wait()
        if self.killed.is_set():
            raise BrokenPipeError("убит")
        self.bytes += data

    def close_input(self):
        self.closed.set()

    def alive(self):
        return not self.killed.is_set()

    def kill(self):
        self.killed.set()
        self.gate.set()

    def finish(self, timeout):
        return fp.Outcome(fp.OK)


class _Door:
    """Поддельная дверь: отдаёт ребёнка и запоминает обратные вызовы."""

    def __init__(self, ready=READY_OK, refuse=None, block=False):
        self.child = _FakeChild()
        self.ready, self.refuse, self.block = ready, refuse, block
        self.on_message = self.on_eof = None

    def __call__(self, python, script, args, *, stderr_path, handshake_timeout, role,
                 on_message, on_eof, cancel):
        self.on_message, self.on_eof = on_message, on_eof
        if self.block:
            cancel.wait(30)
            return None, fp.Outcome(fp.FAILED, reason="ожидание рукопожатия отменено — убит")
        if self.refuse:
            return None, self.refuse
        return self.child, fp.Outcome(fp.OK, payload=dict(self.ready))


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
])
def test_a_handshake_out_of_protocol_is_refused(tmp_path, ready, says_what):
    sh, door, says = _shadow(tmp_path, door=_Door(ready=ready))
    _wait(lambda: sh.state == ln.DEAD, what="тень отказала")
    assert says_what in sh.reason
    _wait(lambda: door.child.killed.is_set(), what="ребёнок убит")


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
                    memory=probe) is None
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
