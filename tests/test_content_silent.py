"""Канал собеседников мёртв посреди звонка (№654): кадры идут, в них нули, а
собеседники звучат через динамики. Сторож кадров молчит; признак — часы
содержимого (те же байты, что в `.pcm`), звонок и речь в микрофоне за окно.

Опыт 30.10 по 9 записям 28–29.10: законные серии нулей в звонке — до 394 с,
отказ — 5015 с (29.10; отказ 05.10 на 818 с в эти 9 записей не входит); хвост после конца
звонка гасит доля микрофона (там 0–6 %, на отказе 28 %).
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


def test_a_silence_is_not_an_incomplete_recording():
    """Серия `silent` в итог «запись неполная» не входит: звонок мог кончиться, а
    очная часть встречи — идти; сигнала конца звонка у хаба нет (Critical Haiku out3).
    Срез хвоста №733 серия тоже не задевает."""
    t = time.time()
    open_ = [_ev("silent", t, t - 600)]
    assert channel_trace.summary_of(open_) is None
    assert not channel_trace.lost_till_end(open_, "blackhole"), "срез хвоста №733 не задет"
    assert channel_trace.summary_of(open_ + [_ev("audible", t + 60, t - 600)]) is None


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


def test_a_frame_loss_takes_the_silence_over(monkeypatch):
    """Кадровая потеря забирает серию себе: защёлка снимается молча, без `audible`;
    итог говорит о потере, не о тишине содержимого (DS C1 = Haiku C1 out1)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    trace = channel_trace.ChannelTrace(pathlib.Path(tempfile.mkdtemp()) / "x_live.md")
    hub.on_channel = trace.on_event
    now = time.time()
    _dead_call(hub, now)
    hub._check_content(now)
    hub._real_frame["blackhole"] = now - 40
    hub._announce_losses({"blackhole": a.Loss("умер", retriable=False, died=True)})
    hub._check_content(now + 5)
    hub._last_frame["blackhole"] = hub._real_frame["blackhole"] = now + 6
    hub._announce_back("blackhole", 6.0)
    hub._content_ts["blackhole"] = now + 7
    hub._check_content(now + 10)
    assert "blackhole" not in hub._silent_open
    assert [e["kind"] for e in trace.events] == ["silent", "lost", "back"]
    note = channel_trace.summary_of(trace.events) or ""
    assert "не пишется" not in note and "не писался" not in note, note


def test_after_a_frame_loss_the_content_age_counts_again(monkeypatch):
    """Старые часы содержимого после возврата канала не дают `silent` сразу (Sonnet I1 out1)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    _dead_call(hub, now)
    hub._lost["blackhole"] = a.Loss("умер", retriable=False, died=True)
    hub._check_content(now)
    hub._lost.clear()
    hub._check_content(now + 5)
    assert got == [], "отсчёт 600 с — от выхода из потери"
    hub._mic_speech.extend(now + a.CONTENT_SILENT_S - i for i in range(60))
    hub._check_content(now + a.CONTENT_SILENT_S + 1)
    assert [e.kind for e in got] == [a.CH_SILENT]


def test_content_back_before_the_stop_closes_the_silence(monkeypatch):
    """Звук вернулся после последнего прохода сторожа — стоп закрывает серию (DS I4 out1)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    _dead_call(hub, now)
    hub._check_content(now)
    hub._content_ts["blackhole"] = now + 1
    hub.end_channel_episodes()
    assert [e.kind for e in got] == [a.CH_SILENT, a.CH_AUDIBLE]
    assert channel_trace.summary_of([e.as_dict() for e in got]) is None


def test_a_failing_sweep_still_checks_the_content(monkeypatch):
    """Упавший проход сторожа не снимает проверку содержимого (Sonnet I2 = DS I2 out1)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    _dead_call(hub, now)

    def boom(*args):
        raise RuntimeError("проход упал")
    monkeypatch.setattr(hub, "_sweep", boom)
    hub._last_check = 0.0
    try:
        hub._watch_streams()
    except RuntimeError:
        pass
    assert [e.kind for e in got] == [a.CH_SILENT]


def test_mic_speech_moments_come_from_the_hub_cut(monkeypatch):
    """Доля микрофона — из того же среза и гейта, что `pull_placed` (DS C3 in2):
    речевой срез микрофона оставляет момент, тихий — нет."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    n = int(hub.sr * hub.chunk_s)
    hub._consume(hub.captures[0], np.zeros(n, dtype=np.float32))
    hub.pull_placed()
    assert len(hub._mic_speech) == 0
    t = np.arange(n) / hub.sr
    hub._consume(hub.captures[0], (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
    hub.pull_placed()
    assert len(hub._mic_speech) == 1


def test_watchdog_closing_the_silence_during_the_stop_is_one_audible(monkeypatch):
    """Сторож закрыл серию между снимком защёлок и снятием в стопе: стоп не падает,
    `audible` один, след закрыт (Important Sonnet = Haiku out2)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    _dead_call(hub, now)
    hub._check_content(now)
    hub._content_ts["blackhole"] = now + 1

    class Racy(dict):
        def __iter__(self):
            keys = list(super().__iter__())
            hub._check_content(now + 2)          # сторож выигрывает гонку
            return iter(keys)

    hub._silent_open = Racy(hub._silent_open)
    hub.end_channel_episodes()
    assert [e.kind for e in got] == [a.CH_SILENT, a.CH_AUDIBLE]
    assert hub._channel_closed


def test_a_quiet_restart_does_not_close_a_dead_content_series(monkeypatch):
    """Тихий перезапуск (`gap`) мимо реестра потерь: защёлка хаба стоит, содержимое
    нулевое — второй `silent` о той же серии не выходит (Important Sonnet = Haiku out2)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got = []
    hub.on_channel = got.append
    now = time.time()
    _dead_call(hub, now)
    hub._check_content(now)
    hub._channel_event(a.CH_GAP, "blackhole", now + 5, now - 40, 45.0,
                       a.Loss("перезапуск", retriable=True, died=True, cause="restarted"))
    hub._check_content(now + 700)
    assert [e.kind for e in got] == [a.CH_SILENT, a.CH_GAP], "серия одна, повтора нет"
    assert "blackhole" in hub._silent_open, "защёлка стоит: содержимое так и нулевое"


def test_no_status_line_when_the_trace_closes_mid_pass(monkeypatch):
    """След закрыт между проверкой флага и событием — строка статуса не уходит (Minor Haiku out2)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    said = []
    hub.on_status = said.append
    now = time.time()
    _dead_call(hub, now)
    monkeypatch.setattr(hub, "_channel_event", lambda *args, **kw: setattr(hub, "_channel_closed", True))
    hub._check_content(now)
    assert not any("не пишется" in s for s in said), said


_NOW = 1_000_000.0          # точная арифметика границ: разность ровно 600.0


def _wired(hub):
    """События и потери, поданные в `_channel_event`: поля `Loss` в событие
    не все доходят, контракт — значение, которое хаб подаёт."""
    got, losses = [], []
    real = hub._channel_event

    def spy(kind, label, at, stopped_at, silent_s, loss):
        losses.append(loss)
        return real(kind, label, at, stopped_at, silent_s, loss)

    hub.on_channel = got.append
    hub._channel_event = spy
    return got, losses


def test_without_the_call_fact_dead_content_is_silence_of_a_room(monkeypatch):
    """Свежий хаб звонка не знает: без `set_call` нули канала — законная тишина
    очной встречи, тревоги нет (мутант `_call = True`)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got, _ = _wired(hub)
    hub._content_ts["blackhole"] = _NOW - 2 * a.CONTENT_SILENT_S
    hub._mic_speech.extend(_NOW - i for i in range(200))
    hub._check_content(_NOW)
    assert got == []


def test_content_age_exactly_the_threshold_is_an_alarm(monkeypatch):
    """Ровно 600 с нулей — уже тревога: граница включительная (мутант `<` → `<=`)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got, _ = _wired(hub)
    _dead_call(hub, _NOW, age=a.CONTENT_SILENT_S)
    hub._check_content(_NOW)
    assert [e.kind for e in got] == [a.CH_SILENT]


def test_mic_share_exactly_the_threshold_is_an_alarm(monkeypatch):
    """Доля микрофона ровно 20 % — звонок жив (мутант `<` → `<=`)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got, _ = _wired(hub)
    hub.set_call()
    hub._content_ts["blackhole"] = _NOW - 700
    step = speech_gate.step_seconds(hub.chunk_s, hub.overlap_s)
    n = round(a.CONTENT_MIC_SHARE * a.CONTENT_SILENT_S / step)
    hub._mic_speech.extend(_NOW - 1 - i for i in range(n))
    assert n * step / a.CONTENT_SILENT_S == a.CONTENT_MIC_SHARE
    hub._check_content(_NOW)
    assert [e.kind for e in got] == [a.CH_SILENT]


def test_mic_moment_on_the_window_edge_is_inside(monkeypatch):
    """Кусок микрофона ровно на левом краю окна в окне: без него доля ниже
    порога (мутант обрезки `<` → `<=`)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got, _ = _wired(hub)
    hub.set_call()
    hub._content_ts["blackhole"] = _NOW - 700
    step = speech_gate.step_seconds(hub.chunk_s, hub.overlap_s)
    n = round(a.CONTENT_MIC_SHARE * a.CONTENT_SILENT_S / step)
    hub._mic_speech.append(_NOW - a.CONTENT_SILENT_S)
    hub._mic_speech.extend(_NOW - 1 - i for i in range(n - 1))
    hub._check_content(_NOW)
    assert [e.kind for e in got] == [a.CH_SILENT]


def test_silent_and_audible_carry_the_content_duration_and_a_final_loss(monkeypatch):
    """`silent` — от границы содержимого до прохода, `audible` — от границы до
    последнего звука, который видит проход сторожа (не до первого звука возврата);
    обе потери окончательные: сторож канал не перезапускает, канал жил
    (мутанты длительности и флагов `Loss`)."""
    _quiet(monkeypatch)
    hub = _hub("mic", "blackhole")
    got, losses = _wired(hub)
    _dead_call(hub, _NOW, age=700)
    hub._check_content(_NOW)
    hub._content_ts["blackhole"] = _NOW + 50
    hub._check_content(_NOW + 55)
    assert [(e.kind, e.stopped_at, e.silent_s, e.died) for e in got] == [
        (a.CH_SILENT, _NOW - 700, 700.0, True),
        (a.CH_AUDIBLE, _NOW - 700, 750.0, True)]
    assert [(x.retriable, x.died) for x in losses] == [(False, True), (False, True)]
