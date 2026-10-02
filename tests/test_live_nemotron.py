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

    def __init__(self, frame_s=0.01, lag=int(1.04 * SR)):
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
    assert dn.run_stream(stream, frame_s=0.01, read=lambda n: next(it), emit=out.append, step=4000) == 0
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
    frame_s = 0.01                                                   # единица продукта: кадр спектра
    segs = [{"start": 0.0, "end": 0.03, "speaker": "nem0"},          # 3 кадра — до фронта
            {"start": 0.03, "end": 0.05, "speaker": "nem2"}]         # 5 кадров — фронт
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
    monkeypatch.setattr(dn, "serve_stream", lambda model, preset, cache_limit_mb=None:
                        calls.append((model, preset, cache_limit_mb)) or 0)
    assert dn.main(["--stream", "--model", str(tmp_path)]) == 0
    assert dn.main(["--stream", "--model", str(tmp_path), "--preset", "very_low"]) == 0
    assert dn.main(["--stream", "--model", str(tmp_path), "--cache-limit-mb", "512"]) == 0
    assert calls == [(tmp_path, "low", None), (tmp_path, "very_low", None), (tmp_path, "low", 512)]
    with pytest.raises(SystemExit):
        dn.main(["--stream", "--model", str(tmp_path), "--cache-limit-mb", "-1"])


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
        self.nowait_kills = 0
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

    def kill_nowait(self):
        self.nowait_kills += 1
        self.kill()

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
    """Тень живёт и её нить запуска закончилась: та дописывает журнал в своём finally уже
    после LIVE, и без ожидания её `_drain` гоняется с проверками теста (№533)."""
    sh, door, says = _shadow(tmp_path, **kw)
    _wait(lambda: sh.state == ln.LIVE, what="тень живёт")
    for t in threading.enumerate():
        if t.name == "nemotron-live-start":
            t.join(5)
    return sh, door, says


def test_the_stream_is_dead_only_after_death_not_before_the_handshake_or_while_stopping(tmp_path):
    """№580: запас трекера до рукопожатия подписывает голос без связи меткой канала, после
    смерти — номером трекера; различает их состояние потока, а не эвристика."""
    sh, door, _says = _live(tmp_path)
    assert (sh.live, sh.dead) == (True, False)
    sh.stop()
    assert (sh.state, sh.dead) == (ln.STOPPING, False)
    _wait(lambda: door.child.closed.is_set(), what="вход ребёнка закрыт")
    door.on_message({"type": "front", "fed": 0, "frames": 0, "final": True})
    door.on_eof()
    assert (sh.state, sh.live, sh.dead) == (ln.DEAD, False, True)
    assert (ln.NO_SHADOW.live, ln.NO_SHADOW.dead) == (False, False)


def test_a_starting_stream_is_not_dead(tmp_path):
    says = []
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="2026-09-29_120000", say=says.append,
                   clock=time.monotonic, memory=lambda: None)
    assert (sh.state, sh.dead) == (ln.STARTING, False)
    sh.close(1.0)


def _ending(sh, tmp_path) -> str:
    """Исход конца тени из строки `end` (№563) — когда строка уже на диске."""
    _wait(lambda: _over(sh, tmp_path), what="строка end")
    return _end(tmp_path)["ending"]


def _over(sh, tmp_path):
    """Тень кончилась и её строка `end` уже на диске: журнал дописывает `_drain` вне замка,
    чуть позже перехода в DEAD (№533)."""
    path = tmp_path / "live.jsonl"
    return sh.state == ln.DEAD and path.exists() and '"type": "end"' in path.read_text(encoding="utf-8")


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


def test_the_price_of_the_child_and_its_cache_limit_reach_the_journal(tmp_path):
    """Замер памяти тени (№478 B): след процесса и счётчики MLX из фронта, лимит кэша из
    рукопожатия — в журнал; не числа — мимо (тот же фильтр `_num`)."""
    ready = {**READY_OK, "cache_limit_mb": 512, "cache_limit_prev_mb": 62259}
    sh, door, _ = _live(tmp_path, door=_Door(ready=ready))
    sh.on_frame("blackhole", 0, np.full(SR, 0.25, dtype=np.float32))
    price = {"cpu_s": 1.5, "rss_mb": 900, "phys_mb": 2100, "mlx_active_mb": 700, "mlx_cache_mb": 300,
             "mlx_peak_mb": 1200}
    door.on_message({"type": "front", "fed": SR, "frames": 5, **price, "mlx_cache_mb": "много"})
    _wait(lambda: any(x["type"] == "front" for x in _journal(tmp_path / "live.jsonl")), what="фронт в журнале")
    lines = _journal(tmp_path / "live.jsonl")
    got = next(x for x in lines if x["type"] == "ready")
    assert (got["cache_limit_mb"], got["cache_limit_prev_mb"]) == (512, 62259)
    front = next(x for x in lines if x["type"] == "front")
    assert {k: front.get(k) for k in price} == {**price, "mlx_cache_mb": None}
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
    assert end["ending"] == ln.END_FAULT, "разрыв оси — сбой нашей стороны"
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
    assert _ending(sh, tmp_path) == ln.END_STOPPED
    got = {c["chunk"]: c["outcome"] for c in _chunks(tmp_path / "live.jsonl")}
    assert got == {0: ln.LABELED, 1: ln.STOPPED}
    assert says == [], "штатная остановка человеку не пишет"


def test_an_ending_outside_the_set_is_refused_before_any_transition(tmp_path):
    """№563: слово конца, которого не знает читатель журнала, — ошибка вызывающего, а не строка."""
    sh, door, _ = _live(tmp_path)
    with pytest.raises(ValueError, match="вне"):
        sh._die_from_thread("проверка", "killed")
    assert sh.state == ln.LIVE and not sh.dead
    assert not (tmp_path / "live.jsonl").exists() or not any(
        x["type"] == "end" for x in _journal(tmp_path / "live.jsonl"))
    sh.close(1.0)


def test_a_thread_fault_while_stopping_ends_as_fault_not_as_a_stop(tmp_path):
    """Сбой нити в остановке: исход — сбой, чанки — «остановлен» (переход STOPPING → DEAD)."""
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(SR * 3, dtype=np.float32))
    sh.note_chunk(_placed(0, 0, SR * 3), "pieces")
    sh.stop()
    assert sh.state == ln.STOPPING
    sh._die_from_thread("писатель упал", ln.END_FAULT)
    assert _ending(sh, tmp_path) == ln.END_FAULT
    assert {c["outcome"] for c in _chunks(tmp_path / "live.jsonl")} == {ln.STOPPED}


def test_stop_while_the_model_loads_cancels_the_handshake(tmp_path):
    door = _Door(block=True)
    sh, door, says = _shadow(tmp_path, door=door)
    _wait(lambda: door.on_eof is not None, what="дверь зовётся")
    sh.note_chunk(_placed(0, 0, SR), "off")
    sh.stop()
    _wait(lambda: _over(sh, tmp_path), what="тень закончилась")
    assert sh.reason == "остановлен до старта потока" and says == []
    assert _ending(sh, tmp_path) == ln.END_STOPPED
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
    _wait(lambda: _over(sh, tmp_path), what="тень умерла")
    assert "не стартовал" in sh.reason and "дверь сломалась" in sh.reason
    assert _ending(sh, tmp_path) == ln.END_NOT_STARTED
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


@pytest.mark.parametrize("size, dies", [(2**20, False), (2**20 + 1, True)])
def test_a_child_log_over_the_cap_stops_the_shadow(tmp_path, monkeypatch, size, dies):
    """Библиотека, печатающая на каждый блок, не заполнит диск (выходной круг 1, M5). Потолок
    свой размер ещё пропускает, байт сверх — нет; причина называет потолок в мегабайтах
    (мутатор диапазона, 29.09)."""
    monkeypatch.setattr(ln, "ERRLOG_CAP_BYTES", 2**20)
    clock = _Clock()
    probed = []
    sh, door, _ = _live(tmp_path, clock=clock, memory=lambda: probed.append(1) or None)
    with open(tmp_path / "live.err", "wb") as fh:
        fh.truncate(size)                  # разреженный файл: размер без записи мегабайта
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    clock.now += ln.PRESSURE_CHECK_S
    door.on_message({"type": "front", "fed": SR, "frames": 5})
    if dies:
        assert sh.state == ln.DEAD and sh.reason == "журнал ребёнка вырос сверх 1 МБ"
        assert _ending(sh, tmp_path) == ln.END_GUARD
    else:
        assert sh.state != ln.DEAD, sh.reason
        assert probed == [1], "журнал ровно по потолку — память всё равно спрашивается"
        sh.stop()
        door.on_eof()


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


def _fronts(sh, door, clock, n, phys=None):
    """`n` фронтов через `PRESSURE_CHECK_S`; `phys` — след ребёнка в каждом (список или число)."""
    for k in range(n):
        clock.now += ln.PRESSURE_CHECK_S
        msg = {"type": "front", "fed": SR, "frames": 1 + k}
        value = phys[k] if isinstance(phys, list) else phys
        if value is not None:
            msg["phys_mb"] = value
        door.on_message(msg)


def test_critical_pressure_stops_the_stream_at_the_first_check(tmp_path):
    """Уровень 4: macOS уже выбирает, кого завершить, — выбор делает поток, а не ядро
    (вход №579, GLM C1): с первой проверки, без второй."""
    clock = _Clock()
    sh, door, says = _live(tmp_path, clock=clock, memory=_pressure([1, 4]))
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _fronts(sh, door, clock, 1, phys=800)
    assert sh.state == ln.LIVE
    _fronts(sh, door, clock, 1, phys=800)
    assert sh.state == ln.DEAD and sh.reason == "давление памяти критичное (уровень 4) — запись важнее потока"
    assert _ending(sh, tmp_path) == ln.END_GUARD
    mem = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "mem"]
    assert [m["pressure"] for m in mem] == [1, 4] and mem[0]["swap_used_mb"] == 1000
    assert [m["phys_mb"] for m in mem] == [800, 800], "в строке mem — след, по которому решали"
    _wait(lambda: door.child.killed.is_set(), what="ребёнок убит")
    _wait(lambda: says, what="строка человеку")


@pytest.mark.parametrize("level", [2, 3])
def test_pressure_below_critical_does_not_stop_the_stream(tmp_path, level):
    """№579: уровень 2 держится и без потока (A/B 01.10: 70 % проб в фазе без ребёнка) —
    поток с нормальным следом живёт под ним сколько угодно."""
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock, memory=_pressure([level] * 6))
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _fronts(sh, door, clock, 6, phys=1040)
    assert sh.state == ln.LIVE, sh.reason
    sh.stop()
    door.on_eof()


def test_own_footprint_over_budget_twice_in_a_row_stops_the_stream(tmp_path):
    """Свой след — своя вина: регрессия лимита кэша (30.09 — 8,5 ГБ) останавливает поток и
    при нормальном давлении. Ровно бюджет — не сверх; одиночный выброс — не режим."""
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock, memory=_pressure([1] * 5))
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    budget = ln.FOOTPRINT_STOP_MB
    _fronts(sh, door, clock, 3, phys=[budget + 1, budget, budget + 1])
    assert sh.state == ln.LIVE, "выброс, потом ровно бюджет — счётчик сброшен"
    _fronts(sh, door, clock, 1, phys=budget + 1)
    assert sh.state == ln.DEAD and sh.reason == f"след ребёнка {budget + 1} МБ сверх бюджета {budget} МБ"
    assert _ending(sh, tmp_path) == ln.END_GUARD
    mem = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "mem"]
    assert [m["phys_mb"] for m in mem] == [budget + 1, budget, budget + 1, budget + 1], \
        "проба памяти ложится и на проверке, которая убила поток (выход r1, GLM M2)"


def test_footprint_budget_works_without_a_pressure_reading(tmp_path):
    """Давление не читается (не macOS, sysctl отказал) — бюджет следа всё равно судит
    (вход №579, Sonnet I1); строк mem нет."""
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock, memory=lambda: None)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _fronts(sh, door, clock, 2, phys=3000)
    assert sh.state == ln.DEAD and "сверх бюджета" in sh.reason
    assert not [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "mem"]


def test_a_front_without_footprint_resets_the_budget_and_says_so_once(tmp_path):
    """Фронт без `phys_mb` не судит по старому значению: счётчик сброшен, и одна строка
    `budget` за встречу говорит, что бюджет не виден (вход №579, GLM I3 / Sonnet I1)."""
    clock = _Clock()
    sh, door, _ = _live(tmp_path, clock=clock, memory=_pressure([1] * 6))
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _fronts(sh, door, clock, 6, phys=[3000, None, 3000, None, None, 3000])
    assert sh.state == ln.LIVE, sh.reason
    rows = _journal(tmp_path / "live.jsonl")
    budget = [x for x in rows if x["type"] == "budget"]
    assert len(budget) == 1 and "phys_mb" in budget[0]["reason"]
    mem = [x for x in rows if x["type"] == "mem"]
    assert [m.get("phys_mb") for m in mem] == [3000, None, 3000, None, None, 3000]
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
    ({"sufler": {"live_nemotron": "maybe"}}, SR, BOTH, 1, "неизвестен"),
    ({"sufler": {"live_nemotron": "on"}}, SR, BOTH, 1, "трекер голосов"),   # on без раскладочного трекера
    ({"sufler": {"live_nemotron": True}}, SR, BOTH, 1, "трекер голосов"),   # голое on в YAML
    (SHADOW, 48000, BOTH, 1, "Гц"),
    (SHADOW, SR, {"mic"}, 1, "канала собеседников"),                      # захват только микрофона
    (SHADOW, SR, BOTH, 4, "давление памяти"),                             # критичное: запись важнее
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

FRAME = int(0.01 * dn.SAMPLE_RATE)          # кадр продукта: 10 мс спектра (фикс A2 №478)
LAG = int(1.04 * dn.SAMPLE_RATE)
CLAIMED = {claimed} * FRAME / dn.SAMPLE_RATE   # единица, которую ребёнок называет родителю


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
       "frame_s": CLAIMED, "step": dn.STREAM_STEP}})
sys.exit(dn.run_stream(Voices(), frame_s=CLAIMED, read=lambda n: os.read(0, n), emit=emit))
'''


def _hub():
    cfg = {"audio": {"samplerate": SR, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                     "vad_energy_db": -200.0, "record": False, "device": "auto"},
           "log": {"recordings_dir": "recordings"}, "sufler": {"user_name": "Владелец"}}
    hub = audio.AudioHub(cfg, captures=[])
    hub._register_captures([types.SimpleNamespace(label="blackhole")])
    return hub


def test_a_child_with_the_wrong_frame_unit_dies_and_the_journal_names_why(tmp_path):
    """Прежняя ошибка A2 настоящим процессом: ребёнок называет кадр в восемь раз длиннее
    того, которым считает модель. Сверка фронта роняет его на первом фронте, а строка
    `end` журнала несёт её причину — последнюю строку журнала ребёнка, а не голый «код 1»
    (выходной круг фикса A2 №478, M1 и критика 1)."""
    engine = tmp_path / "engine.py"
    engine.write_text(FAKE_ENGINE.format(src=str(SRC), claimed=8), encoding="utf-8")
    hub = _hub()
    cap = types.SimpleNamespace(label="blackhole")
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="s", say=lambda line: None,
                   memory=lambda: None)
    hub.add_frame_listener(sh.on_frame)
    sh.begin(python=sys.executable, script=engine, args=[], errlog=tmp_path / "live.err")
    _wait(lambda: sh.state == ln.LIVE, timeout=30, what="рукопожатие поддельного движка")
    voice = np.full(6 * SR, 0.3, dtype=np.float32)
    for pos in range(0, len(voice), 1600):
        if sh.state == ln.DEAD:
            break
        hub._consume(cap, voice[pos:pos + 1600])
        hub.pull_placed()
    _wait(lambda: _over(sh, tmp_path), timeout=30, what="ребёнок умер на сверке")
    end = _journal(tmp_path / "live.jsonl")[-1]
    assert end["type"] == "end" and end["exit"] == fp.FAILED
    assert end["ending"] == ln.END_CHILD_EXIT
    assert "впереди поданного звука" in end["exit_reason"] and "единица кадра не та" in end["exit_reason"]
    assert "единица кадра не та" in sh.reason, sh.reason


def test_hub_to_child_to_journal_keeps_one_axis(tmp_path, monkeypatch):
    """Первые 5 с звучат до старта потока (ось ребёнка начнётся не с нуля), голос
    меняется на 17-й секунде хаба. Каждый чанк после старта обязан получить слот
    своего голоса; сдвиг на `start0` перекрасил бы чанки 12,5–17 с, сдвиг на байт —
    все."""
    engine = tmp_path / "engine.py"
    engine.write_text(FAKE_ENGINE.format(src=str(SRC), claimed=1), encoding="utf-8")
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
    _wait(lambda: _over(sh, tmp_path), timeout=30, what="поток закрыт")
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
    assert _ending(sh, tmp_path) == ln.END_STOPPED
    _wait(lambda: door.child.killed.is_set(), what="неподобранный ребёнок убит")
    time.sleep(0.05)
    assert says == [], "штатная остановка человеку не пишет"


def test_a_bad_handshake_after_the_stop_ends_as_not_started(tmp_path):
    """Стоп пришёл, пока ребёнок поднимался, а рукопожатие негодно: исход — по причине
    (не стартовал), а не по состоянию (финальный Opus, M2)."""
    door = _Door(ready={**READY_OK, "proto": 2}, ok_after_cancel=True)
    sh, door, says = _shadow(tmp_path, door=door)
    _wait(lambda: door.on_eof is not None, what="дверь зовётся")
    sh.stop()
    _wait(lambda: _over(sh, tmp_path), what="тень закончилась")
    assert "протокол" in sh.reason
    assert _ending(sh, tmp_path) == ln.END_NOT_STARTED
    _wait(lambda: door.child.killed.is_set(), what="неподобранный ребёнок убит")


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
    assert _ending(sh, tmp_path) == ln.END_GUARD
    assert sh.state == ln.DEAD and "отстал" in sh.reason


@pytest.mark.parametrize("exit_, said", [
    (fp.Outcome(fp.FAILED, reason="код 1: RuntimeError: фронт впереди"), "код 1: RuntimeError: фронт впереди"),
    (fp.Outcome(fp.OK), "вышел"),
])
def test_a_closed_input_of_the_child_stops_the_shadow_with_the_child_exit(tmp_path, exit_, said):
    """Писатель замечает смерть ребёнка раньше читателя. Причина в строке `end` — выход
    ребёнка (код и последняя строка его журнала), а не сломанная труба: иначе сверка
    фронта и падение модели терялись (выходной круг фикса A2 №478)."""
    door = _Door()

    def closed(data):
        raise BrokenPipeError("труба закрыта")
    door.child.write = closed
    door.child.exit = exit_
    sh, door, _ = _live(tmp_path, door=door)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    _wait(lambda: _over(sh, tmp_path), what="тень умерла")
    assert sh.reason == f"вход ребёнка закрыт (труба закрыта); ребёнок: {said}"
    end = _end(tmp_path)
    assert end["exit"] == exit_.kind and end.get("exit_reason", "") == exit_.reason
    assert end["ending"] == ln.END_CHILD_EXIT


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
    _start_thread_done()                     # иначе её finally может взять строку start и умереть позже проверки

    class Full:
        def write(self, text):
            raise OSError("диск полон")
    real, sh._journal = sh._journal, Full()
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))          # строка start — в очередь
    sh.note_chunk(_placed(1, 0, 1600), "off")          # аудиопоток не пишет: пишет следующий вход (№533)
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
    assert "режиму on" in says[0]


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
    assert kw["args"][:2] == ["--stream", "--model"]
    assert kw["args"][-6:-2] == ["--preset", ln.PRESET, "--cache-limit-mb", str(ln.CACHE_LIMIT_MB)], (
        "лимит кэша MLX — всегда: без него живая тень 30.09 заняла 8,5 ГБ и умерла")
    assert kw["args"][-2:] == ["--parent-pid", str(os.getpid())], (
        "ребёнок знает своего родителя — демона, а не того, кто есть при старте его нити (№540)")
    assert says == [f"поток Nemotron: тень включена, журнал nemotron_live_{stamp}.jsonl"]


def test_start_without_its_journal_is_no_shadow_and_starts_no_child(tmp_path, monkeypatch):
    """Журнал тени не открылся — тени нет: тот же `NO_SHADOW`, что у любого отказа, и ребёнок
    не запускается; демон зовёт у ответа `attach` и `stop` без проверок (мутатор диапазона, 29.09)."""
    monkeypatch.setattr(ln.diarize_nemotron, "engine_interpreter", lambda setting, root: ("/py", None))
    monkeypatch.setattr(ln.Shadow, "begin", lambda self, **kw: pytest.fail("ребёнок без журнала"))
    (tmp_path / "logs").write_text("не каталог", encoding="utf-8")      # журнал не открыть
    says = []
    assert ln.start(SHADOW, root=tmp_path, stamp="s", sr=SR, labels=BOTH, say=says.append,
                    memory=lambda: None) is ln.NO_SHADOW
    assert len(says) == 1 and says[0].startswith("поток Nemotron выключен: журнал не открылся ("), says


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


# ------------------------------------------------ ребёнок принадлежит переходу в DEAD (выходной круг 2, I1)

def test_a_writer_failing_during_the_stop_leaves_no_child(tmp_path):
    """Писатель упал не на трубе, пока тень останавливается: конец штатный, но ребёнок
    без EOF убит тем же переходом в DEAD — отсрочка на DEAD уже ничего не делает."""
    door = _Door()
    release = threading.Event()

    def stuck_then_broken(data):
        release.wait(10)
        raise ValueError("не байты")
    door.child.write = stuck_then_broken
    sh, door, says = _live(tmp_path, door=door)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    sh.stop()
    release.set()
    _wait(lambda: sh.state == ln.DEAD, what="тень закончилась")
    assert sh.reason.startswith("писатель упал")
    assert door.child.killed.is_set(), "ребёнок без EOF пережил тень"
    assert says == [], "остановка — не смерть: человеку не пишется"


def test_no_thread_for_the_writer_or_the_death_still_kills_the_child(tmp_path, monkeypatch):
    import threads
    real = threads.spawn

    def exhausted(target, *, name, role, **kw):
        if name in ("nemotron-live-writer", "nemotron-live-death"):
            raise RuntimeError("can't start new thread")
        return real(target, name=name, role=role, **kw)
    monkeypatch.setattr(threads, "spawn", exhausted)
    sh, door, _ = _shadow(tmp_path)
    _wait(lambda: _over(sh, tmp_path), what="тень умерла")
    assert "писатель не завёлся" in sh.reason
    assert _ending(sh, tmp_path) == ln.END_FAULT
    assert door.child.killed.is_set(), "ребёнок с моделью остался без хозяина"
    assert [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "end"]


def test_a_start_thread_that_does_not_start_ends_the_shadow_with_a_line(tmp_path, monkeypatch):
    import threads
    real = threads.spawn

    def no_start(target, *, name, role, **kw):
        if name == "nemotron-live-start":
            raise RuntimeError("can't start new thread")
        return real(target, name=name, role=role, **kw)
    monkeypatch.setattr(threads, "spawn", no_start)
    sh, door, _ = _shadow(tmp_path)
    assert sh.state == ln.DEAD and sh.reason.startswith("нить запуска не завелась")
    assert _end(tmp_path)["reason"] == sh.reason
    assert _end(tmp_path)["ending"] == ln.END_NOT_STARTED
    assert door.on_eof is None, "дверь не звали — ребёнка нет"


def test_a_stop_without_a_timer_kills_at_once_without_waiting(tmp_path, monkeypatch):
    """Таймер отсрочки не завёлся — главная нить демона убивает сразу и не ждёт выхода:
    пересборке нужна машина, а не пять секунд ожидания."""
    import threads

    def no_timer(*a, **k):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threads, "timer", no_timer)
    sh, door, _ = _live(tmp_path)
    sh.stop()
    assert door.child.killed.is_set() and door.child.nowait_kills == 1
    door.on_eof()


# ------------------------------------------------ аудиопоток не ждёт ввода-вывода тени (№533)

#: Потолок возврата `on_frame`, пока другая нить тени стоит на диске или опросе памяти:
#: блок хаба — 0,25 с звука; без правки `on_frame` ждал все 0,5 с сна.
FRAME_BUDGET_S = 0.05


def _start_thread_done():
    """Нить запуска дописывает журнал в своём finally уже после LIVE — дождаться её, чтобы
    очередь журнала принадлежала тесту."""
    for t in threading.enumerate():
        if t.name == "nemotron-live-start":
            t.join(5)


def _frame_time(sh, start, n=1600):
    t0 = time.perf_counter()
    sh.on_frame("blackhole", start, np.zeros(n, dtype=np.float32))
    return time.perf_counter() - t0


def test_the_audio_thread_does_not_wait_for_the_memory_probe(tmp_path):
    """Читатель в проверке здоровья спит в опросе памяти — кадр хаба проходит сразу."""
    inside, release = threading.Event(), threading.Event()

    def slow_memory():
        inside.set()
        release.wait(0.5)
        return {"pressure": 1, "swap_used_mb": 0}
    sh, door, _ = _live(tmp_path, memory=slow_memory)
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    reader = threading.Thread(target=door.on_message, args=({"type": "front", "fed": 1600, "frames": 1},))
    reader.start()
    assert inside.wait(5), "проверка здоровья не пришла"
    took = _frame_time(sh, 1600)
    release.set()
    reader.join(5)
    assert took < FRAME_BUDGET_S, f"on_frame ждал {took:.3f} с"
    assert sh.state == ln.LIVE
    sh.stop()
    door.on_eof()
    assert [x["type"] for x in _journal(tmp_path / "live.jsonl")].count("mem") == 1


class _SlowJournal:
    """Журнал, чья запись встаёт, пока открыт шлюз: диск под logs/ задумался."""

    def __init__(self, real):
        self.real = real
        self.gate = threading.Event()
        self.gate.set()
        self.inside = threading.Event()

    def write(self, text):
        if not self.gate.is_set():
            self.inside.set()
            self.gate.wait(0.5)
        return self.real.write(text)


def test_the_audio_thread_does_not_wait_for_the_journal(tmp_path):
    sh, door, _ = _live(tmp_path)
    _start_thread_done()
    slow = sh._journal = _SlowJournal(sh._journal)
    slow.gate.clear()
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))     # строка start — в очередь
    stt = threading.Thread(target=sh.note_chunk, args=(_placed(1, 0, 1600), "off"))
    stt.start()
    assert slow.inside.wait(5), "распознавание не дошло до записи"
    took = _frame_time(sh, 1600)
    slow.gate.set()
    stt.join(5)
    assert took < FRAME_BUDGET_S, f"on_frame ждал {took:.3f} с"
    sh.stop()
    door.on_eof()


def test_the_audio_thread_leaves_its_lines_to_the_next_writer_in_order(tmp_path):
    """`on_frame` на диск не пишет: строка `start` ждёт в очереди и ложится раньше
    строк, изменивших состояние после неё."""
    sh, door, _ = _live(tmp_path)
    _start_thread_done()
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    assert "start" not in [x["type"] for x in _journal(tmp_path / "live.jsonl")]
    door.on_message({"type": "front", "fed": 1600, "frames": 1})
    kinds = [x["type"] for x in _journal(tmp_path / "live.jsonl")]
    assert kinds[:4] == ["header", "ready", "start", "front"]
    sh.stop()
    door.on_eof()


def test_a_death_in_the_audio_thread_reaches_the_journal_and_the_human_without_it(tmp_path):
    """Разрыв оси в аудиопотоке: строки смерти и строку человеку доводит писатель тени,
    которого будит сентинель смерти, — аудиопоток сам нити не заводит."""
    sh, door, says = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    sh.on_frame("blackhole", 1601, np.zeros(1600, dtype=np.float32))
    assert sh.state == ln.DEAD
    _wait(lambda: says, what="строка человеку")
    _wait(lambda: any(x["type"] == "end" for x in _journal(tmp_path / "live.jsonl")), what="строка end")


def test_a_journal_that_breaks_with_any_error_stops_the_shadow_and_stops_queueing(tmp_path):
    class Broken:
        def write(self, text):
            raise TypeError("не строка")
    sh, door, says = _live(tmp_path)
    sh._journal = Broken()
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    sh.note_chunk(_placed(1, 0, 1600), "off")
    assert sh.state == ln.DEAD and "журнал тени не пишется" in sh.reason
    sh.note_chunk(_placed(2, 1600, 1600), "off")
    assert len(sh._out) == 0, "мёртвый журнал строк не копит"
    _wait(lambda: says, what="строка человеку")


def test_stop_does_not_wait_for_the_journal(tmp_path):
    """`stop` зовёт главная нить демона перед запуском пересборки — диска она не ждёт."""
    sh, door, _ = _live(tmp_path)
    slow = sh._journal = _SlowJournal(sh._journal)
    slow.gate.clear()
    stt = threading.Thread(target=sh.note_chunk, args=(_placed(1, 0, 1600), "off"))
    stt.start()
    assert slow.inside.wait(5)
    t0 = time.perf_counter()
    sh.stop()
    took = time.perf_counter() - t0
    slow.gate.set()
    stt.join(5)
    assert took < FRAME_BUDGET_S, f"stop ждал {took:.3f} с"
    door.on_eof()


# ------------------------------------------------ выход демона: журнал тени с концом (№533)

def test_close_kills_a_hung_child_inside_the_watchdog_window(tmp_path):
    """Настоящий ребёнок через дверь: рукопожатие, потом сон без чтения входа (модель
    повисла). Сторож приложения добивает демона SIGKILL через 5 с после SIGTERM, и
    уборку при выходе SIGKILL не пускает — поэтому `close` не ждёт таймер отсрочки
    (штатные 5 с), а убивает ребёнка сам и возвращается с `end` в журнале (выходной
    круг 1 по №533, C1)."""
    script = tmp_path / "engine.py"
    script.write_text(
        "import json, sys, time\n"
        f"print(json.dumps({json.dumps(READY_OK)}), flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8")
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="2026-09-30_120000", say=lambda t: None,
                   memory=lambda: None)
    sh.begin(python=sys.executable, script=script, args=[], errlog=tmp_path / "live.err")
    _wait(lambda: sh.state == ln.LIVE, what="тень живёт")
    pid = sh._stream.pid
    t0 = time.monotonic()
    sh.close(ln.STOP_GRACE_S)
    took = time.monotonic() - t0
    assert took < ln.CLOSE_WAIT_S + ln.CLOSE_MARGIN_S + 1.0, f"close ждал {took:.2f} с"
    _wait(lambda: not _alive(pid) and sh.state == ln.DEAD, timeout=2.0, what="ребёнок убит закрытием")
    (end,) = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "end"]
    assert end["ending"] == ln.END_STOPPED, "убит закрытием — всё равно штатный стоп; убийство считает счётчик"
    assert end["counts"]["killed_at_close"] == 1


def test_the_end_is_queued_before_anyone_is_woken(tmp_path):
    """`_dead` и сентинель писателя поднимаются после строки `end`: проснувшийся `close`
    дописывает журнал с концом, а не без него (выходной круг 1 по №533, I1)."""
    sh, door, _ = _live(tmp_path)
    seen = []

    class Watch(threading.Event):
        def set(self):
            seen.append(any(o.get("type") == "end" for o in sh._out))
            super().set()
    sh._dead = Watch()
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    sh.on_frame("blackhole", 1601, np.zeros(1600, dtype=np.float32))     # разрыв оси — смерть
    assert seen == [True]


def test_close_does_not_wait_past_the_grace_for_an_end_that_never_comes(tmp_path, monkeypatch):
    monkeypatch.setattr(ln, "STOP_GRACE_S", 0.1)
    monkeypatch.setattr(ln, "CLOSE_MARGIN_S", 0.1)
    sh, door, _ = _live(tmp_path)
    t0 = time.monotonic()
    sh.close(10.0)                          # stop изнутри; EOF поддельный ребёнок не шлёт
    assert 0.15 <= time.monotonic() - t0 < 2.0
    assert sh.state == ln.STOPPING and door.child.killed.is_set()


def test_close_counts_the_grace_from_the_stop_not_anew(tmp_path, monkeypatch):
    monkeypatch.setattr(ln, "STOP_GRACE_S", 0.3)
    monkeypatch.setattr(ln, "CLOSE_MARGIN_S", 0.0)
    sh, door, _ = _live(tmp_path)
    sh.stop()
    time.sleep(0.3)
    t0 = time.monotonic()
    sh.close(10.0)
    assert time.monotonic() - t0 < 0.1


def test_close_is_capped_by_its_timeout(tmp_path, monkeypatch):
    """Потолок `timeout` держат оба ожидания `close` — и конца тени, и строки end после
    убийства: ребёнок-заглушка EOF не шлёт, ждать больше потолка нечего."""
    monkeypatch.setattr(ln, "CLOSE_MARGIN_S", 3.0)
    sh, door, _ = _live(tmp_path)
    t0 = time.monotonic()
    sh.close(0.2)
    assert time.monotonic() - t0 < 1.0
    door.on_eof()


def test_close_writes_what_the_audio_thread_left_in_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(ln, "STOP_GRACE_S", 0.0)
    monkeypatch.setattr(ln, "CLOSE_MARGIN_S", 0.0)
    sh, door, _ = _live(tmp_path)
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    sh.close(1.0)
    assert "start" in [x["type"] for x in _journal(tmp_path / "live.jsonl")]


def test_no_shadow_closes_and_stops_quietly_without_threads():
    """Без тени выход демона не ждёт и не заводит нитей: `close` и `stop(grace=…)` пустые."""
    before = {t.ident for t in threading.enumerate()}
    t0 = time.monotonic()
    assert ln.NO_SHADOW.stop(grace=ln.EXIT_GRACE_S) is None
    assert ln.NO_SHADOW.close(ln.STOP_GRACE_S) is None
    assert time.monotonic() - t0 < 0.05
    assert {t.ident for t in threading.enumerate()} <= before


def _hung_engine(tmp_path):
    script = tmp_path / "engine.py"
    script.write_text(
        "import json, sys, time\n"
        f"print(json.dumps({json.dumps(READY_OK)}), flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8")
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="2026-09-30_120000", say=lambda t: None,
                   memory=lambda: None)
    sh.begin(python=sys.executable, script=script, args=[], errlog=tmp_path / "live.err")
    _wait(lambda: sh.state == ln.LIVE, what="тень живёт")
    return sh


def test_the_exit_grace_kills_a_hung_child_before_the_hub_is_done(tmp_path):
    """Боевой порядок выхода: `stop` первым, потом `hub.stop()` (до 6 с), `close` последним.
    Повисшего ребёнка убивает таймер отсрочки выхода, а не `close` — раньше SIGKILL сторожа
    приложения через 5 с (выходной круг 2 по №533, I1)."""
    sh = _hung_engine(tmp_path)
    pid = sh._stream.pid
    sh.stop(grace=ln.EXIT_GRACE_S)
    _wait(lambda: not _alive(pid), timeout=ln.EXIT_GRACE_S + 2.0, what="ребёнок убит отсрочкой выхода")
    sh.close(ln.STOP_GRACE_S)
    (end,) = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "end"]
    assert end["counts"].get("killed_after_grace") == 1 and "killed_at_close" not in end["counts"]


class _WatchedQueue:
    """Очередь к писателю с наблюдателем `put`; писатель ждёт на той же настоящей очереди."""

    def __init__(self, real, watch):
        self.real, self.watch = real, watch

    def put(self, item):
        self.watch(item)
        self.real.put(item)

    def get(self, *a, **k):
        return self.real.get(*a, **k)


def test_the_writer_is_woken_after_the_end_is_queued(tmp_path):
    sh, door, _ = _live(tmp_path)
    seen = []
    sh._queue = _WatchedQueue(sh._queue, lambda item: item is None and seen.append(
        any(o.get("type") == "end" for o in sh._out)))
    sh.on_frame("blackhole", 0, np.zeros(1600, dtype=np.float32))
    sh.on_frame("blackhole", 1601, np.zeros(1600, dtype=np.float32))     # разрыв оси — смерть
    assert seen and all(seen)


def test_a_finish_that_breaks_still_wakes_the_waiters(tmp_path, monkeypatch):
    sh, door, _ = _live(tmp_path)

    def boom(outcome, reason=""):
        raise RuntimeError("сломался конец")
    monkeypatch.setattr(sh, "_finish_pending_locked", boom)
    woken = []
    sh._queue = _WatchedQueue(sh._queue, woken.append)
    with pytest.raises(RuntimeError), sh._lock:
        sh._die_locked("проверка", ln.END_FAULT)
    assert sh._dead.is_set() and None in woken


def test_one_kill_is_counted_once_when_the_grace_and_close_race(tmp_path, monkeypatch):
    sh, door, _ = _live(tmp_path)
    door.child.alive = lambda: True           # ребёнок «не умирает» от сигнала — оба пути видят его живым
    sh.stop(grace=0.01)
    _wait(lambda: door.child.nowait_kills >= 1, what="таймер убил")
    monkeypatch.setattr(ln, "CLOSE_MARGIN_S", 0.0)
    sh.close(0.2)
    assert door.child.nowait_kills == 1
    assert sh._counts["killed_after_grace"] == 1 and sh._counts["killed_at_close"] == 0
    door.on_eof()


def test_only_a_well_formed_front_asks_for_the_health_check(tmp_path):
    """Проверку здоровья (опрос памяти вне замка) заказывает только разобранный фронт —
    не сегмент и не битый фронт."""
    clock = _Clock()
    probed = []
    sh, door, _ = _live(tmp_path, clock=clock, memory=lambda: probed.append(1) or None)
    sh.on_frame("blackhole", 0, np.zeros(SR, dtype=np.float32))
    clock.now += ln.PRESSURE_CHECK_S
    door.on_message({"type": "seg", "start": 0.0, "end": 0.5, "slot": 0})
    door.on_message({"type": "front", "fed": "много", "frames": 1})
    assert probed == []
    with sh._lock:
        assert sh._take_front_locked({"frames": "x", "fed": 1}, clock.now) is False
    door.on_message({"type": "front", "fed": SR, "frames": 1})
    assert probed == [1]
    with sh._lock:
        assert sh._take_front_locked({"frames": 2, "fed": SR}, clock.now) is False, "рано: интервал не прошёл"
    sh.stop()
    door.on_eof()


# ------------------------------------------------ режим on (№478 B): ожидание метки

def _on(tmp_path, wait=None):
    """Поток в режиме `on`; `wait` — подставное ожидание (по умолчанию настоящее)."""
    says = []
    kw = {"wait": wait} if wait is not None else {}
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="2026-09-29_120000", say=says.append,
                   memory=lambda: None, mode=ln.ON, **kw)
    door = _Door()
    sh.begin(python="python", script=tmp_path / "engine.py", args=[], errlog=tmp_path / "live.err", spawn=door)
    _wait(lambda: sh.state == ln.LIVE, what="поток живёт")
    for t in threading.enumerate():
        if t.name == "nemotron-live-start":
            t.join(5)
    block = np.zeros(SR, dtype=np.float32)
    for k in range(4):
        sh.on_frame("blackhole", 5 * SR + k * SR, block)     # start0 = 5 с на оси хаба
    return sh, door, says


def _builder(calls):
    def build(segs):
        calls.append(segs)
        if segs:
            return "по потоку", {"source": ln.SOURCE_STREAM, "pieces": 1, "no_recon": 0, "recon_agree": 0}
        return "по трекеру", {"source": ln.SOURCE_TRACKER}
    return build


SEG = {"type": "seg", "start": 0.0, "end": 2.0, "slot": 1, "open": False}
FRONT = {"type": "front", "fed": 2 * SR, "frames": 25}          # фронт: start0 + 2 с


def test_a_chunk_that_gets_its_label_in_time_is_laid_out_by_the_stream_with_one_line(tmp_path):
    box, waits = {}, []

    def wait(event, cap):
        waits.append(cap)
        box["door"].on_message(SEG)
        box["door"].on_message(FRONT)
        return event.is_set()
    sh, door, _ = _on(tmp_path, wait)
    box["door"] = door
    calls = []
    got = sh.label_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces", _builder(calls))
    assert got == "по потоку" and waits == [ln.WAIT_CAP_S]
    assert calls == [[(5 * SR, 7 * SR, 1)]], "сегменты на оси хаба, задевающие чанк"
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["outcome"], line["source"], line["pieces"]) == (ln.LABELED, ln.SOURCE_STREAM, 1)
    assert "fallback" not in line and line["slots"] == {"1": 1.0}
    sh.stop()
    door.on_eof()


def test_a_chunk_that_waits_past_the_cap_goes_to_the_tracker_and_the_late_front_adds_nothing(tmp_path):
    sh, door, _ = _on(tmp_path, lambda event, cap: False)
    calls = []
    assert sh.label_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces", _builder(calls)) == "по трекеру"
    assert calls == [None]
    door.on_message(SEG)
    door.on_message(FRONT)                   # метка пришла после потолка: вторую строку не пишет
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["outcome"], line["source"], line["fallback"]) == (ln.FALLBACK, ln.SOURCE_TRACKER, ln.FALLBACK)
    assert "2" in line["reason"]
    sh.stop()
    door.on_eof()
    _wait(lambda: _over(sh, tmp_path), what="конец потока")
    end = [x for x in _journal(tmp_path / "live.jsonl") if x["type"] == "end"][0]
    assert end["counts"].get("chunk_fallback") == 1 and "chunk_stopped" not in end["counts"]


def test_a_stream_that_dies_while_the_chunk_waits_wakes_it_with_the_reason(tmp_path):
    box = {}

    def wait(event, cap):
        box["sh"].on_frame("blackhole", 0, np.zeros(10, dtype=np.float32))    # разрыв оси — смерть
        return event.is_set()
    sh, door, says = _on(tmp_path, wait)
    box["sh"] = sh
    calls = []
    assert sh.label_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces", _builder(calls)) == "по трекеру"
    assert calls == [None] and sh.state == ln.DEAD
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["outcome"], line["fallback"]) == (ln.DEAD_STREAM, ln.DEAD_STREAM) and "разрыв" in line["reason"]
    _wait(lambda: says, what="строка человеку")
    assert says[0].startswith("поток Nemotron остановлен: ") and "снова от трекера" in says[0]


def test_a_chunk_before_the_stream_does_not_wait(tmp_path):
    sh, door, _ = _on(tmp_path, lambda event, cap: pytest.fail("чанк до потока не ждёт"))
    calls = []
    assert sh.label_chunk(_placed(0, 0, SR), "pieces", _builder(calls)) == "по трекеру"
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["outcome"], line["fallback"]) == (ln.BEFORE_STREAM, ln.BEFORE_STREAM)
    sh.stop()
    door.on_eof()


def test_a_labelled_chunk_where_the_layout_breaks_falls_back_with_one_line(tmp_path):
    box = {}

    def wait(event, cap):
        box["door"].on_message(SEG)
        box["door"].on_message(FRONT)
        return event.is_set()
    sh, door, says = _on(tmp_path, wait)
    box["door"] = door
    calls = []

    def build(segs):
        calls.append(segs)
        if segs:
            raise ValueError("раскладка упала")
        return "по трекеру", {"source": ln.SOURCE_TRACKER}
    assert sh.label_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces", build) == "по трекеру"
    assert len(calls) == 2 and calls[1] is None
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["outcome"], line["source"], line["fallback"]) == (ln.LABELED, ln.SOURCE_TRACKER, "build_failed")
    _wait(lambda: says, what="сбой сказан")
    assert "раскладка" in says[0]
    sh.stop()
    door.on_eof()


def test_a_tracker_fallback_that_breaks_too_still_leaves_its_line_and_raises(tmp_path):
    sh, door, _ = _on(tmp_path, lambda event, cap: False)

    def build(segs):
        raise RuntimeError("и трекер упал")
    with pytest.raises(RuntimeError):
        sh.label_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces", build)
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["outcome"], line["source"], line["fallback"]) == (ln.FALLBACK, ln.SOURCE_TRACKER, "build_failed")
    sh.stop()
    door.on_eof()


def test_the_real_wait_wakes_on_the_front_from_the_reader_thread(tmp_path):
    sh, door, _ = _on(tmp_path)

    def reader():
        time.sleep(0.05)
        door.on_message(SEG)
        door.on_message(FRONT)
    threading.Thread(target=reader).start()
    calls = []
    t0 = time.monotonic()
    assert sh.label_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces", _builder(calls)) == "по потоку"
    assert time.monotonic() - t0 < ln.WAIT_CAP_S, "проснулся по фронту, а не по потолку"
    sh.stop()
    door.on_eof()


def test_a_chunk_of_another_channel_is_laid_out_by_the_tracker_without_a_line(tmp_path):
    sh, door, _ = _on(tmp_path, lambda event, cap: pytest.fail("чужой канал не ждёт"))
    calls = []
    assert sh.label_chunk(_placed(3, 5 * SR, SR, label="mic"), "pieces", _builder(calls)) == "по трекеру"
    assert calls == [None] and _chunks(tmp_path / "live.jsonl") == []
    sh.stop()
    door.on_eof()


def test_a_shadow_line_names_the_tracker_as_its_source(tmp_path):
    sh, door, _ = _live(tmp_path)
    sh.note_chunk(_placed(0, 0, SR), "off")
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert line["source"] == ln.SOURCE_TRACKER and "fallback" not in line
    assert sh.stream_channel is None, "тень ничего не подписывает"
    sh.stop()
    door.on_eof()


def test_the_on_journal_header_names_the_mode_the_cap_and_the_show_threshold(tmp_path):
    sh, door, _ = _on(tmp_path, lambda event, cap: False)
    head = _journal(tmp_path / "live.jsonl")[0]
    assert (head["v"], head["mode"], head["wait_cap_s"], head["slot_show_s"]) == (
        ln.JOURNAL_V, ln.ON, ln.WAIT_CAP_S, ln.SLOT_SHOW_S)
    assert sh.stream_channel == ln.CHANNEL and sh.live is True
    sh.stop()
    door.on_eof()
    _wait(lambda: sh.state == ln.DEAD, what="конец")
    assert sh.live is False


def test_start_raises_the_on_mode_only_with_a_split_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(ln.diarize_nemotron, "engine_interpreter", lambda setting, root: ("/py", None))
    monkeypatch.setattr(ln.Shadow, "begin", lambda self, **kw: None)
    (tmp_path / "logs").mkdir()
    says = []
    sh = ln.start({"sufler": {"live_nemotron": "on"}}, root=tmp_path, stamp="s", sr=SR, labels=BOTH,
                  say=says.append, split_tracker=True, memory=lambda: None)
    assert isinstance(sh, ln.Shadow) and sh.stream_channel == ln.CHANNEL
    assert says == ["поток Nemotron: метки собеседников из потока, журнал nemotron_live_s.jsonl"]


def test_no_shadow_lays_out_by_the_tracker_and_never_streams():
    assert ln.NO_SHADOW.stream_channel is None and ln.NO_SHADOW.live is False
    assert ln.NO_SHADOW.label_chunk(_placed(0, 0, 10), "pieces", lambda segs: (segs, {})) is None


def test_a_stream_far_behind_the_chunk_is_not_waited_for(tmp_path):
    """Фронт отстал от конца чанка больше потолка: поток идёт со скоростью звука и не догонит,
    чанк сразу уходит трекеру (выходной круг 1 №478 B, I4)."""
    sh, door, _ = _on(tmp_path, lambda event, cap: pytest.fail("отстающий поток не ждут"))
    door.on_message({"type": "front", "fed": SR // 2, "frames": 6})          # фронт: start0 + 0,48 с
    calls = []
    assert sh.label_chunk(_placed(7, 8 * SR, SR), "pieces", _builder(calls)) == "по трекеру"   # конец: + 4 с
    (line,) = _chunks(tmp_path / "live.jsonl")
    assert (line["outcome"], line["fallback"]) == (ln.FALLBACK, ln.FALLBACK) and "отстаёт" in line["reason"]
    assert line["behind_s"] > ln.WAIT_CAP_S
    sh.stop()
    door.on_eof()


def test_a_stream_just_behind_the_chunk_is_waited_for(tmp_path):
    box = {}

    def wait(event, cap):
        box["waited"] = cap
        return False
    sh, door, _ = _on(tmp_path, wait)
    door.on_message({"type": "front", "fed": 2 * SR, "frames": 25})          # фронт: start0 + 2 с
    sh.label_chunk(_placed(7, 5 * SR + SR, 2 * SR), "pieces", _builder([]))  # конец: start0 + 3 с
    assert box["waited"] == ln.WAIT_CAP_S, "отставание 1 с — метка успеет"
    sh.stop()
    door.on_eof()


def test_the_line_of_a_waited_chunk_is_stamped_when_it_is_queued(tmp_path):
    clock = _Clock()
    box = {}

    def wait(event, cap):
        box["door"].on_message(SEG)
        box["door"].on_message(FRONT)
        return event.is_set()
    says = []
    sh = ln.Shadow(journal=tmp_path / "live.jsonl", sr=SR, stamp="s", say=says.append, clock=clock,
                   memory=lambda: None, mode=ln.ON, wait=wait)
    door = _Door()
    box["door"] = door
    sh.begin(python="python", script=tmp_path / "e.py", args=[], errlog=tmp_path / "e.err", spawn=door)
    _wait(lambda: sh.state == ln.LIVE)
    for k in range(4):
        sh.on_frame("blackhole", 5 * SR + k * SR, np.zeros(SR, dtype=np.float32))

    def build(segs):
        clock.now += 5.0                     # раскладка шла, пока в журнал ложились другие строки
        box["door"].on_message({"type": "front", "fed": 3 * SR, "frames": 37})
        return "x", {"source": ln.SOURCE_STREAM}
    sh.label_chunk(_placed(7, 5 * SR + SR // 2, SR), "pieces", build)
    ts = [x["t"] for x in _journal(tmp_path / "live.jsonl") if "t" in x]
    assert ts == sorted(ts), "t строк журнала монотонно"
    sh.stop()
    door.on_eof()


def test_the_stream_plan_takes_the_capture_label_of_a_real_hub_chunk_not_its_signature(tmp_path):
    """Чанк настоящего хаба несёт подпись канала («Собеседник») в `speaker` и метку захвата в
    `seq[0]`: план `stream` сравнивает с каналом потока метку захвата. По подписи он не
    выбирался бы никогда, и `on` работал бы тенью (выходной круг 1 №478 B, C1)."""
    import stt_runtime
    cfg = {"audio": {"samplerate": SR, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                     "vad_energy_db": -60.0, "record": False, "device": "auto"},
           "log": {"recordings_dir": "recordings"}, "sufler": {"user_name": "Владелец"}}
    sh, door, _ = _on(tmp_path, lambda event, cap: False)
    loud = np.full(SR // 10, 0.3, dtype=np.float32)
    quiet = np.zeros(SR // 10, dtype=np.float32)
    placed = {}
    for talking in ("blackhole", "mic"):             # говорит одна сторона: другую хаб не глушит эхом
        hub = audio.AudioHub(cfg, captures=[])
        caps = [types.SimpleNamespace(label="blackhole"), types.SimpleNamespace(label="mic")]
        hub._register_captures(caps)
        for _ in range(40):
            for cap in caps:
                hub._consume(cap, loud if cap.label == talking else quiet)
        placed.update({p.seq[0]: p for p in hub.pull_placed()})
    assert set(placed) == {"blackhole", "mic"}
    assert placed["blackhole"].speaker != ln.CHANNEL, "подпись канала — не метка захвата"
    plans = {label: stt_runtime.diarization_plan(lagging=False, has_split=True, channel=p.seq[0],
                                                 stream_channel=sh.stream_channel, stream_live=sh.live)
             for label, p in placed.items()}
    assert plans == {"blackhole": "stream", "mic": "diarize"}
    sh.stop()
    door.on_eof()
