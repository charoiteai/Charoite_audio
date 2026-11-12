"""Python frame contract consumed by the Mac UI, without a simulated UI.

Actual scroll, manual-correlation and decoding behavior belongs to Swift tests.
These tests exercise emitted session frames, their typed projection and delivery
across pause/resume using the real Python publisher and renderer.
"""

import json

from grounded_feed.publish import NdjsonPublisher, ndjson_line
from grounded_feed.render import render_blocks
from grounded_feed.session import GroundedFeedSession
from grounded_feed.thread import ThreadCanvas


def _blocks_session():
    events = []
    session = GroundedFeedSession(events.append)
    session.manual_request("m1", "Synthetic manual question?")
    session.manual_answer("m1", "Synthetic manual answer.")
    return session, events


def test_session_emits_ordered_blocks_without_duplicates():
    session, events = _blocks_session()
    frame = [event for event in events if event["type"] == "thread"][-1]
    assert frame["text"] == session.render()
    assert [row["kind"] for row in frame["blocks"]] == ["question", "answer"]
    assert [row["text"] for row in frame["blocks"]] == [
        "Synthetic manual question?", "Synthetic manual answer."]
    assert frame["text"].count("Synthetic manual answer.") == 1
    question, answer = frame["blocks"]
    assert answer["answer_to"] == question["question_id"]
    assert frame["questions"][0]["id"] == question["question_id"]
    assert frame["questions"][0]["state"] == "answered"


def test_manual_channel_frames_preserve_identity_on_the_wire():
    _session, events = _blocks_session()
    decoded = [json.loads(ndjson_line(event)) for event in events]
    assert decoded == events
    manual = [event for event in decoded if event["type"] == "manual"]
    assert [(event["manual_id"], event["state"], event["text"]) for event in manual] == [
        ("m1", "question", "Synthetic manual question?"),
        ("m1", "answer", "Synthetic manual answer."),
    ]


def test_pause_withholds_frame_and_resume_publishes_pending_first():
    events = []
    publisher = NdjsonPublisher(events.append)
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Synthetic one.")
    assert publisher.publish(render_blocks(canvas.ordered()), canvas.version)
    publisher.pause()
    canvas.paragraph("topic-1", "Release", "Synthetic while paused.")
    assert not publisher.publish(render_blocks(canvas.ordered()), canvas.version)
    assert len(events) == 1
    assert publisher.resume()
    assert [event["type"] for event in events] == ["thread", "thread"]
    assert events[-1]["text"] == render_blocks(canvas.ordered())
    assert events[-1]["text"].count("Synthetic while paused.") == 1
