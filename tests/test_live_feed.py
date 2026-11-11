"""Synthetic speech only: feed policy, manual priority and feed_contract boundaries."""

import dataclasses
import threading

import pytest

from feed_contract import FeedItem, SpeechChunk, SpeechCursor, parse_feed, slice_speech_entries
from live_feed import FeedPolicy, ManualPriority


def test_legacy_config_keeps_classic_defaults_and_feed_is_explicit():
    classic = FeedPolicy.from_config({"sufler": {}})
    assert not classic.feed and classic.auto_hint and classic.auto_expand and classic.instant
    feed = FeedPolicy.from_config(
        {"sufler": {"live_feed": True, "instant": True, "auto_hint": True, "auto_expand": True}}
    )
    assert feed.feed and not feed.auto_hint and not feed.auto_expand and not feed.instant


def test_two_asks_timeout_one_cannot_clear_the_other():
    p = ManualPriority()
    ready, release = threading.Event(), threading.Event()

    @p.requested
    def second():
        ready.set()
        assert release.wait(2)

    worker = threading.Thread(target=second)
    worker.start()
    try:
        assert ready.wait(2)
        p.set()
        p.clear()  # first caller times out
        assert p.is_set()
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and not p.is_set()


def test_feed_contract_immutability_fences_and_slice_boundaries():
    for obj, attr in (
        (SpeechCursor(), "portion"),
        (SpeechChunk("x", SpeechCursor(), 1), "body_chars"),
        (FeedItem("Topic", "Fact"), "topic"),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, attr, 99)

    assert parse_feed('```json\n{"none": true}\n```') == []
    for bad_fence in ('12345678{"none": true}\n```', '```json\n{"none": true}'):
        with pytest.raises(ValueError):
            parse_feed(bad_fence)

    with pytest.raises(ValueError):
        slice_speech_entries([], "not-a-cursor")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        slice_speech_entries([], SpeechCursor(), max_chars=63)
    assert slice_speech_entries([("10:00", "A", "ok")], SpeechCursor(), max_chars=64).body_chars == 2

    entries = [("10:00", "A", "abcd"), ("10:01", "B", "efgh")]
    for bad_cur in (
        SpeechCursor(-1, 0),
        SpeechCursor(0, -1),
        SpeechCursor(3, 0),
        SpeechCursor(2, 1),
        SpeechCursor(0, 5),
    ):
        with pytest.raises(ValueError):
            slice_speech_entries(entries, bad_cur)

    assert slice_speech_entries(entries, SpeechCursor(2, 0)) == SpeechChunk("", SpeechCursor(2, 0), 0)
    from_end0 = slice_speech_entries(entries, SpeechCursor(0, 4))
    assert from_end0 == SpeechChunk("[10:01] B: efgh", SpeechCursor(2, 0), 4)

    c_exact_stop = slice_speech_entries(entries, SpeechCursor(0, 0), max_chars=64)
    assert c_exact_stop.next_cursor == SpeechCursor(2, 0)
    big_entries = [("10:00", "A", "x" * 53), ("10:01", "B", "y" * 10)]
    c64 = slice_speech_entries(big_entries, SpeechCursor(0, 0), max_chars=64)
    assert c64.text == "[10:00] A: " + "x" * 53
    assert c64.next_cursor == SpeechCursor(1, 0)
    assert c64.body_chars == 53

    pair_entries = [("10:00", "A", "x" * 41), ("10:01", "B", "y" * 10)]
    c_room0 = slice_speech_entries(pair_entries, SpeechCursor(0, 0), max_chars=64)
    assert c_room0.next_cursor == SpeechCursor(1, 0)
    c_room1 = slice_speech_entries(pair_entries, SpeechCursor(0, 0), max_chars=65)
    assert c_room1.next_cursor == SpeechCursor(1, 1)
    assert c_room1.body_chars == 42

    lead_space = [("10:00", "A", " " + "z" * 80)]
    c_lead = slice_speech_entries(lead_space, SpeechCursor(0, 0), max_chars=64)
    assert c_lead.body_chars == 53
    assert c_lead.next_cursor == SpeechCursor(0, 53)

    words = [("10:00", "A", "alpha " + "b" * 60)]
    c_word = slice_speech_entries(words, SpeechCursor(0, 0), max_chars=64)
    assert c_word.text == "[10:00] A: alpha "
    assert c_word.next_cursor == SpeechCursor(0, 6)

    long_spk = [("10:00", "S" * 200, "ok")]
    assert len(slice_speech_entries(long_spk, max_chars=1000).text.split("] ", 1)[1].split(": ", 1)[0]) == 128
    assert len(slice_speech_entries(long_spk, max_chars=200).text.split("] ", 1)[1].split(": ", 1)[0]) == 50
