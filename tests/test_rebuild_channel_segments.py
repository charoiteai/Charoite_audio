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
import transcript  # noqa: E402

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
    segments, chan = rt.resolve_channel_segments(bh, None, owner_label=OWNER, call=True)
    assert segments == [(0.0, 30.0, "Собеседник 1"), (40.0, 70.0, "Собеседник 2"),
                        (80.0, 110.0, "Собеседник 1")]
    assert chan == {"Собеседник 1": "bh", "Собеседник 2": "bh"}


def test_the_call_dwarf_threshold_is_25_seconds_and_a_parameter():
    """25 с ровно — голос; 24.5 с — осколок. Параметр меняет только канал звонка."""
    bh = [(0.0, 30.0, 1), (31.0, 56.0, 2), (60.0, 84.5, 3)]
    segs, _ = rt.resolve_channel_segments(bh, None, owner_label=OWNER, call=True)
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 2", "Собеседник 2"]
    segs, _ = rt.resolve_channel_segments(bh, None, owner_label=OWNER, call=True, bh_dwarf_s=5.0)
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 2", "Собеседник 3"]


def test_the_mic_dwarf_threshold_is_10_seconds():
    """В микрофоне 10 с ровно — голос, 9.5 с — осколок ближайшего крупного."""
    mic = [(0.0, 40.0, 0), (50.0, 60.0, 1), (70.0, 79.5, 2)]
    segs, chan = rt.resolve_channel_segments(None, mic, owner_label="", call=True)
    assert segs == [(0.0, 40.0, "Собеседник 1"), (50.0, 60.0, "Собеседник 2"),
                    (70.0, 79.5, "Собеседник 2")]
    assert chan == {"Собеседник 1": "mic", "Собеседник 2": "mic"}


def test_the_mic_threshold_does_not_follow_the_call_parameter():
    mic = [(0.0, 40.0, 0), (70.0, 79.5, 2)]
    segs, _ = rt.resolve_channel_segments(None, mic, owner_label="", call=True, bh_dwarf_s=5.0)
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 1"]


@pytest.mark.parametrize("bh", [None, []], ids=["канала нет", "канал молчал"])
def test_without_call_speech_the_owner_is_not_signed_even_with_a_name(bh, monkeypatch):
    """Очная встреча: различать некого, `owner_voices` отвечает пустым множеством."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    segs, chan = rt.resolve_channel_segments(bh, [(0.0, 60.0, 0)], owner_label=OWNER, call=True)
    assert segs == [(0.0, 60.0, "Собеседник 1")]
    assert chan == {"Собеседник 1": "mic"}
    assert said[-1] == "mic: 1 сегментов, голосов 1, владелец: не назначен (не звонок)"


def call_with_two_mic_voices(owner_label):
    bh = [(100.0, 130.0, 0), (140.0, 170.0, 1)]
    mic = [(0.0, 40.0, 3), (45.0, 57.0, 4)]      # два голоса микрофона: 40 и 12 с
    return rt.resolve_channel_segments(bh, mic, owner_label=owner_label, call=True)


def test_in_a_call_every_mic_voice_is_the_owner_and_call_numbering_holds(monkeypatch):
    """№509: правило живой ленты — в звонке все голоса микрофона после эхо-фильтра
    подписываются владельцем, и малый голос (12 с) тоже: цена гибрида, различение —
    слепок голоса №136. Метка владельца номер «Собеседник N» не тратит."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    segs, chan = call_with_two_mic_voices(OWNER)
    assert said[-1] == ("mic: 2 сегментов, голосов 2, "
                        "владелец: все голоса микрофона после эхо-фильтра")
    assert segs == [(100.0, 130.0, "Собеседник 1"), (140.0, 170.0, "Собеседник 2"),
                    (0.0, 40.0, OWNER), (45.0, 57.0, OWNER)]
    assert chan == {"Собеседник 1": "bh", "Собеседник 2": "bh", OWNER: "mic"}


def test_an_empty_owner_label_leaves_the_owner_unsigned(monkeypatch):
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    segs, chan = call_with_two_mic_voices("")
    assert said[-1].endswith("владелец: не назначен (подпись пуста)")
    assert [lbl for *_, lbl in segs] == ["Собеседник 1", "Собеседник 2",
                                         "Собеседник 3", "Собеседник 4"]
    assert chan["Собеседник 3"] == chan["Собеседник 4"] == "mic"


def test_a_call_with_little_mic_speech_stays_neutral_and_says_why(monkeypatch):
    """Порог MIN_MIC_SECONDS — на сумму речи микрофона: 14 с — рано подписывать."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    segs, _ = rt.resolve_channel_segments([(100.0, 130.0, 0)],
                                          [(0.0, 14.0, 3)], owner_label=OWNER, call=True)
    assert segs == [(100.0, 130.0, "Собеседник 1"), (0.0, 14.0, "Собеседник 2")]
    assert said[-1].endswith("владелец: не назначен (речи 14 с < 15)")
    segs, _ = rt.resolve_channel_segments([(100.0, 130.0, 0)],
                                          [(0.0, 15.0, 3)], owner_label=OWNER, call=True)
    assert segs[-1] == (0.0, 15.0, OWNER)


def test_a_mic_segment_covered_more_than_half_by_a_call_segment_is_echo():
    """Накрыт 10 с из 19 — эхо динамиков, выброшен; в микрофоне речи не осталось."""
    segs, chan = rt.resolve_channel_segments([(0.0, 30.0, 0)], [(20.0, 39.0, 1)],
                                             owner_label=OWNER, call=True)
    assert segs == [(0.0, 30.0, "Собеседник 1")]
    assert chan == {"Собеседник 1": "bh"}


def test_a_mic_segment_covered_exactly_half_stays():
    segs, _ = rt.resolve_channel_segments([(0.0, 30.0, 0)], [(20.0, 40.0, 1)],
                                          owner_label=OWNER, call=True)
    assert segs == [(0.0, 30.0, "Собеседник 1"), (20.0, 40.0, OWNER)]


def test_echo_is_judged_against_the_union_of_call_segments():
    """Доля — по объединению отрезков собеседников (№473): микрофонный отрезок,
    накрытый двумя соседними наполовину каждым, — эхо; до №473 он оставался, и с
    Nemotron (мелкие куски) эхо шло в канал владельца (замер 28.09: 69 % против 17.5 %)."""
    bh = [(0.0, 30.0, 0), (30.0, 60.0, 1)]
    segs, _ = rt.resolve_channel_segments(bh, [(20.0, 40.0, 2)], owner_label=OWNER, call=True)
    assert all(lbl != OWNER for *_, lbl in segs)


def test_the_union_threshold_is_still_more_than_half():
    """Два куска по 5 с на 20-секундном отрезке — ровно половина: остаётся."""
    bh = [(0.0, 25.0, 0), (35.0, 60.0, 1)]
    segs, _ = rt.resolve_channel_segments(bh, [(20.0, 40.0, 2)], owner_label=OWNER, call=True)
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
    """Встреча с двумя каналами; разметку отдают заглушки, звук — нули нужной длины.
    Канал собеседников звучит ровным фоном `bh_level` (−40 дБFS), чтобы гейт речи
    видел звонок (№586); очная (`_room`) ставит его в ноль."""
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
             "bh_level": 0.01,
             "raw": {"mic": [(0.0, 40.0, 3), (45.0, 57.0, 4)],
                     "blackhole": [(100.0, 130.0, 0), (140.0, 170.0, 1)]}}

    def load_wav(p):
        label = "mic" if p.name.endswith("_mic.wav") else "blackhole"
        audio = np.zeros(int(state["len"][label] * SR), dtype=np.float32)
        if label == "blackhole":
            audio[:] = state["bh_level"]
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


AUDIO = {"samplerate": SR, "chunk_seconds": 3.0, "overlap_seconds": 0.5, "vad_energy_db": -55}
CFG = {"audio": AUDIO, "sufler": {"user_name": OWNER}}


def test_rebuild_signs_the_owner_from_settings_in_a_call(meeting):
    out = rt.rebuild(meeting["live"], CFG)
    text = out.read_text(encoding="utf-8")
    assert OWNER in text and "Собеседник 1" in text and "Собеседник 2" in text
    assert "Собеседник 3" not in text, "в звонке оба голоса микрофона — владелец (№509)"


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
    assert note + " | безымянные: Собеседник 1, Собеседник 2" in text.splitlines()
    assert transcript.read_names_pending(text).pending is True


def test_a_live_session_name_leaves_one_label_in_the_banner_tail(meeting, monkeypatch):
    """Имя живой сессии забирает одну из двух нейтральных меток до разбора:
    в хвосте плашки остаётся вторая, литералом."""
    monkeypatch.setattr(rt, "name_speakers",
                        lambda cfg, lines, **kw: rt.NamesOutcome({}, rt.NamesOutcome.SILENT))
    meeting["meta"] = {"names": {"живой": "Анна"}}
    meeting["live"].write_text("**Анна** [14:31]:\nпривет\n", encoding="utf-8")
    out = rt.rebuild(meeting["live"], CFG)
    text = out.read_text(encoding="utf-8")
    assert rt.NAMES_PENDING_NOTE + " | безымянные: Собеседник 2" in text.splitlines()
    assert "Собеседник 1" not in text



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
    assert transcript.read_names_pending(out.read_text(encoding="utf-8")).pending is False


# потолок — места одного канала трекера (№573): до квоты микрофона счёт сам не превышал 8
@pytest.mark.parametrize("speakers,expected", [(0, -1), (1, -1), (2, 2), (8, 8), (12, 8), (13, 8), (61, -1)])
def test_rebuild_hints_the_call_channel_with_the_live_count_in_range(meeting, speakers, expected):
    meeting["meta"] = {"speakers": speakers}
    rt.rebuild(meeting["live"], CFG)
    assert ("blackhole", expected) in meeting["calls"]
    assert ("mic", -1) in meeting["calls"]


@pytest.mark.parametrize("meta,expected", [
    ({"speakers": 2, "speakers_call": 1}, 2),     # один на один: как до №573, не авто-режим
    ({"speakers": 13, "speakers_call": 8}, 8),    # квота микрофона: общий счёт — до потолка мест канала
    ({"speakers": 16, "speakers_call": 3}, 8),
    ({"speakers": 5}, 5),                         # старый сайдкар
    ({"speakers": 5, "speakers_call": 0}, 5),     # счёт канала подсказку не задаёт (финальный Opus по №573)
])
def test_the_call_channel_hint_is_the_live_count_capped_at_one_channels_slots(meeting, meta, expected):
    """Подсказка каналу собеседников — как до №573 (общий счёт живой сессии), с
    потолком мест одного канала трекера: квота микрофона не меняет её на сумме ≤ 8
    и не выбрасывает большой звонок из HINT_RANGE."""
    meeting["meta"] = meta
    rt.rebuild(meeting["live"], CFG)
    assert ("blackhole", expected) in meeting["calls"]


def test_the_call_hint_cap_is_the_live_tracker_slots():
    import diarize_live
    assert rt.LIVE_MAX_SPEAKERS is diarize_live.LIVE_MAX_SPEAKERS


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
    segs, chan = rt.resolve_channel_segments(bh, None, owner_label=OWNER, call=True, bh_dwarf_s=5.0)
    assert segs == [(0.0, 30.0, "Собеседник 1"), (40.0, 70.0, "Собеседник 2")]
    assert set(chan) == {"Собеседник 1", "Собеседник 2"}


def test_the_call_channel_has_no_overlaps_after_resolve():
    bh = [(0.0, 30.0, 1), (25.0, 60.0, 2)]
    segs, _ = rt.resolve_channel_segments(bh, None, owner_label=OWNER, call=True)
    assert segs == [(0.0, 27.5, "Собеседник 1"), (27.5, 60.0, "Собеседник 2")]


# ------------------------------------------------- движок канала собеседников

@pytest.mark.parametrize("backend", [None, "", "sherpa", " Sherpa "])
def test_sherpa_is_the_default_and_calls_no_engine(backend, monkeypatch, tmp_path):
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: pytest.fail("движок позван при sherpa"))
    cfg = {"sufler": {} if backend is None else {"diarize_backend": backend}}
    assert rt.call_channel_engine(cfg, tmp_path / "bh.wav", 600.0, gate_call=True) == (None, "")
    assert rt.call_channel_engine({}, tmp_path / "bh.wav", 600.0, gate_call=True) == (None, "")


def test_an_unknown_backend_falls_back_with_a_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: pytest.fail("движок позван при неизвестном имени"))
    segs, reason = rt.call_channel_engine({"sufler": {"diarize_backend": "nemotorn"}},
                                          tmp_path / "bh.wav", 600.0, gate_call=True)
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
    segs, reason = rt.call_channel_engine(cfg, tmp_path / "bh.wav", 1200.0, gate_call=True)
    assert (segs, reason) == ([(0.0, 5.0, 0), (6.0, 7.0, 1)], "")     # короче секунды — прочь
    assert said == ["Nemotron (голоса собеседников): 2 сегментов из 3 за 0 с"]
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
                                  tmp_path / "bh.wav", 60.0, gate_call=True) == (None, rt.ENGINE_REFUSED_REASON)
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
                                  tmp_path / "bh.wav", 1234.4, gate_call=True) == (
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
                                          tmp_path / "bh.wav", 1200.0, gate_call=True)
    if degenerate:
        assert (segs, reason) == (None, "Nemotron — один отрезок на всю запись (1200 с)")
    else:
        assert (segs, reason) == (payload, "")


def _nemotron_cfg():
    return {"audio": AUDIO,
            "sufler": {"user_name": OWNER, "diarize_backend": "nemotron", "nemotron_python": "/env/bin/python"}}


def _by_channel(bh, mic, asked=None):
    """Заглушка движка с ответом по каналу: имя файла записи решает, чей ответ."""
    def fake(setting, wav, *, root, timeout):
        channel = "mic" if pathlib.Path(wav).name.endswith("_mic.wav") else "blackhole"
        if asked is not None:
            asked.append((channel, timeout))
        return mic if channel == "mic" else bh
    return fake


def test_rebuild_on_nemotron_does_not_run_sherpa_on_the_call_and_keeps_small_voices(meeting, monkeypatch):
    """Nemotron разметил — sherpa по каналу собеседников не зовётся, порог карликов
    5 с: голос с 6 с речи остаётся отдельным человеком (у sherpa слился бы)."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[(100.0, 130.0, 0), (140.0, 146.0, 1)]),
        fp.Outcome(fp.FAILED, reason="нет весов")))
    out = rt.rebuild(meeting["live"], _nemotron_cfg())
    assert [label for label, _ in meeting["calls"]] == ["mic"]       # микрофону — запасной sherpa
    text = out.read_text(encoding="utf-8")
    assert "Собеседник 2" in text and OWNER in text   # два собеседника, микрофон — владелец (№509)
    assert rt.ENGINE_FALLBACK_NOTE.format(reason=rt.ENGINE_REFUSED_REASON) not in text


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
    answer = fp.Outcome(fp.OK, payload=[(10.0, 30.0, 0)])
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(answer, answer, seen))
    meeting["len"]["blackhole"] = 600
    rt.rebuild(meeting["live"], _nemotron_cfg())
    assert seen == [("blackhole", rt.NEMOTRON_TIMEOUT_S + 60.0), ("mic", rt.NEMOTRON_TIMEOUT_S + 6.0)]


# ------------------------------------------------- движок микрофона звонка (№509)

CALL_BH = fp.Outcome(fp.OK, payload=[(100.0, 130.0, 0), (140.0, 170.0, 1)])


def test_on_a_call_nemotron_labels_the_mic_too_and_sherpa_is_not_run(meeting, monkeypatch):
    """Звонок, канал собеседников разметил Nemotron — микрофон тоже Nemotron:
    sherpa не зовётся ни по одному каналу, все метки микрофона — владелец."""
    asked = []
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        CALL_BH, fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0), (25.0, 40.0, 3)]), asked))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert meeting["calls"] == []
    assert [channel for channel, _ in asked] == ["blackhole", "mic"]
    assert f"**{OWNER}**" in text and "Собеседник 3" not in text
    assert "запасным движком" not in text


def test_a_refused_mic_falls_back_to_sherpa_and_says_so_in_the_header(meeting, monkeypatch):
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        CALL_BH, fp.Outcome(fp.FAILED, reason="не уложился в 66 с")))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert rt.MIC_ENGINE_FALLBACK_NOTE.format(reason=rt.ENGINE_REFUSED_REASON) in text
    assert rt.ENGINE_FALLBACK_NOTE.format(reason=rt.ENGINE_REFUSED_REASON) not in text
    assert "не уложился" not in text                                  # сырой отказ — в журнал (№495)
    assert text.index("Речь микрофона размечена") < text.index("**")  # в шапке, до реплик


def test_an_empty_mic_answer_is_a_refusal_with_its_own_reason(meeting, monkeypatch):
    """Пустой микрофон на звонке — отказ, а не «владелец молчал»: иначе владелец
    пропал бы из стенограммы целиком."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        CALL_BH, fp.Outcome(fp.OK, payload=[(3.0, 3.5, 0)])))       # один осколок < 1 с
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert rt.MIC_ENGINE_FALLBACK_NOTE.format(
        reason="Nemotron — 0 с речи на 60 с записи микрофона, меньше 15 с") in text
    assert f"**{OWNER}**" in text


def test_a_call_mic_lost_by_both_engines_cancels_the_rebuild(meeting, monkeypatch):
    """№622 B1, путь 3: звонок, Nemotron отказал на микрофоне, sherpa упала —
    сбой, а не тишина: финал по одному каналу собеседников потерял бы владельца.
    Пересборка отменена, живая стенограмма цела, причина отката — в журнале.
    Был тестом «шапка молчит, когда запасной не разметил тоже» (выход №509, M1
    Sonnet): до шапки этот случай больше не доходит, случай [] сторожит
    `test_the_mic_note_is_dropped_when_sherpa_finds_no_speech`."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        CALL_BH, fp.Outcome(fp.FAILED, reason="нет весов")))
    meeting["raw"]["mic"] = None
    _assert_channel_lost(rt.rebuild(meeting["live"], _nemotron_cfg()), meeting)
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert _lost_lines(said) == [f"{rt.CHANNEL_LOST_REASON}: микрофон, 60 с записи; "
                                 f"откат движка: {rt.ENGINE_REFUSED_REASON}"]


@pytest.mark.parametrize("payload", [[(0.0, 59.0, 0)], [(0.0, 15.0, 2)]], ids=["монолог", "ровно порог"])
def test_one_mic_segment_is_a_valid_answer(payload, monkeypatch, tmp_path):
    """У микрофона нет правила «один отрезок на всю запись — вырожден»: монолог
    владельца — правда."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=payload))
    assert rt.mic_channel_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 60.0) == (payload, "", False)


def test_a_thin_mic_answer_is_a_refusal(monkeypatch, tmp_path):
    """Секунды речи на часе микрофона звонка — сбой в другой форме: правило
    владельца имени при такой речи не даст, владелец пропал бы молча (выход
    №509, I2 GLM)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=[(0.0, 7.0, 0), (100.0, 107.9, 1)]))
    assert rt.mic_channel_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 3600.0) == (
        None, "Nemotron — 15 с речи на 3600 с записи микрофона, меньше 15 с", True)


def test_when_the_call_channel_fell_back_the_mic_stays_on_sherpa(meeting, monkeypatch):
    """Канал собеседников откатился на sherpa — микрофон Nemotron не спрашивают:
    второй отказ того же движка стоил бы ещё одного потолка ожидания."""
    asked = []
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.FAILED, reason="нет весов"), fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0)]), asked))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [channel for channel, _ in asked] == ["blackhole"]
    assert [label for label, _ in meeting["calls"]] == ["blackhole", "mic"]
    assert "Речь микрофона размечена" not in text


def test_without_a_call_recording_the_mic_stays_on_sherpa(meeting, monkeypatch):
    """Записи канала собеседников нет — Nemotron по нему не отвечал, комнату от
    звонка в наушниках не отличить: микрофон на sherpa с подсказкой живой сессии
    и склейкой после (№559), Nemotron не зовётся."""
    asked = []
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]), fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0)]), asked))
    _room(meeting, call="absent")
    rt.rebuild(meeting["live"], _nemotron_cfg())
    assert asked == []
    assert ("mic", 7) in meeting["calls"] and ("mic", True) in meeting["merge"]


def test_a_room_mic_goes_to_nemotron_and_sherpa_is_not_run(meeting, monkeypatch):
    """№596: канал собеседников размечен Nemotron пустым — очная: микрофон
    размечает Nemotron, его слоты — люди комнаты; sherpa не зовётся ни по одному
    каналу, шапка молчит, журнал называет движок, метки и живой счёт."""
    asked, said = [], []
    monkeypatch.setattr(rt, "log", said.append)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]),
        fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0), (25.0, 40.0, 1), (42.0, 55.0, 2)]), asked))
    _room(meeting)
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [channel for channel, _ in asked] == ["blackhole", "mic"]
    assert meeting["calls"] == []
    assert "запасным движком" not in text and OWNER not in text
    assert "mic комнаты: разметил Nemotron, меток 3, живой счёт 7" in said


def test_a_refused_call_channel_keeps_the_room_mic_off_nemotron(meeting, monkeypatch):
    """Вход №596, r1 GLM C1: Nemotron отказал на канале собеседников — микрофон
    комнаты его не спрашивает (второй отказ стоил бы второго потолка), sherpa
    идёт веткой комнаты под запретом склейки."""
    asked = []
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.FAILED, reason="нет весов"), fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0)]), asked))
    _room(meeting)
    rt.rebuild(meeting["live"], _nemotron_cfg())
    assert [channel for channel, _ in asked] == ["blackhole"]
    assert ("mic", rt.VETO_BELOW) in meeting["veto"]


@pytest.mark.parametrize("mic_answer, yields", [
    (fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0)]), 1),
    (fp.Outcome(fp.FAILED, reason="не уложился"), 2),
], ids=["ответил", "отказ"])
def test_the_room_mic_yields_again_only_after_a_nemotron_refusal(mic_answer, yields, meeting, monkeypatch):
    """Вход №596, r2 GLM I1: уступка перед sherpa — после отказа любого из двух
    движков микрофона, а не только звонкового."""
    seen = []
    monkeypatch.setattr(rt, "_yield_to_live", lambda what, cap=None: seen.append(what))
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]), mic_answer))
    _room(meeting)
    rt.rebuild(meeting["live"], _nemotron_cfg())
    assert seen.count("разметка голосов микрофона") == yields


def _slots(n, fragment_last=False):
    """Ответ движка с n слотами по 5 с; последний — одни осколки < 1 с."""
    out = [(10.0 * k, 10.0 * k + 5.0, k) for k in range(n)]
    if fragment_last:
        out[-1] = (10.0 * (n - 1), 10.0 * (n - 1) + 0.5, n - 1)
    return out


@pytest.mark.parametrize("live", [3, 7, 12, 13, 60])
@pytest.mark.parametrize("fragment_last", [False, True], ids=["целые", "осколки"])
def test_all_eight_slots_with_a_live_count_is_a_refusal(live, fragment_last, monkeypatch, tmp_path):
    """Все слоты модели заняты, а живая сессия насчитала людей — людей могло быть
    больше, чем модель разводит: отказ на sherpa. Слоты — по сырому ответу:
    слот из одних осколков < 1 с тоже занят (вход №596, r4 GLM I3)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    said: list[str] = []
    monkeypatch.setattr(rt, "log", said.append)
    payload = _slots(rt.diarize_nemotron.MAX_SLOTS, fragment_last)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=payload))
    assert rt.room_mic_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 120.0, live) == (
        None, "Nemotron — заняты все 8 слотов движка")
    assert any(f"заняты все 8 слотов при живом счёте {live}" in line for line in said), said


@pytest.mark.parametrize("payload,live", [
    (_slots(8), None),                 # живого счёта нет — ответ как есть
    (_slots(8), 2),                    # живых меньше MIC_HINT_MIN — монолог, не толпа
    (_slots(7), 7),                    # слоты не исчерпаны
], ids=["без счёта", "живых 2", "7 слотов"])
def test_slots_below_the_ceiling_or_without_a_live_count_are_an_answer(payload, live, monkeypatch, tmp_path):
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=payload))
    assert rt.room_mic_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 120.0, live) == (payload, "")


@pytest.mark.parametrize("call_len,plan", [(120, ("veto", rt.VETO_BELOW)), (60, ("calls", 7))],
                         ids=["комната", "канал умер посреди встречи"])
def test_all_slots_in_a_room_send_the_mic_to_sherpa_by_its_plan(call_len, plan, meeting, monkeypatch):
    """Потолок стоит в обеих ячейках живого счёта `mic_plan`: sherpa идёт по своему
    плану — запрет склейки или подсказка (вход №596, r2 GLM C1)."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]), fp.Outcome(fp.OK, payload=_slots(8))))
    _room(meeting)
    meeting["len"].update(mic=120, blackhole=call_len)
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert ("mic", plan[1]) in meeting[plan[0]]
    assert rt.MIC_ENGINE_FALLBACK_NOTE.format(reason="Nemotron — заняты все 8 слотов движка") in text


def test_all_slots_with_a_live_count_above_the_sherpa_hint_still_go_to_sherpa(meeting, monkeypatch):
    """Живых 15 — выше верхней границы подсказки sherpa (HINT_RANGE), а потолок
    модели от неё не зависит: людей больше, чем слотов, — sherpa в авто
    (выход №596, r1 GLM I2)."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]), fp.Outcome(fp.OK, payload=_slots(8))))
    _room(meeting, speakers=15)
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert rt.MIC_ENGINE_FALLBACK_NOTE.format(reason="Nemotron — заняты все 8 слотов движка") in text


@pytest.mark.parametrize("payload,reason", [
    ([], "Nemotron — 0 с речи на 60 с записи микрофона, меньше 15 с"),
    ([(0.0, 10.0, 0), (2.0, 10.0, 1)], "Nemotron — 10 с речи на 60 с записи микрофона, меньше 15 с"),
    ([(0.0, 59.0, 0)], "Nemotron — один отрезок на всю запись (60 с)"),
    ([(0.0, rt.DEGENERATE_SHARE * 60.0, 0)], "Nemotron — один отрезок на всю запись (60 с)"),
], ids=["пусто", "перекрытия один раз", "один на всю", "один на долю ровно"])
def test_a_room_answer_below_the_floor_or_degenerate_is_a_refusal(payload, reason, monkeypatch, tmp_path):
    """Пол речи — по объединению: перекрытие отрезков считается один раз (сырая
    сумма 18 с, речи 10 с). Пустой ответ — частный случай пола. Один отрезок на
    почти всю запись — вырожденный ответ, как у канала собеседников."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=payload))
    assert rt.room_mic_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 60.0, 7) == (None, reason)


def test_one_room_segment_short_of_the_share_is_an_answer(monkeypatch, tmp_path):
    """Один отрезок на 49 с из 60 — меньше DEGENERATE_SHARE записи: речь одного
    человека, а не вырожденный ответ (мутант CI по #722)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    payload = [(10.0, 59.0, 0)]
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=payload))
    assert rt.room_mic_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 60.0, 7) == (payload, "")


def test_the_room_mic_is_judged_against_the_length_of_its_recording(meeting, monkeypatch):
    """Вырожденный ответ судится по длине записи микрофона (120 с): один отрезок
    на 119 с — отказ на sherpa, в шапке длина записи (мутант CI по #722)."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]), fp.Outcome(fp.OK, payload=[(0.0, 119.0, 0)])))
    _room(meeting)
    meeting["len"].update(mic=120, blackhole=120)
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert rt.MIC_ENGINE_FALLBACK_NOTE.format(
        reason="Nemotron — один отрезок на всю запись (120 с)") in text


def test_a_call_mic_writes_no_room_line(meeting, monkeypatch):
    """Строка «mic комнаты» — только очной встрече; на звонке её нет (мутант CI
    по #722). Ответ Nemotron по микрофону принят — иначе строки не было бы и у
    мутанта; парный положительный тест — разметка комнаты Nemotron."""
    asked, said = [], []
    monkeypatch.setattr(rt, "log", said.append)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        CALL_BH, fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0)]), asked))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [channel for channel, _ in asked] == ["blackhole", "mic"]
    assert meeting["calls"] == [] and "запасным движком" not in text
    assert not any(line.startswith("mic комнаты") for line in said)


def test_the_call_mic_floor_counts_overlaps_once(monkeypatch, tmp_path):
    """Вход №596, r4 Sonnet M1: сырая сумма 16 с ≥ 15, по объединению 14 с — отказ;
    после `disjoint` правило владельца увидело бы те же 14 с."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=[(0.0, 8.0, 0), (6.0, 14.0, 0)]))
    assert rt.mic_channel_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 3600.0) == (
        None, "Nemotron — 14 с речи на 3600 с записи микрофона, меньше 15 с", True)


@pytest.mark.parametrize("segs,expected", [
    ([], 0.0),
    ([(0.0, 10.0, 0)], 10.0),
    ([(0.0, 10.0, 0), (5.0, 12.0, 1)], 12.0),
    ([(5.0, 12.0, 1), (0.0, 10.0, 0)], 12.0),        # порядок не важен
    ([(0.0, 10.0, 0), (2.0, 3.0, 1)], 10.0),         # вложенный
    ([(0.0, 2.0, 0), (2.0, 4.0, 1), (6.0, 7.0, 0)], 5.0),   # стык и разрыв
])
def test_speech_seconds_is_the_union(segs, expected):
    assert rt.speech_seconds(segs) == pytest.approx(expected)


def test_speech_seconds_matches_disjoint_on_random_layouts():
    """Объединение — та же речь, что после `disjoint`, по которой судит владельца
    `owner_voices`: пол движка и порог владельца не расходятся на перекрытиях."""
    rng = random.Random(596)
    for _ in range(2000):
        segs = []
        for _ in range(rng.randint(0, 10)):
            s = round(rng.uniform(0, 60), 2)
            segs.append((s, round(s + rng.uniform(0.1, 15), 2), rng.randint(0, 3)))
        assert rt.speech_seconds(segs) == pytest.approx(
            sum(e - s for s, e, _ in rt.disjoint(segs))), segs


def test_a_dwarf_is_judged_after_overlaps_are_removed():
    """Карлик судится по речи без перекрытий: два отрезка метки внахлёст — 12 с
    сырой суммой, 8 с речи — это карлик, он сливается с соседом. Карлики до
    `disjoint` оставили бы метку отдельным человеком (выход №596, r1 Sonnet M2)."""
    raw = [(0.0, 6.0, 1), (2.0, 8.0, 1), (10.0, 60.0, 0)]
    segs, chan = rt.resolve_channel_segments([], raw, owner_label="", call=False)
    assert {lbl for *_, lbl in segs} == {"Собеседник 1"}
    assert chan == {"Собеседник 1": "mic"}


def test_overlapping_room_labels_reach_stt_once():
    """№584 / №596: в комнате перекрытие двух меток микрофона давало два STT
    одного звука — после `disjoint` отрезки меток не перекрываются."""
    mic = [(0.0, 30.0, 0), (20.0, 50.0, 1)]
    segs, chan = rt.resolve_channel_segments([], mic, owner_label=OWNER, call=False)
    assert segs == [(0.0, 25.0, "Собеседник 1"), (25.0, 50.0, "Собеседник 2")]
    assert set(chan.values()) == {"mic"}


def test_overlaps_of_the_owner_on_a_call_still_make_one_paragraph():
    """На звонке все метки микрофона — владелец: `disjoint` режет перекрытие, а
    `paragraphs` склеивает куски обратно — абзац тот же, что до №596."""
    bh = [(100.0, 130.0, 0)]
    mic = [(0.0, 30.0, 0), (20.0, 50.0, 1)]
    segs, _ = rt.resolve_channel_segments(bh, mic, owner_label=OWNER, call=True)
    assert [p[:3] for p in rt.paragraphs(segs) if p[2] == OWNER] == [[0.0, 50.0, OWNER]]


def test_the_mic_note_is_dropped_when_sherpa_finds_no_speech(meeting, monkeypatch):
    """Вход №596, r4 Sonnet M2: Nemotron отказал, sherpa вернула пустую разметку —
    шапка не говорит «размечено запасным движком»."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        CALL_BH, fp.Outcome(fp.FAILED, reason="нет весов")))
    meeting["raw"]["mic"] = []
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert "Речь микрофона размечена" not in text


def test_a_nemotron_refusal_on_the_call_yields_again_before_sherpa(meeting, monkeypatch):
    """Отказ Nemotron мог съесть его потолок: перед sherpa канала собеседников —
    свежая уступка встрече (выход №509, I1 GLM)."""
    order = []
    monkeypatch.setattr(rt, "_yield_to_live", lambda what, cap=None: order.append(("yield", what, cap)))
    def fail(setting, wav, *, root, timeout):
        order.append(("nemotron", pathlib.Path(wav).name))
        return fp.Outcome(fp.FAILED, reason="не уложился")
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", fail)
    rt.rebuild(meeting["live"], _nemotron_cfg())
    at = order.index(("nemotron", "2026-08-20_143000_blackhole.wav"))
    assert order[at + 1] == ("yield", "разметка голосов собеседников", 600)   # с потолком: очередь не паркуется


@pytest.mark.parametrize("mic_answer, yields", [
    (fp.Outcome(fp.OK, payload=[(0.0, 20.0, 0)]), 1),
    (fp.Outcome(fp.FAILED, reason="не уложился"), 2),
], ids=["ответил", "отказ"])
def test_the_mic_yields_again_only_after_a_nemotron_refusal(mic_answer, yields, meeting, monkeypatch):
    """Перед sherpa после отказа Nemotron микрофона — вторая уступка; ответ
    Nemotron второй уступки не стоит (мутант CI по #718)."""
    seen = []
    monkeypatch.setattr(rt, "_yield_to_live", lambda what, cap=None: seen.append(what))
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(CALL_BH, mic_answer))
    rt.rebuild(meeting["live"], _nemotron_cfg())
    assert seen.count("разметка голосов микрофона") == yields


@pytest.mark.parametrize("seed", range(40))
def test_resolve_keeps_a_non_empty_call_channel_non_empty(seed):
    """Инвариант, на котором движок микрофона и правило владельца видят один
    признак звонка: `bool(bh_raw) == bool(bh_segs)` — раздел перекрытий и склейка
    карликов непустое не опустошают (выход №509, M3 Sonnet)."""
    rng = random.Random(seed)
    bh = []
    for _ in range(rng.randint(1, 8)):
        s = rng.uniform(0, 100)
        bh.append((s, s + rng.uniform(1.0, 30.0), rng.randint(0, 4)))
    for dwarf in (rt.BH_DWARF_S, rt.NEMOTRON_BH_DWARF_S):
        segs, _ = rt.resolve_channel_segments(bh, None, owner_label=OWNER, call=True, bh_dwarf_s=dwarf)
        assert segs, (bh, dwarf)


# ---------------------------------------------- очная встреча: микрофон без звонка (№559)

ROOM = {"speakers": 7, "names": {"Собеседник 2": "Анна"}}
LIVE_ROOM = "# Встреча\n\n**Анна** [14:30]:\nпривет\n\n**Собеседник 5** [14:30]:\nага\n"


def _room(meeting, speakers=7, call="silent"):
    """Канал собеседников молчит. call="silent" — запись есть и размечена пустой
    (комната, №565); "absent" — записи нет вовсе (подсказка, №559)."""
    meeting["meta"] = dict(ROOM, speakers=speakers)
    meeting["raw"]["blackhole"] = []                       # канал собеседников размечен пустым
    meeting["bh_level"] = 0.0                              # и гейт речи в нём ничего не слышит
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
    """Сбой разметки длинного канала собеседников (None) — не тишина и не комната:
    пересборка отменена до микрофона (№622 B1, путь 1). Прежде здесь микрофон
    уходил в авто без вердикта (выход r1 №559); «короткая запись — не тишина»
    сторожит `test_a_short_call_recording_is_not_silence`."""
    _room(meeting)
    meeting["raw"]["blackhole"] = None
    meeting["raw"]["mic"] = [(0.0, 40.0, 3), (45.0, 57.0, 3)]
    _assert_channel_lost(rt.rebuild(meeting["live"], CFG), meeting, LIVE_ROOM)
    assert meeting["calls"] == [("blackhole", 7)]


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


# ------------------------------------- признак звонка по гейту речи (№586, №587)

def test_segments_of_the_call_channel_without_the_gate_call_do_not_sign_the_owner():
    """Отрезки собеседников есть, а порога гейта нет («дзынь» на очной, ролик
    короче порога) — метки микрофона нейтральные (№586)."""
    segs, _ = rt.resolve_channel_segments([(100.0, 105.0, 0)], [(0.0, 40.0, 3)],
                                          owner_label=OWNER, call=False)
    assert segs == [(100.0, 105.0, "Собеседник 1"), (0.0, 40.0, "Собеседник 2")]


def test_the_gate_call_without_segments_signs_nobody():
    """Порог гейта взят, а разметки канала нет (bh_raw=None) — эхо-фильтру не по
    чему резать: нейтральные, как до №586."""
    segs, _ = rt.resolve_channel_segments(None, [(0.0, 40.0, 3)], owner_label=OWNER, call=True)
    assert segs == [(0.0, 40.0, "Собеседник 1")]


def test_an_empty_nemotron_answer_on_a_silent_channel_is_an_empty_channel(monkeypatch, tmp_path):
    """№587: пусто, и гейт звонка не слышал — канал размечен пустым, без отката."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.OK, payload=[]))
    assert rt.call_channel_engine({"sufler": {"diarize_backend": "nemotron"}},
                                  tmp_path / "bh.wav", 1234.4, gate_call=False) == ([], "")


def test_a_short_beep_in_the_call_channel_does_not_sign_the_room_mic(meeting):
    """№586, главный случай в пересборке: на очной в канале собеседников 5 с
    звука (уведомление), разметка дала по нему отрезок — звонком это не
    становится, микрофон остаётся нейтральным."""
    meeting["bh_level"] = 0.0
    meeting["raw"]["blackhole"] = [(30.0, 35.0, 0)]
    text = rt.rebuild(meeting["live"], CFG).read_text(encoding="utf-8")
    assert OWNER not in text
    assert "Собеседник 2" in text and "Собеседник 3" in text


def test_a_room_on_nemotron_runs_no_sherpa_on_the_call_channel(meeting, monkeypatch):
    """№587: очная, движок Nemotron, канал собеседников тихий — пустой ответ
    принимается: sherpa по каналу не зовётся, строки про канал собеседников нет.
    Микрофон отказал — sherpa веткой комнаты (№565), и шапка говорит почему (№596)."""
    _room(meeting)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]), fp.Outcome(fp.FAILED, reason="нет весов")))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert "Голоса собеседников размечены" not in text
    assert rt.MIC_ENGINE_FALLBACK_NOTE.format(reason=rt.ENGINE_REFUSED_REASON) in text
    assert ("mic", rt.VETO_BELOW) in meeting["veto"]             # комната, как при sherpa (№565)


def test_an_empty_nemotron_answer_on_a_loud_channel_is_still_a_refusal(meeting, monkeypatch):
    """Гейт слышал звонок, а Nemotron — ни одного отрезка: отказ в другой форме,
    размечает sherpa, шапка говорит почему (как до №587)."""
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[]), fp.Outcome(fp.FAILED, reason="нет весов")))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert "blackhole" in [label for label, _ in meeting["calls"]]
    assert "Nemotron — ни одного отрезка на 60 с записи" in text


def test_a_recording_shorter_than_twenty_seconds_with_sound_signs_nobody(meeting, monkeypatch):
    """Вход №586, круг 2 (M3): канал собеседников 15 с со звуком — движок не
    зовётся, отрезков нет; метки микрофона нейтральные, шапка молчит."""
    meeting["len"]["blackhole"] = 15
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[(0.0, 5.0, 0)]), fp.Outcome(fp.FAILED, reason="нет весов")))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert OWNER not in text and "запасным движком" not in text


def test_a_short_beep_on_sherpa_keeps_the_room_mic_under_the_veto(meeting):
    """Выход №586 (обе головы): отрезок от «дзынь» без порога гейта — комната и
    для плана микрофона, не только для подписи: авто под запретом склейки."""
    _room(meeting)
    meeting["raw"]["blackhole"] = [(30.0, 35.0, 0)]
    rt.rebuild(meeting["live"], CFG)
    assert ("mic", rt.VETO_BELOW) in meeting["veto"]


def test_a_short_beep_on_nemotron_is_a_room_for_the_mic(meeting, monkeypatch):
    """Выход №586 (обе головы): Nemotron дал отрезок по «дзынь» на очной — порога
    гейта нет, значит не звонок: микрофон идёт веткой комнаты — Nemotron комнаты
    (№596), а не звонковый, и владельца никто не подписывает."""
    _room(meeting)
    asked: list = []
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[(30.0, 35.0, 0)]),
        fp.Outcome(fp.OK, payload=[(0.0, 50.0, 0)]), asked))
    text = rt.rebuild(meeting["live"], _nemotron_cfg()).read_text(encoding="utf-8")
    assert [channel for channel, _ in asked] == ["blackhole", "mic"]
    assert meeting["calls"] == []
    assert OWNER not in text


# ------------------------------------- гейт потерянного канала (№622 B1)

def _assert_channel_lost(out, meeting, live_text="живая стенограмма"):
    """Отмена: устранимый отказ с кодом потерянного канала, живая стенограмма цела."""
    assert isinstance(out, rt.RebuildSkipped) and out.reason == rt.RebuildSkipped.CHANNEL_LOST
    assert bool(out) is False
    assert meeting["live"].read_text(encoding="utf-8") == live_text
    assert sorted(p.name for p in meeting["live"].parent.iterdir()) == [meeting["live"].name]


def _lost_lines(said):
    return [line for line in said if line.startswith(rt.CHANNEL_LOST_REASON)]


def test_a_lost_call_channel_cancels_the_rebuild_before_the_mic(meeting, monkeypatch):
    """Путь 1: sherpa упала на длинном канале собеседников, микрофон размечался бы
    — финал по одному микрофону потерял бы собеседников. Отмена до микрофона:
    на часе записи это минуты sherpa впустую."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    meeting["raw"]["blackhole"] = None
    _assert_channel_lost(rt.rebuild(meeting["live"], CFG), meeting)
    assert [label for label, _ in meeting["calls"]] == ["blackhole"]
    assert _lost_lines(said) == [f"{rt.CHANNEL_LOST_REASON}: канал собеседников, 60 с записи"]


def test_a_lost_call_channel_after_a_nemotron_refusal_names_the_fallback(meeting, monkeypatch):
    """Путь 1, второй вариант: отказ Nemotron, затем сбой sherpa — в строке
    журнала причина отката движка, без сырого отказа с путями (№495)."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", lambda *a, **k: fp.Outcome(
        fp.FAILED, reason="нет весов"))
    meeting["raw"]["blackhole"] = None
    _assert_channel_lost(rt.rebuild(meeting["live"], _nemotron_cfg()), meeting)
    assert [label for label, _ in meeting["calls"]] == ["blackhole"]
    assert _lost_lines(said) == [f"{rt.CHANNEL_LOST_REASON}: канал собеседников, 60 с записи; "
                                 f"откат движка: {rt.ENGINE_REFUSED_REASON}"]


def test_a_thin_call_mic_and_a_failed_sherpa_write_the_final_without_the_owner(meeting, monkeypatch):
    """Путь 2: Nemotron ответил на микрофоне звонка, но почти тишиной, sherpa
    упала. Ответ движка есть — микрофон размечен пустым, финал по каналу
    собеседников, как до гейта; шапка не говорит «размечено запасным движком»."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        CALL_BH, fp.Outcome(fp.OK, payload=[(0.0, 5.0, 0)])))
    meeting["raw"]["mic"] = None
    out = rt.rebuild(meeting["live"], _nemotron_cfg())
    assert isinstance(out, pathlib.Path)
    text = out.read_text(encoding="utf-8")
    assert "Собеседник 1" in text and "Собеседник 2" in text
    assert f"**{OWNER}**" not in text
    assert "Речь микрофона размечена" not in text
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert _lost_lines(said) == []


def test_a_lost_room_mic_cancels_the_rebuild(meeting, monkeypatch):
    """Путь 4: комната — Nemotron разметил канал собеседников, гейт звонка молчит.
    Nemotron на микрофоне отвергнут тонким ответом, sherpa упала: отрезки
    микрофона здесь — вся стенограмма, исключения пути 2 у комнаты нет."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env", _by_channel(
        fp.Outcome(fp.OK, payload=[(100.0, 130.0, 0)]), fp.Outcome(fp.OK, payload=[(0.0, 5.0, 0)])))
    _room(meeting)
    meeting["raw"]["mic"] = None
    _assert_channel_lost(rt.rebuild(meeting["live"], _nemotron_cfg()), meeting, LIVE_ROOM)
    assert [label for label, _ in meeting["calls"]] == ["mic"]
    assert _lost_lines(said) == [
        f"{rt.CHANNEL_LOST_REASON}: микрофон, 60 с записи; откат движка: "
        "Nemotron — 5 с речи на 60 с записи микрофона, меньше 15 с"]


def test_a_lost_mic_on_sherpa_cancels_the_rebuild(meeting, monkeypatch):
    """Путь 6: движок sherpa, канал собеседников размечен, микрофон упал —
    финал по одним собеседникам потерял бы владельца."""
    said = []
    monkeypatch.setattr(rt, "log", said.append)
    meeting["raw"]["mic"] = None
    _assert_channel_lost(rt.rebuild(meeting["live"], CFG), meeting)
    assert [label for label, _ in meeting["calls"]] == ["blackhole", "mic"]
    assert _lost_lines(said) == [f"{rt.CHANNEL_LOST_REASON}: микрофон, 60 с записи"]


@pytest.mark.parametrize("channel", ["mic", "blackhole"])
def test_a_channel_of_exactly_the_threshold_is_not_lost(channel, meeting):
    """Ровно MIN_DIARIZE_S — канал не размечается, его None — не сбой: пересборка
    идёт по второму каналу, как на базе."""
    meeting["len"][channel] = rt.MIN_DIARIZE_S
    meeting["raw"][channel] = None
    out = rt.rebuild(meeting["live"], CFG)
    assert isinstance(out, pathlib.Path)
    assert channel not in [label for label, _ in meeting["calls"]]


@pytest.mark.parametrize("channel", ["mic", "blackhole"])
def test_a_channel_one_second_over_the_threshold_is_lost(channel, meeting):
    meeting["len"][channel] = rt.MIN_DIARIZE_S + 1
    meeting["raw"][channel] = None
    out = rt.rebuild(meeting["live"], CFG)
    assert out == rt.RebuildSkipped(rt.RebuildSkipped.CHANNEL_LOST)
    _assert_channel_lost(out, meeting)


def test_a_nemotron_refusal_on_the_mic_is_not_thin(monkeypatch, tmp_path):
    """Третье значение `mic_channel_engine` — True только на тонком ответе; отказ
    движка — False (годный ответ — `test_one_mic_segment_is_a_valid_answer`)."""
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(rt.diarize_nemotron, "diarize_in_env",
                        lambda *a, **k: fp.Outcome(fp.FAILED, reason="нет весов"))
    assert rt.mic_channel_engine(_nemotron_cfg(), tmp_path / "x_mic.wav", 60.0) == (
        None, rt.ENGINE_REFUSED_REASON, False)


def test_main_marks_a_lost_channel_in_the_status(meeting, monkeypatch):
    """Настоящий гейт до статуса: длинный канал собеседников не размечен — встреча
    готова (`ready`) с кодом `RebuildSkipped.CHANNEL_LOST`, машинного финала нет.
    Подмены `main()` — как у тестов №500 A (`test_rebuild_skipped.py`)."""
    import subprocess
    import yaml
    root = meeting["live"].parent.parent
    (root / "config").mkdir()
    (root / "config" / "config.yaml").write_text(
        yaml.safe_dump({"audio": AUDIO, "sufler": {"user_name": OWNER, "graph": False}},
                       allow_unicode=True), encoding="utf-8")
    meeting["raw"]["blackhole"] = None
    calls = []

    class Status:
        def __init__(self, *a, **k):
            pass

        def processing(self, *a):
            pass

        def ready(self, *a):
            calls.append(("ready", *a))

        def failed(self, *a):
            calls.append(("failed", *a))

        def has_transcript(self, _live):
            return True

    monkeypatch.setattr(sys, "argv", ["rebuild_transcript.py", str(meeting["live"])])
    monkeypatch.setattr(rt, "MeetingStatusStore", Status)
    monkeypatch.setattr(rt, "_take_rebuild_queue", lambda: None)
    monkeypatch.setattr(rt, "_yield_to_live", lambda *a, **k: None)
    monkeypatch.setattr(rt, "log", lambda m: None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: subprocess.CompletedProcess(argv, 0))
    monkeypatch.setenv("CHAROITE_NO_RETRY", "1")
    rt.main()
    assert calls == [("ready", meeting["live"], None, rt.RebuildSkipped.CHANNEL_LOST)]
    assert meeting["calls"] == [("blackhole", -1)]
