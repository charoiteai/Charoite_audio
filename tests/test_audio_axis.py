"""Ось сэмплов хаба захвата (№478, PR A1): одно число на канал — сколько дописано.

Живой поток Nemotron получит блоки канала через слушателя кадров, а STT-цикл —
чанки из pull_placed. Сопоставить метки потока с чанком можно только если у обоих
одна ось: начало блока и начало чанка отсчитаны от одного счётчика хаба, и никто
из потребителей не считает позицию сам. Здесь — гейт этой оси: чанк равен срезу
потока по своему началу через перекрытие, отрезание потолком и дренаж; тот же
срез лежит в записанном .pcm.
"""
import dataclasses
import pathlib
import sys
import types

import numpy as np
import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import audio as a  # noqa: E402

SR = 16000


def _hub(chunk_s=3.0, overlap_s=0.5):
    """Хаб настоящим конструктором, без устройств и без записи; порог речи такой,
    что речью считается любой ненулевой звук — ось проверяется, а не VAD."""
    cfg = {"audio": {"samplerate": SR, "chunk_seconds": chunk_s, "overlap_seconds": overlap_s,
                     "vad_energy_db": -200.0, "record": False, "device": "auto"},
           "log": {"recordings_dir": "recordings"}, "sufler": {"user_name": "Владелец"}}
    hub = a.AudioHub(cfg, captures=[])
    hub._register_captures([types.SimpleNamespace(label="blackhole")])
    return hub


def _stream(n):
    """Звук с уникальным значением в каждом сэмпле: срез со сдвигом на сэмпл не совпадёт."""
    return ((np.arange(n, dtype=np.float64) % 20000) / 40000.0 + 0.1).astype(np.float32)


def _feed(hub, stream, sizes):
    cap = types.SimpleNamespace(label="blackhole")
    pos = 0
    out = []
    for size in sizes:
        part = stream[pos:pos + size]
        if not len(part):
            break                          # поток кончился: дальше кормить нечем
        hub._consume(cap, part)
        pos += len(part)
        out += hub.pull_placed()
    return out, pos


def test_every_chunk_is_the_stream_at_its_start_through_the_overlap():
    hub = _hub()
    stream = _stream(SR * 40)
    rng = np.random.default_rng(7)
    sizes = [int(x) for x in rng.integers(300, 6000, 200)]
    placed, fed = _feed(hub, stream, sizes)
    assert len(placed) >= 10
    step = int(SR * (hub.chunk_s - hub.overlap_s))
    for k, p in enumerate(placed):
        assert p.start == k * step, "срез с перекрытием сдвигает начало ровно на шаг"
        assert np.array_equal(p.chunk, stream[p.start:p.start + len(p.chunk)])
        assert p.seq == ("blackhole", k)
    assert hub._appended["blackhole"] == fed


def test_a_block_that_fails_to_reach_the_buffer_does_not_move_the_axis():
    """Счётчик оси двигается вместе с буфером: блок, упавший на склейке, не сдвигает
    начала следующих чанков — иначе чанк по обе стороны от пропуска пришёл бы с началом,
    не равным месту его первого сэмпла (выходной круг 1 по №478 A1, M1)."""
    hub = _hub()
    stream = _stream(SR * 6)
    cap = types.SimpleNamespace(label="blackhole")
    hub._consume(cap, stream[:SR])
    try:
        hub._consume(cap, np.zeros((100, 2), dtype=np.float32))    # не того вида — склейка падает
    except ValueError:
        pass
    assert hub._appended["blackhole"] == SR, "упавший блок не на оси"
    hub._consume(cap, stream[SR:SR * 4])
    (p,) = hub.pull_placed()
    assert p.start == 0 and np.array_equal(p.chunk, stream[:len(p.chunk)])


def test_the_axis_survives_the_buffer_cap():
    """Потолок режет голову буфера — начало чанка всё равно на оси потока, со сдвигом
    ровно на отрезанное."""
    hub = _hub()
    hub.BUF_CAP_S = 5
    stream = _stream(SR * 12)
    cap = types.SimpleNamespace(label="blackhole")
    for i in range(0, len(stream), 4000):
        hub._consume(cap, stream[i:i + 4000])          # 12 с без среза: 7 с отрезано потолком
    placed = hub.pull_placed()
    assert placed and placed[0].start == SR * 12 - SR * 5
    assert np.array_equal(placed[0].chunk, stream[placed[0].start:placed[0].start + len(placed[0].chunk)])


def test_listeners_get_every_block_with_its_start_and_a_failing_one_harms_nobody():
    hub = _hub()
    got = []

    def broken(label, start, samples):
        raise RuntimeError("слушатель упал")

    hub.add_frame_listener(broken)
    hub.add_frame_listener(lambda label, start, samples: got.append((label, start, len(samples))))
    stream = _stream(SR * 5)
    sizes = [1000, 2500, 777, 4096, 1]
    _feed(hub, stream, sizes)
    assert got == [("blackhole", sum(sizes[:k]), sizes[k]) for k in range(len(sizes))]


def test_the_drain_moves_the_axis_without_calling_listeners():
    """Хвост после «Стоп» уходит в буфер и файл, но не слушателям — и ось его учитывает:
    следующий блок начинается после дренированного."""
    hub = _hub()
    got = []
    hub.add_frame_listener(lambda label, start, samples: got.append(start))
    cap = types.SimpleNamespace(label="blackhole")
    stream = _stream(3000)
    hub._consume(cap, stream[:1000])
    hub._consume(cap, stream[1000:1500], notify_frame=False)
    hub._consume(cap, stream[1500:3000])
    assert got == [0, 1500]


def test_pull_labeled_is_the_projection_of_pull_placed():
    hub = _hub()
    stream = _stream(SR * 4)
    cap = types.SimpleNamespace(label="blackhole")
    hub._consume(cap, stream)
    placed = hub.pull_placed()
    hub2 = _hub()
    hub2._consume(cap, stream)
    assert [(s, c.tolist()) for s, c in hub2.pull_labeled()] == \
           [(p.speaker, p.chunk.tolist()) for p in placed]
    assert hub.chunk_seq(placed[0].speaker) == placed[0].seq


def test_the_recorded_pcm_holds_the_chunk_at_its_start(tmp_path):
    """Тот же срез лежит в записанном .pcm: финал размечает запись, живой поток — блоки
    хаба, и сверка их меток возможна только при одной оси (квантование s16 — в допуске)."""
    hub = _hub()
    pcm = tmp_path / "blackhole.pcm"
    hub._sinks["blackhole"] = pcm.open("wb")
    stream = _stream(SR * 8)
    placed, _ = _feed(hub, stream, [1234] * (len(stream) // 1234))
    hub._sinks["blackhole"].close()
    recorded = np.frombuffer(pcm.read_bytes(), dtype="<i2").astype(np.float32) / 32767
    for p in placed:
        assert np.allclose(recorded[p.start:p.start + len(p.chunk)], np.clip(p.chunk, -1, 1), atol=1e-4)


def test_a_placed_chunk_cannot_be_moved_by_its_consumer():
    """Чанк идёт потребителям конвейера демона значением: сдвинуть `start` у одного —
    сдвинуть ось всем. Любое присваивание — отказ frozen, и новому полю тоже: со
    `slots=True` CPython 3.12 отвечает на него TypeError из `super()`, а не
    FrozenInstanceError (опыт 29.09) — slots у Placed сняты."""
    p = a.Placed("Собеседник", np.zeros(4, np.float32), SR, ("Собеседник", 1))
    for name in ("start", "extra"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(p, name, 0)
    assert p.start == SR
