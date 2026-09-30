"""Сверка журнала тени Nemotron и прогон записи через тень (№478 B, часть 1).

Всё на синтетике: записей встреч в репозитории нет и быть не может. Журнал тени строится
здесь по правилам ребёнка (фронт отстаёт от поданного на буфер пресета, кадр 10 мс), и
сверка обязана вернуть задержку, которую мы сами туда положили. Прогон записи идёт
настоящим хабом, настоящей тенью и `live_nemotron.start()`, а поддельны только ребёнок
(дверь) и трекер (модели ONNX).
"""
from __future__ import annotations

import json
import math
import pathlib
import sys
import threading
import types
import wave

import numpy as np
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import audio  # noqa: E402
import diarize_live  # noqa: E402
import diarize_nemotron as dn  # noqa: E402
import foreign_python as fp  # noqa: E402
import live_nemotron as ln  # noqa: E402
import nemotron_shadow_check as chk  # noqa: E402
import nemotron_shadow_replay as rp  # noqa: E402

SR = 16000
HOP = 160                      # кадр модели 10 мс
STEP = dn.STREAM_STEP          # шаг ребёнка 0,5 с
LAG = 16640                    # буфер пресета low, 1,04 с
START0 = 4800                  # поток начался не на границе шага


# ------------------------------------------------------------------ синтетический журнал


def journal_lines(*, chunk_ends=(), frame_s=0.01, write_frame=HOP, total=SR * 20, segs=(),
                  exit_="ok", counts=None, finals=1):
    """Журнал тени по правилам ребёнка: после каждого шага — фронт, кадров
    `(fed − LAG) // write_frame`; финал — все кадры поданного звука."""
    out = [{"type": "header", "v": 1, "sr": SR, "channel": "blackhole", "preset": "low", "stamp": "x"},
           {"type": "ready", "t": 0.0, "proto": 1, "frame_s": frame_s, "step": STEP, "pid": 1},
           {"type": "start", "t": 0.1, "start0": START0}]
    for s, e, slot in segs:
        out.append({"type": "seg", "t": 0.2, "start": s, "end": e, "slot": slot, "open": False})
    fed = 0
    while fed + STEP <= total:
        fed += STEP
        frames = max(0, fed - LAG) // write_frame
        out.append({"type": "front", "t": fed / SR, "fed": fed, "frames": frames,
                    "front": START0 + frames * write_frame})
    for _ in range(finals):
        frames = fed // write_frame
        out.append({"type": "front", "t": fed / SR + 1, "fed": fed, "frames": frames,
                    "front": START0 + frames * write_frame, "final": True})
    for n, end in enumerate(chunk_ends):
        out.append({"type": "chunk", "t": 1.0, "chunk": n, "start": end - 3 * SR, "end": end,
                    "state": "pieces", "outcome": ln.LABELED, "wait_s": 0.4, "behind_s": 1.1, "slots": {}})
    out.append({"type": "end", "t": 99.0, "reason": "остановлен", "counts": counts or {}, "exit": exit_})
    return [json.dumps(x) for x in out]


def journal(**kw) -> chk.Journal:
    return chk.read_journal(journal_lines(**kw))


# ------------------------------------------------------------------ (а1) задержка метки


def test_label_lag_is_the_audio_fed_when_the_front_passes_the_chunk_end():
    # конец чанка = start0 + 64000; фронт ≥ конца, когда fed − 16640 ≥ 64000 → fed = 88000 (шаг 11)
    j = journal(chunk_ends=[START0 + 64000])
    lag = chk.label_lag(j)
    assert lag["_values"] == [pytest.approx((88000 - 64000) / SR)]      # 1,5 с звука
    assert lag["share"][">1s"] == 1.0 and lag["share"][">2s"] == 0.0


def test_label_lag_on_a_chunk_boundary_equal_to_the_front():
    # фронт ровно на конце чанка (fed = 72000) — метка готова на этом фронте: 1,04 с
    end = START0 + (72000 - LAG) // HOP * HOP
    lag = chk.label_lag(journal(chunk_ends=[end]))
    assert lag["_values"] == [pytest.approx(LAG / SR)]


def test_chunks_before_the_stream_and_after_the_last_front_are_counted_apart():
    j = journal(chunk_ends=[START0 - 100, START0 + SR * 20], total=SR * 20)
    lag = chk.label_lag(j)
    assert lag["before_stream"] == 1 and lag["final_only"] == 1 and lag["_values"] == []


def test_chunks_without_a_label_are_left_to_the_outcome_count():
    lines = journal_lines(chunk_ends=[START0 + 64000])
    dead = json.dumps({"type": "chunk", "t": 1.0, "chunk": 7, "start": START0 + SR * 30,
                       "end": START0 + SR * 33, "state": "pieces", "outcome": ln.DEAD_STREAM,
                       "wait_s": 0.0, "behind_s": None})
    j = chk.read_journal(lines[:-1] + [dead, lines[-1]])
    lag = chk.label_lag(j)
    assert len(lag["_values"]) == 1 and lag["final_only"] == 0
    assert chk.outcomes(j)[ln.DEAD_STREAM] == 1


def test_frame_unit_eight_times_too_long_is_refused_not_counted():
    """Опровергающий опыт части 1: единица кадра 80 мс вместо 10 — фронт уходит вперёд
    поданного звука, сверка отказывает той же проверкой, что у ребёнка."""
    j = journal(chunk_ends=[START0 + 64000], frame_s=0.08)
    assert any("кадр не сходится" in p for p in chk.validity(j))
    with pytest.raises(chk.Refused):
        chk.check_frames(j)


def test_journal_written_with_the_long_frame_but_read_as_short_is_refused():
    """Обратная подмена: ребёнок считал кадры по 80 мс, в рукопожатии — 10 мс; финал
    не покрывает поданный звук."""
    j = journal(chunk_ends=[START0 + 64000], write_frame=1280, frame_s=0.01)
    with pytest.raises(chk.Refused, match="кадр не сходится"):
        chk.check_frames(j)


# ------------------------------------------------------------------ годность


def test_a_clean_journal_is_valid():
    j = journal(chunk_ends=[START0 + 64000])
    assert chk.validity(j, fed_expected=SR * 20) == []


@pytest.mark.parametrize("kw, word", [
    ({"exit_": "failed"}, "вышел не сам"),
    ({"finals": 0}, "финальных фронтов 0"),
    ({"finals": 2}, "финальных фронтов 2"),
    ({"counts": {"killed_after_grace": 1}}, "счётчики"),
    ({"counts": {"fault_чанк": 2}}, "счётчики"),
])
def test_a_broken_run_is_invalid(kw, word):
    j = journal(chunk_ends=[START0 + 64000], **kw)
    assert any(word in p for p in chk.validity(j)), chk.validity(j)


def test_an_early_stop_that_did_not_cover_the_fed_audio_is_invalid():
    j = journal(chunk_ends=[START0 + 64000], total=SR * 10)
    assert any("покрыл" in p for p in chk.validity(j, fed_expected=SR * 20))


def test_no_labelled_chunk_is_invalid():
    assert any("ни одного" in p for p in chk.validity(journal()))


def test_two_runs_in_one_file_and_an_unfinished_journal_are_refused():
    lines = journal_lines(chunk_ends=[START0 + 64000])
    with pytest.raises(chk.Refused, match="два заголовка"):
        chk.read_journal(lines + lines)
    with pytest.raises(chk.Refused, match="нет строк: end"):
        chk.read_journal(lines[:-1])


def test_chunk_lines_after_end_do_not_make_the_journal_unfinished():
    """Тень пишет строку чанку, принятому после конца потока, и после `end`."""
    lines = journal_lines(chunk_ends=[START0 + 64000])
    late = json.dumps({"type": "chunk", "t": 100.0, "chunk": 9, "start": 0, "end": 1, "state": "pieces",
                       "outcome": ln.DEAD_STREAM, "wait_s": 0.0, "behind_s": None})
    assert chk.read_journal(lines + [late]).chunks[-1]["outcome"] == ln.DEAD_STREAM


# ------------------------------------------------------------------ (а2) стоимость шага


def test_step_cost_starts_when_both_the_audio_and_the_previous_front_are_there():
    rows = []
    t = 0.0
    for k in range(1, 12):
        rows.append({"k": "write", "t": t, "sent": k * STEP})          # звук шага ушёл в трубу
        rows.append({"k": "front", "t": t + 0.2, "fed": k * STEP})     # ребёнок думал 0,2 с
        t += 0.5
    cost = chk.step_cost(rows, STEP, SR)
    assert cost["step_cost_s"]["n"] == 11 - chk.WARMUP_FRONTS
    assert cost["step_cost_s"]["p50"] == pytest.approx(0.2)
    assert cost["rtf_p50"] == pytest.approx(2.5)


def test_step_cost_counts_from_the_previous_front_when_the_child_was_behind():
    # весь звук ушёл сразу, ребёнок разбирает шаги подряд по 0,3 с
    rows = [{"k": "write", "t": 0.0, "sent": 20 * STEP}]
    rows += [{"k": "front", "t": 0.3 * k, "fed": k * STEP} for k in range(1, 21)]
    assert chk.step_cost(rows, STEP, SR)["step_cost_s"]["max"] == pytest.approx(0.3)


def test_a_front_before_its_audio_was_written_is_refused():
    with pytest.raises(chk.Refused):
        chk.step_cost([{"k": "front", "t": 1.0, "fed": STEP}], STEP, SR)


# ------------------------------------------------------------------ (б) матрица


def test_overlap_matrix_counts_seconds_and_speech_without_the_other_side():
    a = [(0.0, 10.0, "slot0"), (10.0, 20.0, "slot1")]
    b = [(0.0, 8.0, "A"), (8.0, 20.0, "B"), (25.0, 30.0, "C")]
    m = chk.overlap_matrix(a, b)
    assert m[("slot0", "A")] == pytest.approx(8.0)
    assert m[("slot0", "B")] == pytest.approx(2.0)
    assert m[("slot1", "B")] == pytest.approx(10.0)
    assert m[(chk.NONE, "C")] == pytest.approx(5.0)
    assert chk.purity(m) == {"slot0": 0.8, "slot1": 1.0}
    assert chk.coverage(m, min_s=1.0) == {"A": 1.0, "B": pytest.approx(10 / 12, abs=1e-4), "C": 0.0}


def test_overlap_matrix_window_clips_intervals():
    m = chk.overlap_matrix([(0.0, 10.0, "s")], [(0.0, 10.0, "A")], lo=4.0, hi=6.0)
    assert m == {("s", "A"): pytest.approx(2.0)}


def test_overlapping_labels_on_one_side_split_the_seconds_not_double_them():
    m = chk.overlap_matrix([(0.0, 4.0, "s")], [(0.0, 4.0, "A"), (0.0, 4.0, "B")])
    assert sum(m.values()) == pytest.approx(4.0)


def test_prefix_mapping_is_an_oracle_over_past_windows():
    # слот 0 — голос A три окна, потом B: окна 1 и 2 угаданы, окно 3 — нет
    w = 10.0
    a = [(0.0, 4 * w, "slot0")]
    b = [(0.0, 3 * w, "A"), (3 * w, 4 * w, "B")]
    got = chk.prefix_mapping(a, b, total=4 * w, window=w)
    assert got["oracle_upper_bound"] == pytest.approx(2 / 3, abs=1e-4)


def test_fragmentation_counts_slots_holding_a_real_share_of_a_voice():
    m = chk.overlap_matrix([(0.0, 50.0, "s0"), (50.0, 100.0, "s1"), (100.0, 101.0, "s2")],
                           [(0.0, 101.0, "A")])
    assert chk.fragmentation(m) == {"A": 2}


# ------------------------------------------------------------------ (в) трекер


def test_tracker_intervals_give_the_overlap_to_the_later_chunk_and_skip_channel_labels():
    lines = [{"start": 0, "intervals": [[0, 3 * SR, 1]]},
             {"start": int(2.5 * SR), "intervals": [[int(2.5 * SR), int(5.5 * SR), 2]]},
             {"start": 5 * SR, "intervals": [[5 * SR, 8 * SR, None]]}]
    got = chk.tracker_intervals(lines, SR)
    assert got == [(0.0, 2.5, "v1"), (2.5, 5.0, "v2")]


def test_report_runs_end_to_end_on_a_synthetic_journal():
    segs = [(START0, START0 + 5 * SR, 0), (START0 + 5 * SR, START0 + 10 * SR, 1)]
    j = journal(chunk_ends=[START0 + 64000], segs=segs)
    final = {"duration_s": 20.0, "segments": [[START0 / SR, (START0 + 5 * SR) / SR, "Собеседник 1"],
                                              [(START0 + 5 * SR) / SR, (START0 + 10 * SR) / SR, "Собеседник 2"]]}
    out = chk.report(j, final)
    assert out["b_slots"]["purity"] == {"slot0": 1.0, "slot1": 1.0}
    assert out["c_agreement_with_final"]["stream"]["der"] == pytest.approx(0.0, abs=0.01)


def test_report_refuses_an_invalid_journal():
    with pytest.raises(chk.Refused):
        chk.report(journal(chunk_ends=[START0 + 64000], exit_="failed"),
                   {"duration_s": 20.0, "segments": [[0, 1, "a"]]})


# ------------------------------------------------------------------ прогон: части


def _hub(chunk_s=3.0, overlap_s=0.5):
    cfg = {"audio": {"samplerate": SR, "chunk_seconds": chunk_s, "overlap_seconds": overlap_s,
                     "vad_energy_db": -200.0, "record": False, "device": "auto"},
           "log": {"recordings_dir": "recordings"}, "sufler": {"user_name": "Владелец"}}
    hub = audio.AudioHub(cfg, captures=[])
    hub._register_captures([types.SimpleNamespace(label=x) for x in rp.LABELS])
    return hub


@pytest.mark.parametrize("seconds", [2.9, 3.0, 5.4, 5.5, 5.6, 61.3])
def test_expected_cuts_is_what_the_real_hub_cuts(seconds):
    hub = _hub()
    n = int(SR * seconds)
    sound = np.full(n, 0.1, dtype=np.float32)
    caps = [types.SimpleNamespace(label=x) for x in rp.LABELS]
    for lo in range(0, n, 1600):
        for c in caps:
            hub._consume(c, sound[lo:lo + 1600])
        hub.pull_placed()
    cuts = {x: hub.chunk_no.get(x, -1) + 1 for x in rp.LABELS}
    assert cuts == {x: rp.expected_cuts(n, SR, 3.0, 0.5) for x in rp.LABELS}


def test_pad_equal_pads_the_shorter_channel_with_silence():
    a, b = rp.pad_equal(np.ones(5, dtype=np.float32), np.ones(3, dtype=np.float32))
    assert len(a) == len(b) == 5 and list(b[3:]) == [0.0, 0.0]


def test_the_witness_proxy_passes_write_errors_through_and_books_only_successes(tmp_path):
    w = rp.Witness(tmp_path / "t.jsonl")

    class Dead:
        pid = 7

        def write(self, data):
            raise BrokenPipeError("убит")

    proxy = rp._StreamProxy(Dead(), w)
    with pytest.raises(BrokenPipeError):
        proxy.write(b"\0\0" * 10)
    assert w.sent == 0 and proxy.pid == 7
    w.close()
    assert (tmp_path / "t.jsonl").read_text() == ""


def test_witness_spawn_shows_the_front_to_the_witness_before_the_shadow(tmp_path):
    w = rp.Witness(tmp_path / "t.jsonl")
    seen = []

    def door(python, script, args, *, on_message, **kw):
        on_message({"type": "front", "fed": 8000})
        return object(), fp.Outcome(fp.OK)

    stream, _ = rp.witness_spawn(w, door)("py", pathlib.Path("s"), [], on_message=lambda m: seen.append(w.fed))
    assert seen == [8000] and isinstance(stream, rp._StreamProxy)


class _Tracker:
    """Трекер с заданным ответом `split`."""

    def __init__(self, result=None, raises=False):
        self.result, self.raises = result, raises

    def split(self, chunk, channel="_default"):
        if self.raises:
            raise RuntimeError("упал")
        return self.result


def _decide(tracker, start=1000):
    placed = audio.Placed("Собеседник", np.zeros(3 * SR, dtype=np.float32), start, ("blackhole", 0))
    return rp.chunk_decision(tracker, placed, stt_runtime=__import__("stt_runtime"),
                             jobs_for=diarize_live.jobs_for, diarized_state=ln.diarized_state)


def test_chunk_decision_names_every_tracker_path_on_the_hub_axis():
    piece = diarize_live.Piece(start=0, end=SR, voice=2, raw_start=100, raw_end=900)
    assert _decide(_Tracker(diarize_live.SplitResult([piece], 2))) == ("pieces", "pieces", [(1100, 1900, 2)])
    assert _decide(_Tracker(diarize_live.SplitResult([], 1))) == ("none", "excluded", [])
    assert _decide(_Tracker(diarize_live.SplitResult(None, 3))) == ("pieces", "whole", [(1000, 1000 + 3 * SR, 3)])
    assert _decide(_Tracker(raises=True)) == ("split_failed", "split_failed", [])


def test_run_root_refuses_an_existing_output(tmp_path):
    (tmp_path / "out").mkdir()
    with pytest.raises(rp.Refused, match="уже есть"):
        rp.run_root(tmp_path / "out", tmp_path)


# ------------------------------------------------------------------ прогон целиком


class _Child:
    """Ребёнок-заглушка: копит звук шагами, после каждого шага — сегмент и фронт с
    буфером пресета; на закрытии входа — финальный фронт и конец вывода."""

    def __init__(self, on_message, on_eof):
        self.on_message, self.on_eof = on_message, on_eof
        self.pid, self.nonjson, self.callback_errors = 4242, 0, 0
        self.buf = self.fed = 0
        self._alive = True

    def write(self, data):
        self.buf += len(data) // 2
        while self.buf >= STEP:
            self.buf -= STEP
            self.fed += STEP
            frames = max(0, self.fed - LAG) // HOP
            self.on_message({"type": "seg", "start": (self.fed - STEP) / SR, "end": self.fed / SR,
                             "slot": 0, "open": True})
            self.on_message({"type": "front", "fed": self.fed, "frames": frames})

    def close_input(self):
        if not self._alive:
            return
        self._alive = False
        self.fed += self.buf
        self.buf = 0
        self.on_message({"type": "front", "fed": self.fed, "frames": self.fed // HOP, "final": True})
        threading.Thread(target=self.on_eof, daemon=True).start()

    def alive(self):
        return self._alive

    def kill(self):
        self._alive = False

    kill_nowait = kill

    def finish(self, timeout):
        return fp.Outcome(fp.OK)


def _wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((samples * 32767).astype("<i2").tobytes())


def test_replay_drives_the_real_hub_and_shadow_and_the_check_accepts_it(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "config.yaml").write_text(
        "audio: {samplerate: 16000, chunk_seconds: 3.0, overlap_seconds: 0.5, vad_energy_db: -200,"
        " record: false, device: auto}\nsufler: {user_name: Владелец, nemotron_python: ''}\n", encoding="utf-8")
    (data / "recordings").mkdir()
    stamp = "2026-01-01_100000"
    rng = np.random.default_rng(3)
    _wav(data / "recordings" / f"{stamp}_blackhole.wav", rng.uniform(-0.3, 0.3, SR * 20).astype(np.float32))
    _wav(data / "recordings" / f"{stamp}_mic.wav", rng.uniform(-0.3, 0.3, SR * 19).astype(np.float32))
    (data / "models" / "diar" / "nemotron").mkdir(parents=True)
    for name in ("segmentation.onnx", "embedding.onnx"):
        (data / "models" / "diar" / name).write_bytes(b"")
    py = data / "engines" / "nemotron" / "python" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text("")
    monkeypatch.setattr(diarize_live, "SegmentTracker",
                        lambda *a, **k: _Tracker(diarize_live.SplitResult(None, 1)))
    ready = {"type": "ready", "proto": dn.STREAM_PROTO, "sr": SR, "preset": "low",
             "frame_s": HOP / SR, "step": STEP}

    def door(python, script, args, *, on_message, on_eof, **kw):
        assert "--stream" in args and str(data / "models") not in args[args.index("--model") + 1]
        return _Child(on_message, on_eof), fp.Outcome(fp.OK, payload=ready)

    monkeypatch.setattr(fp, "spawn_stream", door)
    out = tmp_path / "out"
    meta = rp.replay(stamp, data_root=data, out=out, memory=lambda: None, say=lambda s: None)
    assert meta["cuts_per_channel"] == rp.expected_cuts(SR * 20, SR, 3.0, 0.5)
    assert (out / "models").is_symlink() and not (data / "logs").exists()

    j = chk.read_journal((out / meta["journal"]).read_text(encoding="utf-8").splitlines())
    assert chk.validity(j, fed_expected=meta["fed_to_shadow"]) == []
    assert j.start0 == int(SR * rp.PREROLL_S)
    lag = chk.label_lag(j)
    assert lag["before_stream"] >= 1 and lag["_values"]
    # фронт отстаёт от поданного на буфер 1,04 с, чанк ждёт до шага сверху
    assert all(LAG / SR <= x <= (LAG + STEP) / SR + 1e-9 for x in lag["_values"])
    tracker = [json.loads(x) for x in (out / "tracker.jsonl").read_text().splitlines()]
    assert {t["path"] for t in tracker} == {"whole"}
    timing = [json.loads(x) for x in (out / "timing.jsonl").read_text().splitlines()]
    assert {r["k"] for r in timing} == {"write", "front"}
    final = {"duration_s": 20.0, "segments": [[0.0, 20.0, "Собеседник 1"]]}
    report = chk.report(j, final, tracker=tracker, timing=timing, meta=meta)
    assert report["b_slots"]["purity"]["slot0"] == 1.0
    assert not math.isnan(report["c_agreement_with_final"]["tracker"]["der"])


# ------------------------------------------------------------------ память


def _with_memory(lines, *, exit_="ok"):
    """Журнал с ценой ребёнка во фронтах, лимитом кэша в рукопожатии и строками `mem`
    (одна — после `end`: так пишет нить читателя)."""
    out = []
    for raw in lines:
        obj = json.loads(raw)
        if obj["type"] == "ready":
            obj.update(cache_limit_mb=512, cache_limit_prev_mb=62259)
        if obj["type"] == "front":
            obj.update(phys_mb=2000 + obj["fed"] // STEP, rss_mb=600, mlx_active_mb=700, mlx_cache_mb=300,
                       mlx_peak_mb=1200)
        if obj["type"] == "end":
            obj["exit"] = exit_
            out.append(json.dumps({"type": "mem", "t": 5.0, "pressure": 1, "swap_used_mb": 2800}))
            out.append(json.dumps({"type": "mem", "t": 10.0, "pressure": 2, "swap_used_mb": 3100}))
        out.append(json.dumps(obj))
    out.append(json.dumps({"type": "mem", "t": 99.5, "pressure": 2, "swap_used_mb": 3000}))
    return out


def test_memory_reads_the_current_footprint_mlx_counters_limit_and_pressure():
    j = chk.read_journal(_with_memory(journal_lines(chunk_ends=[START0 + 64000])))
    mem = chk.memory(j)
    assert mem["phys_mb"]["max"] == 2000 + 20 * SR // STEP and mem["mlx_cache_mb"]["p50"] == 300
    assert mem["rss_peak_mb"] == 600 and mem["cache_limit_mb"] == 512 and mem["cache_limit_prev_mb"] == 62259
    assert mem["pressure_checks"] == {1: 1, 2: 2}
    assert mem["swap_used_mb"] == {"first": 2800.0, "max": 3100.0}
    assert "memory" in chk.report(j, {"duration_s": 20.0, "segments": [[0, 1, "a"]]})


def test_memory_is_reported_even_when_the_journal_is_refused(tmp_path, capsys):
    """Тень, умершая от давления, — предмет замера памяти: отказ сверки её не прячет."""
    path = tmp_path / "j.jsonl"
    path.write_text("\n".join(_with_memory(journal_lines(chunk_ends=[START0 + 64000]), exit_="failed")),
                    encoding="utf-8")
    final = tmp_path / "final.json"
    final.write_text(json.dumps({"duration_s": 20.0, "segments": [[0, 1, "a"]]}), encoding="utf-8")
    assert chk.main([str(path), "--final", str(final)]) == 2
    got = json.loads(capsys.readouterr().out)
    assert "вышел не сам" in got["refused"] and got["memory"]["pressure_checks"] == {"1": 1, "2": 2}


def test_the_witness_door_adds_the_cache_limit_to_the_child_arguments(tmp_path):
    w = rp.Witness(tmp_path / "t.jsonl")
    seen = []

    def door(python, script, args, *, on_message, **kw):
        seen.append(list(args))
        return None, fp.Outcome(fp.FAILED, reason="нет")

    rp.witness_spawn(w, door, ["--cache-limit-mb", "512"])("py", pathlib.Path("s"), ["--stream"],
                                                           on_message=lambda m: None)
    assert seen == [["--stream", "--cache-limit-mb", "512"]]
