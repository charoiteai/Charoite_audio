"""Accepted speech positions, including backlog, use body bytes without ACK."""

from types import SimpleNamespace

import pytest

from grounded_feed.accepted_source import AcceptedSource, AdapterRefused
from grounded_feed.session import GroundedFeedSession
from grounded_feed.source_actor import SourceActor
from transcript import Transcript


def test_channel_event_follows_accepted_unicode_speech_before_ingestion(tmp_path):
    transcript = Transcript(tmp_path)
    session = GroundedFeedSession(lambda event: None)
    session.attach_transcript(transcript)
    transcript.add("Привет, 🌍!", "Synthetic speaker")
    accepted = transcript.accepted_portions()
    expected = sum(len(row.body.encode("utf-8")) for row in accepted)
    assert session._accepted_source.cursor == 0
    session.add_system("Synthetic channel event")
    assert session._blocks()[0].position == expected
    assert session._accepted_source.cursor == 0  # positioning is not ingestion
    assert session.source_export()["portions"] == []
    assert session.ingest_source_once() == len(accepted)
    assert session.source_export()["source_tail"] == expected


def test_manual_boundary_includes_accepted_tail_without_ingestion(tmp_path):
    transcript = Transcript(tmp_path)
    session = GroundedFeedSession(lambda event: None)
    session.attach_transcript(transcript)
    transcript.add("An invented first topic.", "Synthetic A")
    first = sum(len(row.body.encode()) for row in transcript.accepted_portions())
    session.manual_request("manual-a", "An invented question?")
    transcript.add("Another invented topic: ёж.", "Synthetic B")
    second = sum(len(row.body.encode()) for row in transcript.accepted_portions())
    session.add_system("Synthetic event after the second topic")
    assert [block.position for block in session._blocks()] == [first, second]
    assert session._accepted_source.cursor == 0


class Journal:
    def __init__(self):
        self.rows = [SimpleNamespace(index=0, body="First", speaker="Synthetic", stamp="")]

    def accepted_cursor(self):
        return SimpleNamespace(portion=len(self.rows))

    def accepted_portions(self, start, stop):
        return tuple(self.rows[start:stop])


def test_failed_boundary_snapshot_does_not_advance_derived_cursor_or_ack():
    journal = Journal()
    source = AcceptedSource(SourceActor(), journal.accepted_portions, session_epoch=1)
    assert source.accepted_boundary() == 5
    journal.rows.append(SimpleNamespace(index=8, body="Second", speaker="Synthetic", stamp=""))
    with pytest.raises(AdapterRefused, match="boundary fields"):
        source.accepted_boundary()
    journal.rows[-1].index = 1
    assert source.accepted_boundary() == 11
    assert source.cursor == 0


def test_regressed_boundary_is_refused_without_ack():
    journal = Journal()
    source = AcceptedSource(SourceActor(), journal.accepted_portions, session_epoch=1)
    source.accepted_boundary()
    journal.rows.clear()
    with pytest.raises(AdapterRefused, match="regressed"):
        source.accepted_boundary()
    assert source.cursor == 0
