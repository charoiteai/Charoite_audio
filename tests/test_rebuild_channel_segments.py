"""Разметка каналов пересборки — поведение, закреплённое до смены движка (№473).

Блок жил замыканием внутри `rebuild()` без тестов, и четыре входных круга подряд
находили Critical на его стыках: два порога карликов, эхо-фильтр по отрезкам
собеседников, нумерация голосов. Здесь — СЕГОДНЯШНЕЕ поведение sherpa: вынос в
`resolve_channel_segments` / `merge_dwarfs` / `paragraphs` его не меняет, и
следующий движок подключается поставщиком сырых сегментов, а не правкой блока.
"""
import os
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


def test_nearness_is_the_gap_to_the_nearer_edge_of_each_big_segment():
    """Карлик сразу после голоса 1 (зазор 1 с) и за 2 с до голоса 2 — к голосу 1;
    карлик в 1 с перед голосом 2 и в 2 с после голоса 1 — к голосу 2. Длинные
    крупные отрезки: расстояние считается до ближнего края, а не до начала."""
    a, b = (0.0, 30.0, 1), (34.0, 60.0, 2)
    assert rt.merge_dwarfs([a, (31.0, 32.0, 7), b], 25.0)[1] == (31.0, 32.0, 1)
    a, b = (0.0, 29.0, 1), (33.0, 60.0, 2)
    assert rt.merge_dwarfs([a, (31.0, 32.0, 7), b], 25.0)[1] == (31.0, 32.0, 2)


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


def test_in_a_call_the_dominant_mic_voice_is_the_owner_and_numbering_continues(monkeypatch):
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    segs, chan = call_with_two_mic_voices(OWNER)
    assert said[-1] == "mic: 2 сегментов, голосов 2, владелец: по преобладанию"
    assert segs == [(100.0, 130.0, "Собеседник 1"), (140.0, 170.0, "Собеседник 2"),
                    (0.0, 40.0, OWNER), (45.0, 57.0, "Собеседник 3")]
    assert chan == {"Собеседник 1": "bh", "Собеседник 2": "bh", OWNER: "mic",
                    "Собеседник 3": "mic"}


def test_an_empty_owner_label_leaves_the_owner_unsigned(monkeypatch):
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    segs, chan = call_with_two_mic_voices("")
    assert said[-1].endswith("владелец: не назначен")
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


def test_echo_is_judged_against_the_union_of_call_segments():
    """Доля — по объединению отрезков собеседников (№473): микрофонный отрезок,
    накрытый двумя соседними наполовину каждым, — эхо; до №473 он оставался, и с
    Nemotron (мелкие куски) эхо шло в канал владельца (замер 28.09: 69 % против 17.5 %)."""
    bh = [(0.0, 30.0, 0), (30.0, 60.0, 1)]
    segs, _ = rt.resolve_channel_segments(bh, [(20.0, 40.0, 2)], owner_label=OWNER)
    assert all(lbl != OWNER for *_, lbl in segs)


def test_the_union_threshold_is_still_more_than_half():
    """Два куска по 5 с на 20-секундном отрезке — ровно половина: остаётся."""
    bh = [(0.0, 25.0, 0), (35.0, 60.0, 1)]
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


def test_paragraphs_join_to_the_last_paragraph_not_the_first():
    """Склейка смотрит на последний абзац: и зазор, и новый конец — его."""
    assert rt.paragraphs([(0.0, 1.0, "A"), (5.0, 6.0, "B"), (6.5, 7.0, "B")]) == [
        [0.0, 1.0, "A"], [5.0, 7.0, "B"]]
    assert rt.paragraphs([(0.0, 30.0, "A"), (5.0, 6.0, "B"), (6.5, 7.0, "B")]) == [
        [0.0, 30.0, "A"], [5.0, 7.0, "B"]]


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
    state = {"len": {"mic": 60, "blackhole": 60}, "meta": {}, "calls": [], "merge": [], "veto": [],
             "raw": {"mic": [(0.0, 40.0, 3), (45.0, 57.0, 4)],
                     "blackhole": [(100.0, 130.0, 0), (140.0, 170.0, 1)]}}

    def load_wav(p):
        label = "mic" if p.name.endswith("_mic.wav") else "blackhole"
        audio = np.zeros(int(state["len"][label] * SR), dtype=np.float32)
        audio[:1] = 1.0 if label == "blackhole" else 0.0       # метка канала для заглушки
        return audio, SR

    def diarize_channel(audio, sr, min_len=1.0, num_speakers=-1, **kw):
        label = "blackhole" if audio[0] == 1.0 else "mic"
        state["calls"].append((label, num_speakers))
        state["merge"].append((label, kw.get("merge_shards")))
        state["veto"].append((label, kw.get("veto")))
        raw = state["raw"][label]
        if callable(raw):
            return raw(num_speakers)
        return None if raw is None else list(raw)

    monkeypatch.setattr(rt, "wait_recording", lambda rec, stamp, label, sr: paths.get(label))
    state["paths"] = paths
    monkeypatch.setattr(rt, "load_wav", load_wav)
    monkeypatch.setattr(rt, "diarize_channel", diarize_channel)
    monkeypatch.setattr(rt, "live_meta", lambda live: state["meta"])
    monkeypatch.setattr(rt, "STT", lambda cfg: object())
    monkeypatch.setattr(rt, "stt_segment", lambda stt, chunk, sr: "реплика")
    monkeypatch.setattr(rt, "name_speakers", lambda cfg, lines, **kw: rt.NamesOutcome({}, rt.NamesOutcome.ANSWERED))
    state["live"] = live
    return state


CFG = {"audio": {"samplerate": SR}, "sufler": {"user_name": OWNER}}


def test_rebuild_signs_the_owner_from_settings_in_a_call(meeting):
    out = rt.rebuild(meeting["live"], CFG)
    text = out.read_text(encoding="utf-8")
    assert OWNER in text and "Собеседник 1" in text and "Собеседник 3" in text


@pytest.mark.parametrize("naming,note,not_note", [
    (rt.NamesOutcome({}, rt.NamesOutcome.SILENT), rt.NAMES_PENDING_NOTE, "не прошло проверку"),
    (rt.NamesOutcome({}, rt.NamesOutcome.REJECTED, 2), rt.NAMES_REJECTED_NOTE.format(proposed=2),
     "не ответила на разборе"),
])
def test_unnamed_labels_get_the_note_of_their_cause(meeting, monkeypatch, naming, note, not_note):
    """№499: плашка в шапке — по исходу разбора имён. Молчание зовёт пересобрать, отвергнутые гвардами
    имена — нет (пересборка упрётся в те же гварды); признак names_pending видит обе."""
    monkeypatch.setattr(rt, "name_speakers", lambda cfg, lines, **kw: naming)
    out = rt.rebuild(meeting["live"], CFG)
    text = out.read_text(encoding="utf-8")
    assert note in text
    assert not_note not in text
    assert rt.names_pending(out) is True



def test_the_rebuild_yields_to_a_live_meeting_before_each_heavy_step(meeting, monkeypatch):
    """№508: уступка встрече — перед разметкой каждого канала и перед распознаванием,
    а не только на входе в очередь: пересборка, простоявшая за соседней, о встрече,
    начавшейся за это время, не знает, а разметка канала — минуты работы."""
    order = []
    monkeypatch.setattr(rt, "_yield_to_live", lambda what, cap=None: order.append(("уступка", what, cap)))
    real = rt.diarize_channel

    def diarize_channel(audio, sr, **kw):
        order.append(("разметка", "blackhole" if audio[0] == 1.0 else "mic"))
        return real(audio, sr, **kw)

    monkeypatch.setattr(rt, "diarize_channel", diarize_channel)
    monkeypatch.setattr(rt, "STT", lambda cfg: order.append(("распознавание",)) or object())
    rt.rebuild(meeting["live"], CFG)
    heavy = [o for o in order if o[0] in ("уступка", "разметка", "распознавание")]
    assert heavy[:6] == [("уступка", "разметка голосов собеседников", 600), ("разметка", "blackhole"),
                         ("уступка", "разметка голосов микрофона", 600), ("разметка", "mic"),
                         ("уступка", "распознавание", 600), ("распознавание",)]

def test_a_usable_answer_leaves_no_note(meeting):
    """Годный ответ модели без имён («имён не звучало») плашки не даёт — даже с безымянными метками."""
    out = rt.rebuild(meeting["live"], CFG)
    assert rt.NAMES_PENDING_PREFIX not in out.read_text(encoding="utf-8")
    assert rt.names_pending(out) is False


@pytest.mark.parametrize("speakers,expected", [(0, -1), (1, -1), (2, 2), (12, 12), (13, -1)])
def test_rebuild_hints_the_call_channel_with_the_live_count_in_range(meeting, speakers, expected):
    meeting["meta"] = {"speakers": speakers}
    rt.rebuild(meeting["live"], CFG)
    assert ("blackhole", expected) in meeting["calls"]
    assert ("mic", -1) in meeting["calls"]


@pytest.mark.parametrize("meta,expected", [
    ({"speakers": 16, "speakers_call": 8}, 8),    # квота микрофона (№573): общий счёт вне диапазона
    ({"speakers": 9, "speakers_call": 5}, 5),
    ({"speakers": 7}, 7),                         # старый сайдкар — общий счёт, как было
    ({"speakers": 7, "speakers_call": 0}, -1),    # ключ есть: решает он, а не общий счёт
    ({"speakers": 7, "speakers_call": "x"}, -1),
])
def test_the_call_channel_is_hinted_with_its_own_count(meeting, meta, expected):
    meeting["meta"] = meta
    rt.rebuild(meeting["live"], CFG)
    assert ("blackhole", expected) in meeting["calls"]


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


# ------------------------------------------------------------ disjoint (№473)

import random  # noqa: E402

import foreign_python as fp  # noqa: E402


def _union(segs):
    """Объединение интервалов — отсортированный список непересекающихся [s, e]."""
    out = []
    for s, e, *_ in sorted(segs):
        if out and s <= out[-1][1] + 1e-9:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def _same_union(a, b):
    ua, ub = _union(a), _union(b)
    return len(ua) == len(ub) and all(abs(x[0] - y[0]) < 1e-9 and abs(x[1] - y[1]) < 1e-9
                                      for x, y in zip(ua, ub))


def test_disjoint_splits_a_partial_overlap_in_the_middle_in_either_order():
    a, b = (0.0, 10.0, 1), (6.0, 16.0, 2)
    assert rt.disjoint([a, b]) == rt.disjoint([b, a]) == [(0.0, 8.0, 1), (8.0, 16.0, 2)]


def test_disjoint_cuts_the_enclosing_voice_around_a_nested_reply_longer_than_a_second():
    outer, inner = (0.0, 10.0, 1), (4.0, 5.5, 2)
    assert rt.disjoint([outer, inner]) == rt.disjoint([inner, outer]) == [
        (0.0, 4.0, 1), (4.0, 5.5, 2), (5.5, 10.0, 1)]


@pytest.mark.parametrize("length", [0.4, 1.0])
def test_a_nested_reply_of_a_second_or_less_goes_to_the_enclosing_voice(length):
    outer, inner = (0.0, 10.0, 1), (4.0, 4.0 + length, 2)
    assert rt.disjoint([outer, inner]) == rt.disjoint([inner, outer]) == [outer]


def test_a_nested_reply_at_the_edge_leaves_no_empty_piece():
    assert rt.disjoint([(0.0, 10.0, 1), (0.0, 3.0, 2)]) == [(0.0, 3.0, 2), (3.0, 10.0, 1)]
    assert rt.disjoint([(0.0, 10.0, 1), (7.0, 10.0, 2)]) == [(0.0, 7.0, 1), (7.0, 10.0, 2)]


def test_disjoint_keeps_what_does_not_overlap_and_drops_empty_segments():
    segs = [(5.0, 6.0, 2), (0.0, 1.0, 1), (3.0, 3.0, 4), (4.0, 3.0, 5)]
    assert rt.disjoint(segs) == [(0.0, 1.0, 1), (5.0, 6.0, 2)]
    assert rt.disjoint([]) == []


def test_a_long_segment_over_several_resolves_each_by_its_rule():
    """Отрезок 0–20 поверх трёх: частичное слева, вложенное длинное, вложенное
    короткое, частичное справа."""
    segs = [(-2.0, 2.0, 1), (5.0, 8.0, 2), (10.0, 10.5, 3), (18.0, 25.0, 4), (0.0, 20.0, 9)]
    got = rt.disjoint(segs)
    assert got == [(-2.0, 1.0, 1), (1.0, 5.0, 9), (5.0, 8.0, 2), (8.0, 19.0, 9), (19.0, 25.0, 4)]


@pytest.mark.parametrize("segs,expected", [
    # хвост, начинающийся ровно там, где кончается вставляемый, остаётся на месте
    ([(0.0, 3.0, 1), (0.0, 4.0, 2), (1.0, 3.0, 3)],
     [(0.0, 1.0, 1), (1.0, 3.0, 3), (3.0, 4.0, 2)]),
    # ...и не удваивается, когда вставляемый пересекает несколько кусков
    ([(0.0, 10.0, 1), (2.0, 6.0, 2), (3.0, 5.0, 3), (4.0, 6.0, 4)],
     [(0.0, 2.0, 1), (2.0, 3.0, 2), (3.0, 4.5, 3), (4.5, 6.0, 4), (6.0, 10.0, 1)]),
    # кусок, начавшийся вместе со вставляемым, — вложенный, а не «раньше»; секунда — поглощается
    ([(0.0, 2.0, 1), (0.0, 3.0, 2), (2.0, 4.0, 3)],
     [(0.0, 2.0, 1), (2.0, 4.0, 3)]),
    # кусок, кончающийся вместе со вставляемым, — тоже вложенный
    ([(0.0, 2.0, 1), (0.0, 3.0, 2), (1.0, 3.0, 3)],
     [(0.0, 1.5, 1), (1.5, 3.0, 3)]),
    # равное начало: длинный раньше — каждый голос сохраняет свою часть
    ([(0.0, 2.0, 1), (0.0, 3.0, 2), (0.0, 5.0, 3)],
     [(0.0, 2.0, 1), (2.0, 3.0, 2), (3.0, 5.0, 3)]),
])
def test_disjoint_on_boundaries_and_ties(segs, expected):
    """Границы и равные начала — там, где случайные разметки почти не попадают
    (мутатор диапазона 28.09: восемь выживших в `_insert`)."""
    assert rt.disjoint(segs) == expected


def test_disjoint_properties_on_random_layouts():
    """Без перекрытий, по порядку, объединение то же, метки только свои."""
    rng = random.Random(473)
    for _ in range(3000):
        segs = []
        for _ in range(rng.randint(0, 12)):
            s = round(rng.uniform(0, 60), 2)
            segs.append((s, round(s + rng.choice([rng.uniform(0.1, 1.2), rng.uniform(1, 15)]), 2),
                         rng.randint(0, 4)))
        got = rt.disjoint(segs)
        assert all(e > s for s, e, _ in got), (segs, got)
        assert all(got[i][1] <= got[i + 1][0] + 1e-9 for i in range(len(got) - 1)), (segs, got)
        assert _same_union(segs, got), (segs, got)
        assert {k for *_, k in got} <= {k for *_, k in segs}


def test_a_voice_whose_only_reply_is_absorbed_leaves_the_numbering():
    """Решение круга 5 (I4): короткая вложенная реплика отходит объемлющему голосу,
    как слитый карлик; её голос не получает номера, следующий идёт по порядку."""
    bh = [(0.0, 30.0, 7), (10.0, 10.8, 3), (40.0, 70.0, 5)]
    segs, chan = rt.resolve_channel_segments(bh, None, owner_label=OWNER, bh_dwarf_s=5.0)
    assert segs == [(0.0, 30.0, "Собеседник 1"), (40.0, 70.0, "Собеседник 2")]
    assert set(chan) == {"Собеседник 1", "Собеседник 2"}


def test_the_call_channel_has_no_overlaps_after_resolve():
    bh = [(0.0, 30.0, 1), (25.0, 60.0, 2)]
    segs, _ = rt.resolve_channel_segments(bh, None, owner_label=OWNER)
    assert segs == [(0.0, 27.5, "Собеседник 1"), (27.5, 60.0, "Собеседник 2")]


# ------------------------------------------------- движок канала собеседников

@pytest.mark.parametrize("backend", [None, "", "sherpa", " Sherpa "])
def test_sherpa_is_the_default_and_calls_no_engine(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: pytest.fail("движок позван при sherpa"))
    cfg = {"sufler": {} if backend is None else {"diarize_backend": backend}}
    assert rt.call_channel_engine(cfg, tmp_path / "bh.wav", 600.0) == (None, "")
    assert rt.call_channel_engine({}, tmp_path / "bh.wav", 600.0) == (None, "")


def test_an_unknown_backend_falls_back_with_a_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: pytest.fail("движок позван при неизвестном имени"))
    segs, reason = rt.call_channel_engine({"sufler": {"diarize_backend": "nemotorn"}},
                                          tmp_path / "bh.wav", 600.0)
    assert segs is None and "'nemotorn'" in reason and "sherpa, nemotron" in reason


def test_nemotron_gets_its_setting_the_data_root_and_a_ceiling(monkeypatch, tmp_path):
    """Пересборка отдаёт двери настройку как есть и корень данных; где окружение и
    веса — решает модуль движка (№474)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    seen = {}

    def fake(setting, wav, *, root, timeout):
        seen.update(setting=setting, wav=wav, root=root, timeout=timeout)
        return fp.Outcome(fp.OK, payload=[(0.0, 5.0, 0), (5.0, 5.9, 1), (6.0, 7.0, 1)])
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", fake)
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    cfg = {"sufler": {"diarize_backend": "Nemotron", "nemotron_python": " /env/bin/python "}}
    segs, reason = rt.call_channel_engine(cfg, tmp_path / "bh.wav", 1200.0)
    assert (segs, reason) == ([(0.0, 5.0, 0), (6.0, 7.0, 1)], "")     # короче секунды — прочь
    assert said == ["Nemotron: 2 сегментов из 3 за 0 с"]
    assert seen == {"setting": " /env/bin/python ", "wav": tmp_path / "bh.wav",
                    "root": tmp_path, "timeout": 180.0}


@pytest.mark.parametrize("kind", [fp.UNAVAILABLE, fp.FAILED])
def test_a_refusal_of_nemotron_gives_the_fixed_phrase_and_logs_the_raw_reason(kind, monkeypatch, tmp_path):
    """№495: в шапку — закреплённая фраза, сырой отказ — только в журнал."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    said: list[str] = []
    monkeypatch.setattr(rt, "log", said.append)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(kind, reason="нет весов"))
    assert rt.call_channel_engine({"sufler": {"diarize_backend": "nemotron"}},
                                  tmp_path / "bh.wav", 60.0) == (None, rt.ENGINE_REFUSED_REASON)
    assert any("нет весов" in line for line in said), said


@pytest.mark.parametrize("payload", [[], [(0.0, 0.5, 0), (3.0, 3.9, 1)]], ids=["пусто", "одни осколки"])
def test_a_success_without_segments_is_a_refusal(payload, monkeypatch, tmp_path):
    """Успех без единого отрезка (после отсева коротких) на записи длиннее 20 с —
    отказ движка в другой форме: иначе канал собеседников молча пустел бы
    (выходной круг 1 по №473, C1)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=payload))
    assert rt.call_channel_engine({"sufler": {"diarize_backend": "nemotron"}},
                                  tmp_path / "bh.wav", 1234.4) == (
        None, "Nemotron — ни одного отрезка на 1234 с записи")


@pytest.mark.parametrize("payload,degenerate", [
    ([(0.0, 1110.0, 0)], True),                          # один отрезок на 92,5 % записи
    ([(30.0, 1110.0, 0)], True),                         # ровно 90 % — тоже
    ([(0.0, 1070.0, 0)], False),                         # 89 % — ещё правдоподобно
    ([(600.0, 1100.0, 0)], False),                       # поздний отрезок: считается длина, не конец
    ([(0.0, 600.0, 0), (601.0, 1190.0, 0)], False),      # звонок один на один: много отрезков
])
def test_one_segment_over_the_whole_recording_is_a_refusal(payload, degenerate, monkeypatch, tmp_path):
    """Один отрезок почти на всю запись — вырожденный ответ (выходной круг 2 по
    №473, M1); один голос с паузами — нет."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=payload))
    segs, reason = rt.call_channel_engine({"sufler": {"diarize_backend": "nemotron"}},
                                          tmp_path / "bh.wav", 1200.0)
    if degenerate:
        assert (segs, reason) == (None, "Nemotron — один отрезок на всю запись (1200 с)")
    else:
        assert (segs, reason) == (payload, "")


def _nemotron_cfg():
    return {"audio": {"samplerate": SR},
            "sufler": {"user_name": OWNER, "diarize_backend": "nemotron", "nemotron_python": "/env/bin/python"}}


def test_rebuild_on_nemotron_does_not_run_sherpa_on_the_call_and_keeps_small_voices(meeting, monkeypatch):
    """Nemotron разметил — sherpa по каналу собеседников не зовётся, порог карликов
    5 с: голос с 6 с речи остаётся отдельным человеком (у sherpa слился бы)."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", lambda *a, **k: fp.Outcome(
        fp.OK, payload=[(100.0, 130.0, 0), (140.0, 146.0, 1)]))
    out = rt.rebuild(meeting["live"], _nemotron_cfg())
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    text = out.read_text(encoding="utf-8")
    assert "Собеседник 2" in text and "Собеседник 3" in text   # два собеседника, затем микрофон
    assert "запасным движком" not in text


def test_rebuild_falls_back_to_sherpa_and_says_why_in_the_header(meeting, monkeypatch):
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", lambda *a, **k: fp.Outcome(
        fp.FAILED, reason="не уложился в 66 с"))
    meeting["meta"] = {"speakers": 3}
    out = rt.rebuild(meeting["live"], _nemotron_cfg())
    assert ("blackhole", 3) in meeting["calls"]
    text = out.read_text(encoding="utf-8")
    assert rt.ENGINE_FALLBACK_NOTE.format(reason=rt.ENGINE_REFUSED_REASON) in text
    assert "не уложился" not in text                                  # сырой отказ — в журнал (№495)
    assert text.index("запасным движком") < text.index("**")          # в шапке, до реплик


@pytest.mark.parametrize("kind", [fp.UNAVAILABLE, fp.FAILED])
def test_the_header_carries_no_local_path_of_a_refusal(kind, meeting, monkeypatch):
    """№495, сторож по поведению: отказ движка несёт пути машины — домашний
    каталог (имя учётки), корень данных, его realpath и рецепт скачивания весов с
    путём, собранный настоящей `fetch_recipe`. Ни одно из них не доходит до
    пересылаемой стенограммы."""
    root = meeting["live"].parent.parent
    home = pathlib.Path.home()
    recipe = rt.diarize_nemotron.fetch_recipe(home / "models" / "diar" / "nemotron")
    raw = (f"нет интерпретатора {home / 'venv' / 'bin' / 'python'}; веса в {root / 'models'} — "
           f"{recipe}; {os.path.realpath(root)}")
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(kind, reason=raw))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert rt.ENGINE_REFUSED_REASON in text
    for leaked in (str(home), str(root), os.path.realpath(root), "hf download"):
        assert leaked not in text, leaked


def test_rebuild_on_an_empty_nemotron_answer_labels_the_call_with_sherpa(meeting, monkeypatch):
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=[]))
    out = rt.rebuild(meeting["live"], _nemotron_cfg())
    assert ("blackhole", -1) in meeting["calls"]
    text = out.read_text(encoding="utf-8")
    assert "ни одного отрезка" in text and "Собеседник 1" in text


def test_rebuild_on_sherpa_writes_no_engine_note(meeting):
    out = rt.rebuild(meeting["live"], CFG)
    assert "запасным движком" not in out.read_text(encoding="utf-8")


def test_the_nemotron_ceiling_grows_with_the_recording(meeting, monkeypatch):
    seen = []
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda setting, wav, *, root, timeout: seen.append(timeout) or fp.Outcome(
                            fp.OK, payload=[(100.0, 130.0, 0)]))
    meeting["len"]["blackhole"] = 600
    rt.rebuild(meeting["live"], _nemotron_cfg())
    assert seen == [rt.NEMOTRON_TIMEOUT_S + 60.0]


# ---------------------------------------------- очная встреча: микрофон без звонка (№559)

ROOM = {"speakers": 7, "names": {"Собеседник 2": "Анна"}}
LIVE_ROOM = "# Встреча\n\n**Анна** [14:30]:\nпривет\n\n**Собеседник 5** [14:30]:\nага\n"


def _room(meeting, speakers=7, call="silent"):
    """Канал собеседников молчит. call="silent" — запись есть и размечена пустой
    (комната, №565); "absent" — записи нет вовсе (подсказка, №559)."""
    meeting["meta"] = dict(ROOM, speakers=speakers)
    meeting["raw"]["blackhole"] = []                       # канал собеседников размечен пустым
    if call == "absent":
        del meeting["paths"]["blackhole"]
    meeting["live"].write_text(LIVE_ROOM, encoding="utf-8")


def test_a_silent_call_channel_hints_the_mic_with_the_live_count_and_merges_after(meeting):
    """Канала собеседников не записано: число живой сессии идёт микрофону верхней
    границей — со склейкой осколков после (иначе монолог, раздробленный живым
    трекером, нарезался бы; №559)."""
    _room(meeting, call="absent")
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", 7) in meeting["calls"] and ("mic", True) in meeting["merge"]


def test_no_call_recording_at_all_also_hints_the_mic(meeting):
    _room(meeting, call="absent")
    rt.rebuild(meeting["live"], CFG)
    assert meeting["calls"] == [("mic", 7)] and meeting["veto"] == [("mic", None)]


def test_a_recorded_silent_call_channel_is_a_room_the_mic_goes_auto_under_the_veto(meeting):
    """№565: запись канала собеседников есть и размечена пустой — комната. Микрофон
    идёт в авто со склейкой под запретом, без подсказки живого трекера (она сливала
    разделимые голоса: 3 метки против 4 на очной 29.09)."""
    _room(meeting)
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", -1) in meeting["calls"] and ("mic", 7) not in meeting["calls"]
    assert ("mic", rt.VETO_BELOW) in meeting["veto"] and ("mic", True) not in meeting["merge"]


def test_the_collapse_verdict_counts_the_mic_voices(meeting):
    """№573: вердикт слитой метки — по голосам микрофона, а не по общему счёту, который
    с квотой микрофона на звонке доходит до 16 и выпадает из диапазона."""
    _room(meeting, speakers=16)
    meeting["meta"]["speakers_mic"] = 7
    meeting["raw"]["mic"] = [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert rt.MIC_COLLAPSED_NOTE.format(live=7) in text


def test_the_room_keeps_the_collapse_verdict(meeting):
    """Вердикт слитой метки — тот же, что на пути подсказки: одна метка при семи живых."""
    _room(meeting)
    meeting["raw"]["mic"] = [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert rt.MIC_COLLAPSED_NOTE.format(live=7) in text


@pytest.mark.parametrize("call_len,room", [(48, True), (47, False)])
def test_a_call_recording_cut_short_is_not_a_room(meeting, call_len, room):
    """Канал собеседников, умерший посреди встречи, оставляет короткий пустой файл:
    его тишина о комнате не говорит — подсказка, как было (вход №565, r3, Sonnet I2)."""
    _room(meeting)
    meeting["len"]["blackhole"] = call_len                     # микрофон — 60 с, порог 0,8
    rt.rebuild(meeting["live"], CFG)
    assert (("mic", rt.VETO_BELOW) in meeting["veto"]) is room
    assert (("mic", 7) in meeting["calls"]) is not room


@pytest.mark.parametrize("speakers", [None, 2, 13])
def test_the_veto_replaces_only_the_hint(meeting, speakers):
    """Вне ячейки подсказки (живых < 3, > 12 или счёта нет) — авто без запрета, как
    было: монолог при раздробленном трекере запрет раскалывал бы (вход №565, r3)."""
    _room(meeting, speakers or 7)
    if speakers is None:
        del meeting["meta"]["speakers"]
    rt.rebuild(meeting["live"], CFG)
    assert [v for v in meeting["veto"] if v[0] == "mic"] == [("mic", None)]
    assert ("mic", -1) in meeting["calls"]


def test_a_call_that_speaks_gets_no_veto(meeting):
    meeting["meta"] = {"speakers": 7}
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", None) in meeting["veto"] and ("mic", rt.VETO_BELOW) not in meeting["veto"]


def test_a_failed_call_channel_is_not_silence(meeting):
    """Сбой разметки канала собеседников (None) — не тишина: звонок мог быть, живой
    счёт включает его собеседников. Микрофон — в авто, вердикта нет (выход r1)."""
    _room(meeting)
    meeting["raw"]["blackhole"] = None
    meeting["raw"]["mic"] = [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert ("mic", -1) in meeting["calls"] and ("mic", 7) not in meeting["calls"]
    assert "дала одну метку" not in text


def test_a_short_call_recording_is_not_silence(meeting):
    _room(meeting)
    meeting["len"]["blackhole"] = 20
    rt.rebuild(meeting["live"], CFG)
    assert meeting["calls"] == [("mic", -1)]


def test_the_mic_hint_goes_up_to_twelve(meeting):
    _room(meeting, 12, call="absent")
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", 12) in meeting["calls"]


@pytest.mark.parametrize("speakers,verdict", [(12, True), (13, False)])
def test_the_collapse_verdict_trusts_the_live_count_only_in_the_hint_range(meeting, speakers, verdict):
    """Выше диапазона подсказки живому счёту не верим — и вердикт по нему не выносим
    (выход r1, Sonnet I2)."""
    _room(meeting, speakers)
    meeting["raw"]["mic"] = lambda n: [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert ("дала одну метку" in text) is verdict
    assert ("**Анна**" in text) is not verdict


@pytest.mark.parametrize("speakers", [2, 13])
def test_the_mic_gets_no_hint_below_three_or_out_of_range(meeting, speakers):
    _room(meeting, speakers)
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", -1) in meeting["calls"] and ("mic", True) not in meeting["merge"]


def test_the_mic_hint_needs_exactly_three_voices_not_more(meeting):
    _room(meeting, 3, call="absent")
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", 3) in meeting["calls"]


def test_a_call_that_speaks_keeps_the_mic_on_auto(meeting):
    meeting["meta"] = {"speakers": 7}
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", -1) in meeting["calls"] and ("mic", True) not in meeting["merge"]


def test_an_empty_hinted_mic_falls_back_to_auto(meeting, monkeypatch):
    """Подсказка ничего не дала — повтор без неё, а не потеря канала (вход r3, Sonnet I2)."""
    _room(meeting, call="absent")
    auto = [(0.0, 40.0, 3), (45.0, 57.0, 4)]
    meeting["raw"]["mic"] = lambda n: [] if n > 0 else list(auto)
    yields = []
    monkeypatch.setattr(rt, "_yield_to_live", lambda what, cap=None: yields.append((what, cap)))
    out = rt.rebuild(meeting["live"], CFG)
    assert yields.count(("разметка голосов микрофона", 600)) == 2, "повтор — тоже тяжёлая разметка, уступка перед ним"
    assert [c for c in meeting["calls"] if c[0] == "mic"] == [("mic", 7), ("mic", -1)]
    assert out is not None


def test_a_mic_collapsed_into_one_label_gets_no_live_name_and_no_model_name(meeting, monkeypatch):
    """29.09: шестеро под одной меткой, перенос по времени отдал ей имя единственного
    названного живого блока. Слитая метка не получает имени ни от переноса, ни от
    модели; в шапке — строка причины (№559)."""
    _room(meeting)
    meeting["raw"]["mic"] = [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    asked = []
    monkeypatch.setattr(rt, "name_speakers", lambda cfg, lines, **kw: asked.append(lines)
                        or rt.NamesOutcome({lbl: "Борис" for lbl, _ in lines}, rt.NamesOutcome.ANSWERED))
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert "**Анна**" not in text and "**Борис**" not in text and "**Собеседник 1**" in text
    assert not any(lines for lines in asked)
    assert rt.MIC_COLLAPSED_NOTE.format(live=7) in text
    assert "слышала голосов: 7." in text


def test_two_mic_labels_are_not_a_collapse_and_keep_the_live_name(meeting):
    """Очная на двоих, живой трекер насчитал больше — две метки остаются с именами
    (вход r3, Sonnet I1: порог «≤ живых/3» снял бы верные имена)."""
    _room(meeting)
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert "**Анна**" in text and "дала одну метку" not in text


def test_one_mic_label_with_few_live_voices_is_not_a_collapse(meeting):
    _room(meeting, 2)
    meeting["raw"]["mic"] = [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert "**Анна**" in text


def test_the_collapse_verdict_holds_at_exactly_three_live_voices(meeting):
    _room(meeting, 3)
    meeting["raw"]["mic"] = lambda n: [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert "**Анна**" not in text


def test_a_call_with_one_mic_voice_is_not_a_collapse(meeting):
    meeting["meta"] = {"speakers": 7, "names": {"Собеседник 2": "Анна"}}
    meeting["raw"]["mic"] = [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert "дала одну метку" not in text


@pytest.mark.parametrize("value,count,hint", [
    (7, 7, 7), (7.0, 7, 7), (1, 1, None), (2, 2, 2), (12, 12, 12), (13, 13, None),
    (60, 60, None), (61, None, None), (0, None, None), (True, None, None),
    ("7", None, None), ([7], None, None), (7.5, None, None), (None, None, None),
])
def test_live_speakers_are_read_with_their_form_checked(value, count, hint):
    """Число из live.json — целое в своих границах; мусор не роняет пересборку."""
    assert rt.speakers_count({"speakers": value}) == count
    assert rt.speakers_hint({"speakers": value}) == hint
    assert rt.speakers_count({}) is None and rt.speakers_count("битый") is None


def test_a_failing_diarization_is_none_not_an_empty_channel(monkeypatch):
    """Сбой разметки — «не размечали», а не «речи нет»: пустой канал собеседников
    значит очную встречу, сбой так читаться не должен... и не читается как речь."""
    def boom(*a, **k):
        raise RuntimeError("модель не загрузилась")
    monkeypatch.setattr(rt, "diarize", boom)
    assert rt.diarize_channel(np.zeros(16000), 16000) is None


def test_the_log_names_why_the_call_channel_is_silent(meeting, monkeypatch):
    """Журнал различает «канала нет» и «размечен пустым» (выход r1, GLM M4)."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    _room(meeting)
    rt.rebuild(meeting["live"], CFG)
    assert any("канал собеседников размечен пустым" in m for m in said)
    said.clear()
    meeting["calls"].clear()
    del meeting["paths"]["blackhole"]
    rt.rebuild(meeting["live"], CFG)
    assert any("канала собеседников нет" in m for m in said)


@pytest.mark.parametrize("chan,expected", [
    ({"Собеседник 1": "mic"}, {"Собеседник 1"}),
    ({"Собеседник 1": "mic", "Собеседник 2": "bh"}, {"Собеседник 1"}),
    ({"Собеседник 1": "mic", OWNER: "mic"}, {"Собеседник 1"}),
    ({"Собеседник 1": "mic", "Собеседник 2": "mic"}, set()),
    ({OWNER: "mic"}, set()),
])
def test_the_collapse_counts_only_neutral_mic_labels(chan, expected):
    assert rt.collapsed_mic_labels(chan, 7, call_silent=True) == expected
    assert rt.collapsed_mic_labels(chan, 7, call_silent=False) == set()
