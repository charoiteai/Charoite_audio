"""Public accepted-source identity and UTF-8 cursor boundary contracts."""

import hashlib

import pytest

from grounded_feed.semantic import ContractError, SemanticActor, speech_cursor_to_byte
from grounded_feed.source_actor import SourceActor, SourceRefused


def test_speech_cursor_preserves_utf8_boundaries_including_end_of_journal():
    actor = SemanticActor()
    actor.append_source("Hi 🌿", "speaker-a")
    actor.append_source("Да", "speaker-b")
    portions = actor.state.portions
    assert speech_cursor_to_byte(portions, 0, 0) == 0
    assert speech_cursor_to_byte(portions, 0, 3) == 3
    assert speech_cursor_to_byte(portions, 1, 0) == 7
    assert speech_cursor_to_byte(portions, 1, 1) == 9
    assert speech_cursor_to_byte(portions, 2, 0) == 11
    with pytest.raises(ContractError, match="out of bounds"):
        speech_cursor_to_byte(portions, 2, 1)


@pytest.mark.parametrize("bad_index", [True, 1.0])
def test_idempotent_source_retry_rejects_noninteger_index(bad_index):
    actor = SourceActor()
    journal = object()
    actor.bind_source(journal, session_epoch=1)
    label = "Synthetic speaker"
    speaker_id = "speaker:" + hashlib.sha256(label.encode()).hexdigest()
    body = "Synthetic accepted speech."
    fields = dict(session_epoch=1, body_hash=hashlib.sha256(body.encode()).hexdigest(),
                  source=journal, speaker_label=label)
    actor.append_source_at(0, body, speaker_id, **fields)
    second = actor.append_source_at(1, body, speaker_id, **fields)
    before = actor.export()

    # The valid retry returns the same accepted row; bool/float aliases must
    # not bypass admission through the existing-row fast path.
    assert actor.append_source_at(1, body, speaker_id, **fields) is second
    with pytest.raises(SourceRefused, match="source fields"):
        actor.append_source_at(bad_index, body, speaker_id, **fields)
    assert actor.export() == before
