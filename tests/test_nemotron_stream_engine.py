"""Сторона движка живого потока Nemotron (№478, PR A2) без mlx: обёртка модели, длина
кадра, цикл по трубе на границах блока, цена процесса, рукопожатие и отказ.

Сквозной гейт «хаб → ребёнок → журнал» (`test_live_nemotron.py`) гонит цикл целиком, но
на случайных кусках: границы — блок, пришедший ровно целым, один сэмпл и полбайта на
EOF, «открыт» сегмента до и после `close` — держат тесты здесь (мутатор диапазона).
"""
import io
import json
import pathlib
import sys
import types

import numpy as np
import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import diarize_nemotron as dn  # noqa: E402
from exit_codes import EXIT_ENGINE_UNAVAILABLE  # noqa: E402

SR = dn.SAMPLE_RATE
FRAME = 1280                      # 0,08 с: hop 160 × subsampling 8 при 16 кГц


class _Model:
    """Поддельная модель mlx-audio: кадры отдаёт с задержкой в один кадр, на финале —
    всё; сегмент — от прошлого фронта до нового, слот 1."""

    def __init__(self):
        self.calls = []
        self._processor_config = types.SimpleNamespace(hop_length=160, sampling_rate=SR)
        self.config = types.SimpleNamespace(
            fc_encoder_config=types.SimpleNamespace(subsampling_factor=8))

    def init_streaming_state(self):
        return types.SimpleNamespace(frames_processed=0, fed=0)

    def feed(self, pcm, state, sr, *, threshold, final=False):
        self.calls.append((len(pcm), sr, threshold, final))
        fed = state.fed + len(pcm)
        frames = fed // FRAME if final else max(0, (fed - FRAME) // FRAME)
        segments = []
        if frames > state.frames_processed:
            segments.append(types.SimpleNamespace(start=state.frames_processed * 0.08,
                                                  end=frames * 0.08, speaker=1))
        return (types.SimpleNamespace(segments=segments),
                types.SimpleNamespace(frames_processed=frames, fed=fed))


def test_the_stream_wrapper_feeds_mono_and_reports_the_frames_of_the_model_state():
    model = _Model()
    stream = dn.NemotronStream(model, threshold=0.4)
    assert stream.frames_processed == 0
    assert stream.feed(np.zeros(SR, dtype=np.float32)) == [{"start": 0.0, "end": 0.88, "speaker": "nem1"}]
    assert stream.frames_processed == 11 and type(stream.frames_processed) is int
    assert stream.close() == [{"start": 0.88, "end": 0.96, "speaker": "nem1"}]
    assert model.calls == [(SR, SR, 0.4, False), (0, SR, 0.4, True)]
    assert stream.frames_processed == 12
    with pytest.raises(ValueError, match="моно"):
        stream.feed(np.zeros((2, 10), dtype=np.float32))


def test_the_frame_lasts_hop_times_subsampling_over_the_rate():
    assert dn.frame_seconds(_Model()) == pytest.approx(0.08)


class _Stream:
    """Поток без модели: сколько сэмплов в каждом `feed`, кадр на каждые 1280, сегмент
    от прошлого фронта до нового; `close` дописывает ещё кадр."""

    def __init__(self):
        self.fed = []
        self.frames_processed = 0

    def feed(self, pcm):
        self.fed.append(len(pcm))
        before = self.frames_processed
        self.frames_processed = sum(self.fed) // FRAME
        return [{"start": before * 0.08, "end": self.frames_processed * 0.08, "speaker": "nem2"}]

    def close(self):
        before = self.frames_processed
        self.frames_processed += 1
        return [{"start": before * 0.08, "end": self.frames_processed * 0.08, "speaker": "nem3"}]


def _run(reads, step):
    stream, out = _Stream(), []
    at_read = []
    it = iter(reads)

    def read(n):
        at_read.append(len(stream.fed))
        return next(it)
    assert dn.run_stream(stream, frame_s=0.08, read=read, emit=out.append, step=step) == 0
    return stream, out, at_read


def test_a_whole_block_is_fed_on_the_read_that_completes_it():
    """Блок ровно целиком — модели сразу, а не следующим чтением: фронт отставал бы на блок."""
    step = 2 * FRAME
    stream, _out, at_read = _run([b"\x01\x00" * step, b"\x01\x00" * step, b""], step)
    assert stream.fed == [step, step]
    assert at_read == [0, 1, 2], "к следующему чтению прошлый блок уже у модели"


@pytest.mark.parametrize("tail, want", [
    (b"\x01\x00", 1),                  # ровно один сэмпл на EOF — не теряется
    (b"\x01\x00\x07", 1),              # сэмпл и полбайта — полбайта отброшены, не падение
])
def test_at_eof_the_last_whole_sample_is_fed(tail, want):
    step = 2 * FRAME
    stream, out, _ = _run([b"\x01\x00" * step + tail, b""], step)
    assert stream.fed == [step, want]
    assert out[-1] == {**out[-1], "type": "front", "final": True, "fed": step + want}


def test_a_segment_is_open_while_streaming_and_closed_by_the_final():
    """До `close` сегмент, упёршийся во фронт, — «открыт»: речь может продолжиться; после —
    закрыто всё."""
    step = 2 * FRAME
    _stream, out, _ = _run([b"\x01\x00" * (2 * step), b""], step)
    segs = [m for m in out if m["type"] == "seg"]
    streaming = [m for m in segs if m["slot"] == 2]
    final = [m for m in segs if m["slot"] == 3]
    assert streaming and all(m["open"] for m in streaming)
    assert final and not any(m["open"] for m in final)


def test_the_price_of_the_process_is_cpu_seconds_and_peak_megabytes(monkeypatch):
    import resource
    import time
    monkeypatch.setattr(resource, "getrusage", lambda who: types.SimpleNamespace(ru_maxrss=300 * 2**20 + 7))
    monkeypatch.setattr(time, "process_time", lambda: 12.3456)
    assert dn._load() == {"cpu_s": 12.35, "rss_mb": 300}


def test_an_unavailable_model_is_the_engine_code_and_no_handshake(monkeypatch, capsys):
    proto = io.StringIO()
    monkeypatch.setattr(dn, "_protocol_channel", lambda: proto)

    def unavailable(path, preset):
        raise dn.ModelUnavailable("нет весов\nв каталоге")
    monkeypatch.setattr(dn, "load_model", unavailable)
    assert dn.serve_stream(pathlib.Path("/нет"), "low", read=lambda n: b"") == EXIT_ENGINE_UNAVAILABLE
    assert proto.getvalue() == "", "без модели рукопожатия нет"
    assert capsys.readouterr().err == "нет весов; в каталоге\n"


def test_the_engine_shakes_hands_and_runs_the_stream_to_eof(monkeypatch):
    proto = io.StringIO()
    monkeypatch.setattr(dn, "_protocol_channel", lambda: proto)
    monkeypatch.setattr(dn, "load_model", lambda path, preset: _Model())
    reads = iter([b"\x01\x00" * SR, b""])
    assert dn.serve_stream(pathlib.Path("/m"), "very_low", read=lambda n: next(reads)) == 0
    lines = [json.loads(x) for x in proto.getvalue().splitlines()]
    assert lines[0] == {"type": "ready", "proto": dn.STREAM_PROTO, "sr": SR, "preset": "very_low",
                        "frame_s": 0.08, "step": dn.STREAM_STEP}
    assert lines[-1]["type"] == "front" and lines[-1]["final"] is True and lines[-1]["fed"] == SR
