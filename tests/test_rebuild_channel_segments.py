"""Разметка каналов пересборки — поведение, закреплённое до смены движка (№473).

Блок жил замыканием внутри `rebuild()` без тестов, и четыре входных круга подряд
находили Critical на его стыках: два порога карликов, эхо-фильтр по отрезкам
собеседников, нумерация голосов. Здесь — СЕГОДНЯШНЕЕ поведение sherpa: вынос в
`resolve_channel_segments` / `merge_dwarfs` / `paragraphs` его не меняет, и
следующий движок подключается поставщиком сырых сегментов, а не правкой блока.
"""
import pathlib
import sys

import numpy as np
import pytest
import charoite_paths

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import rebuild_transcript as rt  # noqa: E402

OWNER = "Игорь Ветров"


# ---------------------------------------------------------------- merge_dwarfs

def test_a_dwarf_joins_the_temporally_nearest_big_voice_on_either_side():
    """Голос 7 набрал 2 с: его отрезок после голоса 1 уходит голосу 1, перед
    голосом 2 — голосу 2. Близость — по зазору до ближайшего края."""
    segs = [(0.0, 30.0, 1), (31.0, 32.0, 7), (100.0, 130.0, 2), (95.0, 96.0, 7)]
    assert rt.merge_dwarfs(segs, 25.0) == [
        (0.0, 30.0, 1), (31.0, 32.0, 1), (100.0, 130.0, 2), (95.0, 96.0, 2)]


def test_the_threshold_is_inclusive_and_a_hair_below_is_a_dwarf():
    assert rt.merge_dwarfs([(0.0, 25.0, 1), (30.0, 60.0, 2)], 25.0) == [
        (0.0, 25.0, 1), (30.0, 60.0, 2)]
    assert rt.merge_dwarfs([(0.0, 24.5, 1), (30.0, 60.0, 2)], 25.0) == [
        (0.0, 24.5, 2), (30.0, 60.0, 2)]


def test_the_dwarf_test_sums_every_segment_of_a_voice():
    """Голос 1 — два отрезка по 15 с: крупный по сумме, хотя каждый короче порога."""
    segs = [(0.0, 15.0, 1), (40.0, 55.0, 1), (16.0, 36.0, 2), (60.0, 70.0, 3)]
    assert [k for *_, k in rt.merge_dwarfs(segs, 25.0)] == [1, 1, 1, 1]


def test_when_every_voice_is_a_dwarf_nothing_is_merged():
    segs = [(0.0, 5.0, 1), (6.0, 9.0, 2)]
    assert rt.merge_dwarfs(segs, 25.0) == segs
    assert rt.merge_dwarfs([], 25.0) == []


# ----------------------------------------------------- resolve_channel_segments

def test_call_voices_are_numbered_by_first_appearance_and_sound_from_the_call():
    bh = [(0.0, 30.0, 5), (40.0, 70.0, 3), (80.0, 110.0, 5)]
    segments, chan = rt.resolve_channel_segments(bh, None, owner_label=OWNER)
    assert segments == [(0.0, 30.0, "Собеседник 1"), (40.0, 70.0, "Собеседник 2"),
                        (80.0, 110.0, "Собеседник 1")]
    assert chan == {"Собеседник 1": "bh", "Собеседник 2": "bh"}


def test_the_call_dwarf_threshold_is_25_seconds_and_a_parameter():
    """25 с ровно — голос; 24.5 с — осколок. Параметр меняет только канал звонка."""
    bh = [(0.0, 30.0, 1), (31.0, 56.0, 2), (60.0, 84.5, 3)]
    segs, _ = rt.resolve_channel_segments(bh, None, owner_label=OWNER)
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 2", "Собеседник 2"]
    segs, _ = rt.resolve_channel_segments(bh, None, owner_label=OWNER, bh_dwarf_s=5.0)
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 2", "Собеседник 3"]


def test_the_mic_dwarf_threshold_is_10_seconds():
    """В микрофоне 10 с ровно — голос, 9.5 с — осколок ближайшего крупного."""
    mic = [(0.0, 40.0, 0), (50.0, 60.0, 1), (70.0, 79.5, 2)]
    segs, chan = rt.resolve_channel_segments(None, mic, owner_label="")
    assert segs == [(0.0, 40.0, "Собеседник 1"), (50.0, 60.0, "Собеседник 2"),
                    (70.0, 79.5, "Собеседник 2")]
    assert chan == {"Собеседник 1": "mic", "Собеседник 2": "mic"}


def test_the_mic_threshold_does_not_follow_the_call_parameter():
    mic = [(0.0, 40.0, 0), (70.0, 79.5, 2)]
    segs, _ = rt.resolve_channel_segments(None, mic, owner_label="", bh_dwarf_s=5.0)
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 1"]


@pytest.mark.parametrize("bh", [None, []], ids=["канала нет", "канал молчал"])
def test_without_call_speech_the_owner_is_not_signed_even_with_a_name(bh):
    """Очная встреча: различать некого, `owner_voice` отвечает None."""
    segs, chan = rt.resolve_channel_segments(bh, [(0.0, 60.0, 0)], owner_label=OWNER)
    assert segs == [(0.0, 60.0, "Собеседник 1")]
    assert chan == {"Собеседник 1": "mic"}


def call_with_two_mic_voices(owner_label):
    bh = [(100.0, 130.0, 0), (140.0, 170.0, 1)]
    mic = [(0.0, 40.0, 3), (45.0, 57.0, 4)]      # 40 против 12 с: доля 0.77, отрыв 0.54
    return rt.resolve_channel_segments(bh, mic, owner_label=owner_label)


def test_in_a_call_the_dominant_mic_voice_is_the_owner_and_numbering_continues():
    segs, chan = call_with_two_mic_voices(OWNER)
    assert segs == [(100.0, 130.0, "Собеседник 1"), (140.0, 170.0, "Собеседник 2"),
                    (0.0, 40.0, OWNER), (45.0, 57.0, "Собеседник 3")]
    assert chan == {"Собеседник 1": "bh", "Собеседник 2": "bh", OWNER: "mic",
                    "Собеседник 3": "mic"}


def test_an_empty_owner_label_leaves_the_owner_unsigned():
    segs, chan = call_with_two_mic_voices("")
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 2",
                                         "Собеседник 3", "Собеседник 4"]
    assert chan["Собеседник 3"] == chan["Собеседник 4"] == "mic"


def test_a_mic_segment_covered_more_than_half_by_a_call_segment_is_echo():
    """Накрыт 10 с из 19 — эхо динамиков, выброшен; в микрофоне речи не осталось."""
    segs, chan = rt.resolve_channel_segments([(0.0, 30.0, 0)], [(20.0, 39.0, 1)],
                                             owner_label=OWNER)
    assert segs == [(0.0, 30.0, "Собеседник 1")]
    assert chan == {"Собеседник 1": "bh"}


def test_a_mic_segment_covered_exactly_half_stays():
    segs, _ = rt.resolve_channel_segments([(0.0, 30.0, 0)], [(20.0, 40.0, 1)],
                                          owner_label=OWNER)
    assert segs == [(0.0, 30.0, "Собеседник 1"), (20.0, 40.0, OWNER)]


def test_echo_is_judged_against_each_call_segment_separately():
    """Сегодняшняя геометрия: доля считается по ОДНОМУ отрезку собеседников.
    Микрофонный отрезок, накрытый двумя соседними наполовину каждым, остаётся."""
    bh = [(0.0, 30.0, 0), (30.0, 60.0, 1)]
    segs, _ = rt.resolve_channel_segments(bh, [(20.0, 40.0, 2)], owner_label=OWNER)
    assert (20.0, 40.0, OWNER) in segs


# ------------------------------------------------------------------ paragraphs

def test_paragraphs_join_one_voice_across_a_gap_shorter_than_two_seconds():
    segs = [(0.0, 5.0, "A"), (6.9, 9.0, "A"), (11.0, 12.0, "A"),
            (12.5, 13.0, "B"), (13.5, 14.0, "A")]
    assert rt.paragraphs(segs) == [[0.0, 9.0, "A"], [11.0, 12.0, "A"],
                                   [12.5, 13.0, "B"], [13.5, 14.0, "A"]]


def test_paragraphs_keep_the_longer_end_and_take_the_gap_as_a_parameter():
    assert rt.paragraphs([(0.0, 10.0, "A"), (1.0, 5.0, "A")]) == [[0.0, 10.0, "A"]]
    assert rt.paragraphs([(0.0, 1.0, "A"), (3.0, 4.0, "A")], gap=2.5) == [[0.0, 4.0, "A"]]


def test_paragraphs_sort_by_start_and_keep_the_call_first_on_a_tie():
    segs = [(10.0, 12.0, "B"), (0.0, 3.0, "A"), (10.0, 11.0, "C")]
    assert rt.paragraphs(segs) == [[0.0, 3.0, "A"], [10.0, 12.0, "B"], [10.0, 11.0, "C"]]


# ------------------------------------------------- rebuild(): звук и движок

SR = 16000


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    """Встреча с двумя каналами; разметку отдают заглушки, звук — нули нужной длины."""
    for d in ("logs", "transcripts", "recordings"):
        (tmp_path / d).mkdir()
    charoite_paths.use_data_root(tmp_path, replace=True)
    live = tmp_path / "transcripts" / "2026-08-20_143000.md"
    live.write_text("живая стенограмма", encoding="utf-8")
    paths = {}
    for label in ("mic", "blackhole"):
        paths[label] = tmp_path / "recordings" / f"2026-08-20_143000_{label}.wav"
        paths[label].write_bytes(b"")
    state = {"len": {"mic": 60, "blackhole": 60}, "meta": {}, "calls": [],
             "raw": {"mic": [(0.0, 40.0, 3), (45.0, 57.0, 4)],
                     "blackhole": [(100.0, 130.0, 0), (140.0, 170.0, 1)]}}

    def load_wav(p):
        label = "mic" if p.name.endswith("_mic.wav") else "blackhole"
        audio = np.zeros(int(state["len"][label] * SR), dtype=np.float32)
        audio[:1] = 1.0 if label == "blackhole" else 0.0       # метка канала для заглушки
        return audio, SR

    def diarize_channel(audio, sr, min_len=1.0, num_speakers=-1):
        label = "blackhole" if audio[0] == 1.0 else "mic"
        state["calls"].append((label, num_speakers))
        return list(state["raw"][label])

    monkeypatch.setattr(rt, "wait_recording", lambda rec, stamp, label, sr: paths.get(label))
    monkeypatch.setattr(rt, "load_wav", load_wav)
    monkeypatch.setattr(rt, "diarize_channel", diarize_channel)
    monkeypatch.setattr(rt, "live_meta", lambda live: state["meta"])
    monkeypatch.setattr(rt, "STT", lambda cfg: object())
    monkeypatch.setattr(rt, "stt_segment", lambda stt, chunk, sr: "реплика")
    monkeypatch.setattr(rt, "name_speakers", lambda cfg, lines, **kw: ({}, True))
    state["live"] = live
    return state


CFG = {"audio": {"samplerate": SR}, "sufler": {"user_name": OWNER}}


def test_rebuild_signs_the_owner_from_settings_in_a_call(meeting):
    out = rt.rebuild(meeting["live"], CFG)
    text = out.read_text(encoding="utf-8")
    assert OWNER in text and "Собеседник 1" in text and "Собеседник 3" in text


@pytest.mark.parametrize("speakers,expected", [(0, -1), (1, -1), (2, 2), (12, 12), (13, -1)])
def test_rebuild_hints_the_call_channel_with_the_live_count_in_range(meeting, speakers, expected):
    meeting["meta"] = {"speakers": speakers}
    rt.rebuild(meeting["live"], CFG)
    assert ("blackhole", expected) in meeting["calls"]
    assert ("mic", -1) in meeting["calls"]


@pytest.mark.parametrize("short", ["mic", "blackhole"])
def test_a_channel_of_twenty_seconds_or_less_is_not_diarized(meeting, short):
    meeting["len"][short] = 20
    rt.rebuild(meeting["live"], CFG)
    assert [label for label, _ in meeting["calls"]] == [
        label for label in ("blackhole", "mic") if label != short]


def test_the_owner_name_is_not_read_when_the_mic_is_not_diarized(meeting, monkeypatch):
    """Без микрофона пересборка настройки подписи не читала — и не читает."""
    meeting["len"]["mic"] = 10

    def refuse(cfg, **kw):
        raise AssertionError("подпись владельца читается без микрофона")
    monkeypatch.setattr(rt.channel_labels.ChannelLabels, "from_config", classmethod(
        lambda cls, cfg, **kw: refuse(cfg)))
    out = rt.rebuild(meeting["live"], CFG)
    assert "Собеседник 1" in out.read_text(encoding="utf-8")
