"""Сторона движка живого потока Nemotron (№478, PR A2) без mlx: обёртка модели, длина
кадра, цикл по трубе на границах блока, цена процесса, рукопожатие и отказ.

Сквозной гейт «хаб → ребёнок → журнал» (`test_live_nemotron.py`) гонит цикл целиком, но
на случайных кусках: границы — блок, пришедший ровно целым, один сэмпл и полбайта на
EOF, «открыт» сегмента до и после `close` — держат тесты здесь (мутатор диапазона).
"""
import io
import json
import pathlib
import subprocess
import sys
import types

import numpy as np
import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import diarize_nemotron as dn  # noqa: E402
from exit_codes import EXIT_ENGINE_UNAVAILABLE  # noqa: E402

SR = dn.SAMPLE_RATE
HOP = 160                         # родной кадр спектра, 0,01 с: единица frames_processed модели
PACK = 8 * HOP                    # пачка энкодера: вход прорежен в 8 раз, выход — снова по hop
FRAME = 1280                      # кадр поддельного потока `_Stream` — своя пара с frame_s=0.08


class _Model:
    """Поддельная модель mlx-audio в единицах библиотеки (0.5.6, `StreamingState`):
    `frames_processed` — родные кадры спектра; в потоке они приходят пачками энкодера
    с задержкой в пачку, на финале — все целые кадры поданного звука. Сегмент — от
    прошлого фронта до нового, слот 1. В конфиге лежит и прореживание энкодера:
    формула hop · 8 / частота дала бы 0,08 с вместо 0,01 (фикс A2 №478)."""

    def __init__(self):
        self.calls = []
        self.config = types.SimpleNamespace(
            processor_config=types.SimpleNamespace(hop_length=HOP, sampling_rate=SR),
            encoder_config=types.SimpleNamespace(subsampling_factor=8),
            output_subsampling_factor=1)

    def init_streaming_state(self):
        return types.SimpleNamespace(frames_processed=0, fed=0)

    def feed(self, pcm, state, sr, *, threshold, final=False):
        self.calls.append((len(pcm), sr, threshold, final))
        fed = state.fed + len(pcm)
        frames = fed // HOP if final else max(0, fed // PACK - 1) * 8
        segments = []
        if frames > state.frames_processed:
            segments.append(types.SimpleNamespace(start=state.frames_processed * HOP / SR,
                                                  end=frames * HOP / SR, speaker=1))
        return (types.SimpleNamespace(segments=segments),
                types.SimpleNamespace(frames_processed=frames, fed=fed))


def test_the_stream_wrapper_feeds_mono_and_reports_the_frames_of_the_model_state():
    model = _Model()
    stream = dn.NemotronStream(model, threshold=0.4)
    assert stream.frames_processed == 0
    assert stream.feed(np.zeros(SR, dtype=np.float32)) == [{"start": 0.0, "end": 0.88, "speaker": "nem1"}]
    assert stream.frames_processed == 88 and type(stream.frames_processed) is int
    assert stream.close() == [{"start": 0.88, "end": 1.0, "speaker": "nem1"}]
    assert model.calls == [(SR, SR, 0.4, False), (0, SR, 0.4, True)]
    assert stream.frames_processed == 100
    with pytest.raises(ValueError, match="моно"):
        stream.feed(np.zeros((2, 10), dtype=np.float32))


def test_the_frame_is_the_native_spectrum_hop_not_the_encoder_frame():
    """Единица `frames_processed` — hop / частота: 10 мс, а не 80 мс кадра энкодера."""
    assert dn.frame_seconds(_Model()) == pytest.approx(0.01)


class _Stream:
    """Поток без модели: сколько сэмплов в каждом `feed`, кадр на каждые 1280 с задержкой
    в кадр, сегмент от прошлого фронта до нового; `close` дописывает отставший кадр — как
    настоящая модель, фронт не обгоняет поданный звук (`_check_front`)."""

    def __init__(self):
        self.fed = []
        self.frames_processed = 0

    def feed(self, pcm):
        self.fed.append(len(pcm))
        before = self.frames_processed
        self.frames_processed = max(0, sum(self.fed) // FRAME - 1)
        return [{"start": before * 0.08, "end": self.frames_processed * 0.08, "speaker": "nem2"}]

    def close(self):
        before = self.frames_processed
        self.frames_processed = sum(self.fed) // FRAME
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
                        "frame_s": 0.01, "step": dn.STREAM_STEP}
    assert lines[-1]["type"] == "front" and lines[-1]["final"] is True and lines[-1]["fed"] == SR
    assert lines[-1]["frames"] == SR // HOP, "финал разметил весь звук"


def test_the_engine_reads_its_audio_from_its_stdin():
    """Боевой читатель потока — дескриптор 0 процесса движка; тесты выше подставляют свой."""
    code = ("import sys; sys.path.insert(0, %r); import diarize_nemotron as dn; "
            "sys.stdout.write(repr(dn._read_stdin(64)))" % str(SRC))
    out = subprocess.run([sys.executable, "-c", code], input=b"\x01\x02\x03", capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    assert out.stdout.decode() == repr(b"\x01\x02\x03")


@pytest.mark.parametrize("frames, fed, final, broken", [
    (10, 10 * HOP, False, False),          # фронт ровно на звуке
    (10, 10 * HOP - 1, False, True),       # на сэмпл впереди звука — не та единица
    (0, 10 * HOP, False, False),           # в потоке отставать можно: модель ждёт окно
    (10, 11 * HOP - 1, True, False),       # финал недобрал меньше кадра — хвост не кадр
    (10, 11 * HOP, True, True),            # финал недобрал ровно кадр
    (11, 10 * HOP, True, True),            # финал впереди звука
])
def test_the_front_is_checked_against_the_audio_fed(frames, fed, final, broken):
    """Единица кадра сверяется с поданным звуком: модель не размечает звука, которого не
    получила, а финал размечает весь с точностью до кадра (замер на mlx-audio 0.5.6 —
    на финале ровно `fed // hop`; входной круг фикса A2 №478, I1 и M3)."""
    if broken:
        with pytest.raises(RuntimeError, match="единица кадра не та"):
            dn._check_front(frames, fed, 0.01, final=final)
    else:
        dn._check_front(frames, fed, 0.01, final=final)


class _Unit(_Stream):
    """Поток, чья единица кадра в восемь раз меньше той, что ребёнок назвал: фронт
    убегает вперёд звука — прежняя формула кадра на настоящей модели."""

    def feed(self, pcm):
        out = super().feed(pcm)
        self.frames_processed *= 8
        return out


def test_the_stream_dies_with_numbers_when_the_front_outruns_the_audio():
    """Сверка стоит в самом цикле: неверная единица роняет ребёнка до строки фронта, а не
    уходит в журнал обычным фронтом."""
    stream, out = _Unit(), []
    reads = iter([b"\x01\x00" * (4 * FRAME), b""])
    with pytest.raises(RuntimeError, match=r"впереди поданного звука .*единица кадра не та"):
        dn.run_stream(stream, frame_s=0.08, read=lambda n: next(reads), emit=out.append, step=2 * FRAME)
    assert not [m for m in out if m["type"] == "front"], "неверный фронт не уходит в протокол"


class _Short(_Stream):
    """Поток, чей финал не дописывает отставшие кадры и недобирает больше кадра: единица
    кадра больше настоящей."""

    def feed(self, pcm):
        out = super().feed(pcm)
        self.frames_processed = max(0, self.frames_processed - 1)
        return out

    def close(self):
        return []


def test_the_stream_dies_when_the_final_front_does_not_cover_the_audio():
    stream, out = _Short(), []
    reads = iter([b"\x01\x00" * (4 * FRAME), b""])
    with pytest.raises(RuntimeError, match=r"не покрыл поданный звук"):
        dn.run_stream(stream, frame_s=0.08, read=lambda n: next(reads), emit=out.append, step=2 * FRAME)
    assert not [m for m in out if m.get("final")], "финального фронта без покрытия нет"
