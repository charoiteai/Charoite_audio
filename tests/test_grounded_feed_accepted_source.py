"""Synthetic accepted-source delivery; no model, display or owner data."""

import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from grounded_feed.accepted_source import AcceptedSource, AdapterRefused
from grounded_feed.semantic import ContractError, ShadowActor
from transcript import AcceptedPortion, Transcript


class SyntheticJournal:
    def __init__(self, rows):
        self.rows = rows
        self.spans = []

    def accepted_cursor(self):
        return SimpleNamespace(portion=len(self.rows))

    def accepted_portions(self, start, stop):
        self.spans.append((start, stop))
        if not 0 <= start <= stop <= len(self.rows):
            raise ValueError("accepted portion range outside journal")
        return tuple(self.rows[start:stop])


def source(actor, rows, epoch=1):
    journal = rows if isinstance(rows, SyntheticJournal) else SyntheticJournal(rows)
    return AcceptedSource(actor, journal.accepted_portions, session_epoch=epoch)


def test_raw_utf8_and_identical_bodies_keep_speaker_identity():
    actor = ShadowActor()
    rows = [AcceptedPortion(0, "10:00", "speaker-a", "Привет 🌿"),
            AcceptedPortion(1, "10:01", "speaker-b", "Привет 🌿")]
    pump = source(actor, rows)
    portions = pump.drain()
    assert pump.cursor == 2
    assert [p.body_utf8 for p in portions] == [r.body for r in rows]
    assert [pump.speaker_labels[p.speaker_id] for p in portions] == [r.speaker for r in rows]
    assert portions[1].position == len(rows[0].body.encode("utf-8"))
    assert portions[0].body_hash == hashlib.sha256(rows[0].body.encode("utf-8")).hexdigest()
    assert "speaker-a:" not in portions[0].body_utf8
    assert pump.next() is None


@pytest.mark.parametrize("after", [False, True])
def test_exception_before_or_after_commit_retries_once(after):
    actor = ShadowActor()
    original = actor.append_source_at
    calls = []

    def unreliable(*args, **kwargs):
        if not calls:
            calls.append(True)
            if after:
                original(*args, **kwargs)
            raise RuntimeError("synthetic failure")
        return original(*args, **kwargs)

    actor.append_source_at = unreliable
    pump = source(actor, [AcceptedPortion(0, "", "speaker-a", "Raw body.")])
    with pytest.raises(RuntimeError):
        pump.next()
    assert pump.cursor == 0
    assert pump.next().body_utf8 == "Raw body."
    assert pump.cursor == 1
    assert len(actor.state.portions) == 1


@pytest.mark.parametrize("field,value", [("index", 1), ("body", "Changed."),
                                         ("speaker", "speaker-b"), ("stamp", "11:00")])
def test_failed_delivery_cannot_change_identity(field, value):
    actor = ShadowActor()
    original = actor.append_source_at
    rows = [AcceptedPortion(0, "", "speaker-a", "Raw body.")]
    pump = source(actor, rows)
    actor.append_source_at = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
    with pytest.raises(RuntimeError):
        pump.next()
    actor.append_source_at = original
    rows[0] = replace(rows[0], **{field: value})
    with pytest.raises(AdapterRefused):
        pump.next()
    assert not actor.state.portions
    assert pump.cursor == 0


def test_reset_late_old_tail_is_archived_pending_only():
    actor = ShadowActor()
    rows = [AcceptedPortion(0, "", "speaker-a", "Late raw tail.")]
    pump = source(actor, rows)
    assert actor.reset() == 2
    pump.next()
    old = actor.export(1)
    assert old["portions"][0]["body_utf8"] == "Late raw tail."
    assert old["pending"]
    assert not actor.state.portions
    assert pump.session_epoch == 1
    with pytest.raises(ContractError, match="source epoch must be current"):
        source(actor, rows, epoch=99).next()


def test_concurrent_independent_retry_cursors_do_not_duplicate():
    actor = ShadowActor()
    rows = [AcceptedPortion(0, "", "speaker-a", "Raw body.")]
    journal = SyntheticJournal(rows)
    pumps = [source(actor, journal) for _ in range(16)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        portions = list(pool.map(lambda pump: pump.next(), pumps))
    assert len(actor.state.portions) == 1
    assert all(portion == portions[0] for portion in portions)
    assert all(pump.cursor == 1 for pump in pumps)
    with pytest.raises(ContractError, match="identity changed"):
        rows[0] = replace(rows[0], speaker="speaker-b")
        source(actor, journal).next()


def test_real_transcript_snapshot_accessor_and_display_metadata(tmp_path):
    transcript = Transcript(tmp_path / "synthetic")
    transcript.add("Сырой текст 🌿", "Собеседник 1")
    actor = ShadowActor()
    pump = AcceptedSource(actor, transcript.accepted_portions, session_epoch=1)
    portion = pump.next()
    labels = pump.speaker_labels
    assert labels[portion.speaker_id] == "Собеседник 1"
    assert portion.speaker_id.isascii()
    assert portion.body_utf8 == "Сырой текст 🌿"
    assert portion.id == "p:1:0"
    assert pump.next() is None
    transcript.rename_speaker("Собеседник 1", "Новое имя")
    transcript.add("Другой сырой текст.", "Собеседник 2")
    second = pump.next()
    assert pump.speaker_labels[second.speaker_id] == "Собеседник 2"
    assert second.speaker_id not in labels  # prior snapshot never changes
    assert labels[portion.speaker_id] == "Собеседник 1"
    with pytest.raises(TypeError):
        labels[portion.speaker_id] = "changed"


def test_snapshot_range_error_is_not_eof():
    class InvalidJournal(SyntheticJournal):
        def accepted_portions(self, start, stop):
            raise ValueError("accepted portion range outside journal")

    pump = source(ShadowActor(), InvalidJournal([]))
    with pytest.raises(ValueError, match="outside journal"):
        pump.next()
    assert pump.cursor == 0


def test_wrong_receipt_index_retains_cursor_and_can_retry():
    actor = ShadowActor()
    original = actor.append_source_at
    actor.append_source_at = lambda *a, **k: replace(original(*a, **k), id="p:1:9")
    pump = source(actor, [AcceptedPortion(0, "", "Speaker label", "Raw body.")])
    with pytest.raises(AdapterRefused, match="receipt mismatch"):
        pump.next()
    assert pump.cursor == 0
    actor.append_source_at = original
    assert pump.next().id == "p:1:0"
    assert len(actor.state.portions) == 1


@pytest.mark.parametrize("accessor", [lambda start, stop: (), SyntheticJournal.accepted_portions])
def test_unbound_or_dynamic_accessor_refused(accessor):
    with pytest.raises(AdapterRefused, match="stable bound"):
        AcceptedSource(ShadowActor(), accessor, session_epoch=1)


def test_source_cannot_be_swapped_or_cross_epochs():
    actor = ShadowActor()
    old = SyntheticJournal([AcceptedPortion(0, "", "Old label", "Old body.")])
    new = SyntheticJournal([AcceptedPortion(0, "", "New label", "New body.")])
    old_pump = source(actor, old)
    with pytest.raises(ContractError, match="epoch already bound"):
        source(actor, new)
    actor.reset()
    with pytest.raises(ContractError, match="another epoch"):
        source(actor, old, epoch=2)
    new_pump = source(actor, new, epoch=2)
    with pytest.raises(ContractError, match="epoch already bound"):
        source(actor, new, epoch=1)
    old_pump.next()
    new_pump.next()
    assert actor.export(1)["portions"][0]["body_utf8"] == "Old body."
    assert actor.export(1)["pending"]
    assert actor.state.portions[0].body_utf8 == "New body."
    digest = hashlib.sha256(b"Forged body.").hexdigest()
    with pytest.raises(ContractError, match="binding mismatch"):
        actor.append_source_at(1, "Forged body.", "speaker-a", session_epoch=1,
                               body_hash=digest, source=new, speaker_label="Label")
    assert len(actor.export(1)["portions"]) == 1


def test_accessor_fetches_only_one_record_per_delivery():
    actor = ShadowActor()
    journal = SyntheticJournal([AcceptedPortion(i, "", "Label", f"Body {i}.") for i in range(100)])
    pump = source(actor, journal)
    assert len(pump.drain(limit=100)) == 100
    assert pump.next() is None
    assert journal.spans == [(i, i + 1) for i in range(100)] + [(100, 100)]


def test_bound_epochs_reject_legacy_append_but_unbound_keeps_legacy_api():
    actor = ShadowActor()
    journal = SyntheticJournal([AcceptedPortion(0, "", "Capture label", "Trusted body.")])
    pump = source(actor, journal)
    with pytest.raises(ContractError, match="indexed append required"):
        actor.append_source("Foreign current body.", "speaker-a")
    actor.reset()
    with pytest.raises(ContractError, match="indexed append required"):
        actor.append_source("Foreign archived body.", "speaker-a", session_epoch=1)
    assert not actor.export(1)["portions"]
    assert actor.append_source("Legacy unbound body.", "speaker-a").session_epoch == 2
    pump.next()
    assert actor.export(1)["portions"][0]["body_utf8"] == "Trusted body."


def test_capture_labels_survive_stop_reset_and_late_tail_without_pump():
    actor = ShadowActor()
    rows = [AcceptedPortion(0, "", "Собеседник 1", "Raw body.")]
    journal = SyntheticJournal(rows)
    pump = source(actor, journal)
    first = pump.next()
    stopped = actor.stop()
    assert stopped["speaker_labels"][first.speaker_id] == "Собеседник 1"
    actor.reset()
    rows.append(AcceptedPortion(1, "", "Собеседник 2", "Late body."))
    second = pump.next()
    del pump
    archived = actor.export(1)
    assert archived["speaker_labels"] == {
        first.speaker_id: "Собеседник 1", second.speaker_id: "Собеседник 2"}
    archived["speaker_labels"][first.speaker_id] = "changed export copy"
    assert actor.export(1)["speaker_labels"][first.speaker_id] == "Собеседник 1"
    assert actor.export()["speaker_labels"] == {}
    assert all("Собеседник" not in p["body_utf8"] for p in archived["portions"])
    digest = hashlib.sha256(b"Raw body.").hexdigest()
    with pytest.raises(ContractError, match="label hash mismatch"):
        actor.append_source_at(0, "Raw body.", first.speaker_id, session_epoch=1,
                               body_hash=digest, source=journal, speaker_label="Changed label")


def test_speaker_id_collision_refuses_without_changing_export(monkeypatch):
    actor = ShadowActor()
    journal = SyntheticJournal([AcceptedPortion(0, "", "First label", "Raw body.")])
    pump = source(actor, journal)
    portion = pump.next()
    module = sys.modules[ShadowActor.__module__]
    original_hash = module._hash
    monkeypatch.setattr(module, "_hash", lambda value: (
        original_hash("First label") if value == "Second label" else original_hash(value)))
    with pytest.raises(ContractError, match="label identity changed"):
        actor.append_source_at(0, "Raw body.", portion.speaker_id, session_epoch=1,
                               body_hash=original_hash("Raw body."), source=journal,
                               speaker_label="Second label")
    assert actor.export()["speaker_labels"] == {portion.speaker_id: "First label"}
    assert len(actor.state.portions) == 1


@pytest.mark.parametrize("matching", [False, True])
def test_first_binding_rejects_populated_legacy_epoch_without_mutation(matching):
    actor = ShadowActor()
    label = "Capture label"
    speaker_id = "speaker:" + hashlib.sha256(label.encode("utf-8")).hexdigest()
    actor.append_source("Legacy body.", speaker_id)
    before = actor.export()
    rows = ([AcceptedPortion(0, "", label, "Legacy body.")] if matching else
            [AcceptedPortion(0, "", "Other label", "Foreign journal body.")])
    journal = SyntheticJournal(rows)
    with pytest.raises(ContractError, match="pristine for first binding"):
        source(actor, journal)
    assert actor.export() == before
    assert actor._source_bindings == {}
    with pytest.raises(ContractError, match="pristine for first binding"):
        source(actor, SyntheticJournal([]))
    assert actor.export() == before
    assert actor._source_bindings == {}
    # A failed takeover leaves the original legacy ownership mode usable.
    assert actor.append_source("More legacy body.", "speaker-a").id == "p:1:1"
