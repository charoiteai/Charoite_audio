"""Канал собеседников мёртв посреди звонка (№654): кадры идут, в них нули, а
собеседники звучат через динамики. Сторож кадров молчит; признак — часы
содержимого (те же байты, что в `.pcm`), звонок и речь в микрофоне за окно.

Опыт 30.10 по 9 записям 28–29.10: законные серии нулей в звонке — до 394 с,
отказы — 818 и 5015 с; хвост после конца звонка гасит доля микрофона (там 5–10 %,
на отказе 28 %).
"""
from __future__ import annotations
import charoite_paths

import pathlib
import queue
import sys
import tempfile
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import audio as a  # noqa: E402
import channel_trace  # noqa: E402
import speech_gate  # noqa: E402


class _Cap:
    def __init__(self, label):
        self.label = label
        self.q = queue.Queue()

    def stop(self):
        pass


def _hub(*labels):
    cfg = {"audio": {"samplerate": 16000, "chunk_seconds": 3.0, "overlap_seconds": 0.5,
                     "vad_energy_db": -45.0, "record": False, "device": "auto"},
           "log": {"recordings_dir": "recordings"}, "sufler": {"user_name": "Владелец"}}
    return a.AudioHub(cfg, captures=[_Cap(lbl) for lbl in labels])


def _quiet(monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kw: None)
    charoite_paths.use_data_root(pathlib.Path(tempfile.mkdtemp()), replace=True)


def _dead_call(hub, now, *, age=a.CONTENT_SILENT_S + 5, share=0.5):
    """Звонок идёт, содержимое канала стоит `age` секунд, микрофон звучит долей `share`."""
    hub.set_call()
    hub._content_ts["blackhole"] = now - age
    step = speech_gate.step_seconds(hub.chunk_s, hub.overlap_s)
    n = int(share * a.CONTENT_SILENT_S / step)
    hub._mic_speech.extend(now - a.CONTENT_SILENT_S + 1 + i for i in range(n))


def test_content_clock_follows_the_recorded_bytes_not_the_float_block(monkeypatch):
    """Возраст — по байтам записи: блок, который квантуется в нули, звуком не
    считается, хотя float в нём ненулевой (Important Sonnet in1)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    cap = hub.captures[1]
    hub._consume(cap, np.full(400, 1e-6, dtype=np.float32))
    assert "blackhole" not in hub._content_ts, "в файле нули — содержимого нет"
    assert not speech_gate.has_sound(np.frombuffer(a.pcm16(np.full(4, 1e-6, np.float32)), "<i2"))
    hub._consume(cap, np.full(400, 2.0 / 32767, dtype=np.float32))
    assert "blackhole" in hub._content_ts
    snap = hub.health_snapshot()["channels"]["blackhole"]
    assert snap["content_age_seconds"] is not None and snap["content_age_seconds"] < 5


def test_file_sample_of_one_lsb_is_sound_for_zero_runs():
    """Пересборка читает файл как int16/32768: отсчёт ±1 — звук, серию рвёт
    (Critical обеих голов in2: повторное квантование увело бы его в ноль)."""
    sr = 1000
    x = np.zeros(20 * sr, dtype=np.float32)
    x[int(10.05 * sr)] = 1 / 32768
    assert speech_gate.zero_runs(x, sr, 5.0) == [(0.0, 10.0), (10.1, 20.0)]


def test_dead_content_during_a_call_is_one_silent_event_with_the_content_boundary(monkeypatch):
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got, said = [], []
    hub.on_channel = got.append
    hub.on_status = said.append
    now = time.time()
    _dead_call(hub, now)
    hub._check_content(now)
    hub._check_content(now + 5)                       # следующий проход — без повтора
    assert [e.kind for e in got] == [a.CH_SILENT]
    ev = got[0]
    assert ev.label == "blackhole" and ev.cause == "silent_content"
    assert abs(ev.stopped_at - (now - a.CONTENT_SILENT_S - 5)) < 1e-6, "граница — часы содержимого"
    assert len(said) == 1 and "не пишется с" in str(said[0]), "одна тихая строка, без совета перезапуска"
    assert "перезапуст" not in str(said[0])
    assert hub._scream_count == 0, "бюджет громких криков не тратится"
    hub._content_ts["blackhole"] = now + 6            # звук вернулся
    hub._check_content(now + 10)
    assert [e.kind for e in got] == [a.CH_SILENT, a.CH_AUDIBLE]


def test_no_alarm_without_a_call_or_with_a_quiet_mic_or_a_short_pause(monkeypatch):
    _quiet(monkeypatch)
    now = time.time()
    for case in ("no_call", "quiet_mic", "short"):
        hub = _hub("mic", "blackhole")
        got = []
        hub.on_channel = got.append
        _dead_call(hub, now, age=100 if case == "short" else a.CONTENT_SILENT_S + 5,
                   share=0.05 if case == "quiet_mic" else 0.5)
        if case == "no_call":
            hub._call = False
        hub._check_content(now)
        assert got == [], case


def test_a_frame_loss_wins_over_dead_content(monkeypatch):
    """Канал в реестре потерь — кадровая потеря главнее: второй тревоги о том же
    канале нет, открытый `silent` гаснет молча (DS in1 I1)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    _dead_call(hub, now)
    hub._check_content(now)
    hub._lost["blackhole"] = a.Loss("умер", retriable=False, died=True)
    hub._check_content(now + 5)
    assert "blackhole" not in hub._silent_open
    assert [e.kind for e in got] == [a.CH_SILENT]


def test_after_the_trace_is_closed_nothing_is_emitted(monkeypatch):
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    _dead_call(hub, now)
    hub.end_channel_episodes()
    hub._check_content(now)
    assert [e.kind for e in got if e.kind != a.CH_END] == []


def test_kinds_match_between_hub_and_trace():
    assert (a.CH_SILENT, a.CH_AUDIBLE) == (channel_trace.KIND_SILENT, channel_trace.KIND_AUDIBLE)


def _ev(kind, at, stopped_at):
    return {"label": "blackhole", "kind": kind, "at": at, "stopped_at": stopped_at,
            "silent_s": at - stopped_at, "died": True, "cause": "silent_content", "reason": ""}


def test_summary_marks_only_a_silence_open_at_the_end():
    t = time.time()
    open_ = [_ev("silent", t, t - 600)]
    note = channel_trace.summary_of(open_)
    assert note and note.startswith(channel_trace.SUMMARY_MARK) and "до конца записи" in note
    assert not channel_trace.lost_till_end(open_, "blackhole"), "срез хвоста №733 не задет"
    closed = open_ + [_ev("audible", t + 60, t - 600)]
    assert channel_trace.summary_of(closed) is None, "вернувшееся содержимое — канал жив"


def test_live_line_is_a_trace_line_and_does_not_eat_the_episode_budget(monkeypatch):
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    lines = []
    trace = channel_trace.ChannelTrace(pathlib.Path(tempfile.mkdtemp()) / "x_live.md",
                                       note=lines.append)
    hub.on_channel = trace.on_event
    now = time.time()
    _dead_call(hub, now)
    hub._check_content(now)
    assert len(lines) == 1 and channel_trace.is_trace_line(lines[0]), lines
    assert trace._episodes_shown["blackhole"] == 0 and "blackhole" not in trace._open_shown
