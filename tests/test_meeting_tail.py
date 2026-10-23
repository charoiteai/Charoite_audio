"""Конец встречи по звонку (№733): что записано после него, в стенограмму не идёт.

Правило и отказы — `meeting_tail.split`; здесь каждое условие отказа и
отрицательный случай: ложный конец посреди встречи не режется."""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import meeting_tail  # noqa: E402

OWNER = "Владелец"
CHAN = {OWNER: "mic", "Собеседник 1": "bh", "Собеседник 2": "bh", "Собеседник 3": "mic"}


def split(segments, chan=CHAN, **kw):
    kw.setdefault("call", True)
    return meeting_tail.split(segments, chan, **kw)


def test_owner_speech_after_the_last_call_segment_goes_after():
    segs = [(0.0, 10.0, "Собеседник 1"), (11.0, 20.0, OWNER), (21.0, 40.0, "Собеседник 1"),
            (43.0, 50.0, OWNER), (52.0, 60.0, "Собеседник 3")]
    out = split(segs)
    assert out.end == 40.0
    assert out.meeting == segs[:3]
    assert out.after == segs[3:], "владелец и голос комнаты после конца звонка — запись после"


def test_answer_started_over_the_last_words_stays_whole_in_the_meeting():
    """Отрезок, начатый до конца звонка, не режется посреди слова: остаётся во встрече."""
    segs = [(0.0, 30.0, "Собеседник 1"), (29.0, 33.0, OWNER), (36.0, 50.0, OWNER)]
    out = split(segs)
    assert out.meeting == segs[:2] and out.after == segs[2:]


def test_a_long_silence_of_the_call_in_the_middle_is_not_an_end():
    """Отрицательный: собеседники молчат 13 минут посреди встречи, потом говорят —
    конец звонка по последнему отрезку, середина встречи цела."""
    segs = [(0.0, 60.0, "Собеседник 1"), (60.0, 840.0, OWNER), (840.0, 900.0, "Собеседник 2"),
            (901.0, 905.0, OWNER)]
    out = split(segs)
    assert out.end == 900.0 and out.meeting == segs[:3] and out.after == segs[3:]


def test_no_mic_after_the_call_gives_no_after_part():
    segs = [(0.0, 10.0, OWNER), (5.0, 30.0, "Собеседник 1")]
    out = split(segs)
    assert out.after == [] and out.meeting == segs and out.end is None


def test_room_meeting_has_no_after_part():
    segs = [(0.0, 10.0, OWNER), (20.0, 30.0, "Собеседник 3")]
    out = split(segs, call=False)
    assert out.after == [] and out.why == "не звонок"


def test_doubt_about_the_call_channel_keeps_everything_in_the_meeting():
    segs = [(0.0, 10.0, "Собеседник 1"), (20.0, 30.0, OWNER)]
    out = split(segs, doubt="канал собеседников пропал и не вернулся до конца")
    assert out.after == [] and out.meeting == segs and "пропал" in out.why


def test_relabeled_voice_from_a_dead_channel_extends_the_call_and_blocks_the_after_part():
    """Отказ гарнитуры (06.10): канал собеседников пишет нули, собеседники звучат в
    микрофоне и переподписаны из-за отрезков без опоры. Их голос — сторона звонка;
    конец по нему не доказан — хвоста нет."""
    chan = dict(CHAN, **{"Собеседник 4": "mic"})
    segs = [(0.0, 100.0, "Собеседник 1"), (120.0, 900.0, "Собеседник 4"), (905.0, 910.0, OWNER)]
    out = split(segs, chan, unsupported=[(100.0, 918.0)], unsupported_labels={"Собеседник 4"})
    assert out.after == [] and out.meeting == segs and "без опоры" in out.why


def test_call_end_inside_an_unsupported_span_blocks_the_after_part():
    segs = [(0.0, 100.0, "Собеседник 1"), (120.0, 130.0, OWNER)]
    out = split(segs, unsupported=[(90.0, 140.0)])
    assert out.after == [] and "внутри отрезка без опоры" in out.why


def test_terminal_zero_run_after_the_call_does_not_block_a_real_after_part():
    """Нули канала до конца записи есть и у настоящих хвостов (2 из 2 замеренных):
    отрезок без опоры ПОСЛЕ конца звонка запись после звонка не отменяет."""
    segs = [(0.0, 100.0, "Собеседник 1"), (130.0, 150.0, OWNER)]
    out = split(segs, unsupported=[(101.0, 160.0)])
    assert out.end == 100.0 and out.after == segs[1:]


def test_unknown_label_gives_no_after_part():
    segs = [(0.0, 10.0, "Собеседник 1"), (20.0, 30.0, "Кто-то")]
    out = split(segs)
    assert out.after == [] and "Кто-то" in out.why


def test_no_call_segments_gives_no_after_part():
    segs = [(0.0, 10.0, OWNER)]
    out = split(segs)
    assert out.after == [] and out.why == "нет реплик собеседников"
