"""Mac UI rules against the grounded NDJSON frames (model-free).

follow only at the bottom; a manual card survives appends and blocks the jump;
pause withholds a frame and resume publishes the pending one first; Stop keeps
the last thread and a new meeting resets scroll/session; the additive `questions`
projection reaches the consumer without breaking the legacy frame.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

from grounded_feed.publish import NdjsonPublisher, ndjson_line  # noqa: E402
from grounded_feed.render import render_blocks  # noqa: E402
from grounded_feed.thread import ThreadCanvas  # noqa: E402
from grounded_feed_ui_model import MacFeedConsumer, render_blocks_frame  # noqa: E402


def _blocks_session():
    from grounded_feed.session import GroundedFeedSession
    session = GroundedFeedSession(lambda event: None)
    session.manual_request("m1", "Synthetic manual question?")
    session.manual_answer("m1", "Synthetic manual answer.")
    return session


def _wire():
    consumer = MacFeedConsumer()
    events = []
    publisher = NdjsonPublisher(lambda event: (events.append(dict(event)),
                                               consumer.consume(ndjson_line(event))))
    consumer.consume(ndjson_line({"type": "feed_mode", "enabled": True}))
    return consumer, publisher, events


def _publish(canvas, publisher, questions=None):
    return publisher.publish(render_blocks(canvas.ordered()), canvas.version, questions=questions)


def test_blocks_are_the_single_render_source_without_duplicates():
    session = _blocks_session()
    consumer = MacFeedConsumer()
    frame = [event for event in session.publisher.emitted if event["type"] == "thread"][-1]
    consumer.consume(ndjson_line(frame))
    blocks = consumer.blocks
    assert [row["kind"] for row in blocks] == ["question", "answer"]
    assert consumer.thread == render_blocks_frame(blocks)
    assert consumer.thread.count("Synthetic manual answer.") == 1
    assert consumer.thread.index("Synthetic manual question?") < consumer.thread.index("Synthetic manual answer.")


def test_manual_channel_frames_do_not_render_and_keep_thread_blocks():
    session = _blocks_session()
    consumer = MacFeedConsumer()
    for event in session.publisher.emitted:
        consumer.consume(ndjson_line(event))
    frame = [event for event in session.publisher.emitted if event["type"] == "thread"][-1]
    assert consumer.thread == render_blocks_frame(frame["blocks"])
    assert consumer.thread.count("Synthetic manual answer.") == 1
    manual = [e for e in session.publisher.emitted if e["type"] == "manual"]
    assert manual and all("manual_id" in e for e in manual)


def test_follow_only_at_bottom():
    consumer, publisher, _ = _wire()
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Synthetic one.")
    assert _publish(canvas, publisher)
    assert consumer.thread_records[-1]["followed"] is True
    consumer.user_scroll_up()
    canvas.paragraph("topic-1", "Release", "Synthetic two.")
    assert _publish(canvas, publisher)
    assert consumer.thread_records[-1]["followed"] is False
    consumer.user_scroll_to_bottom()
    canvas.paragraph("topic-1", "Release", "Synthetic three.")
    assert _publish(canvas, publisher)
    assert consumer.thread_records[-1]["followed"] is True


def test_manual_question_is_preserved_and_blocks_follow():
    consumer, publisher, _ = _wire()
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Synthetic one.")
    _publish(canvas, publisher)
    consumer.ask_manual("Synthetic manual question?")
    publisher.hint("Synthetic manual answer.", manual=True)
    assert consumer.manual_card_open and consumer.manual_question == "Synthetic manual question?"
    canvas.paragraph("topic-1", "Release", "Synthetic two.")
    _publish(canvas, publisher)
    assert consumer.thread_records[-1]["followed"] is False
    assert consumer.manual_question == "Synthetic manual question?"
    publisher.hint_done(manual=True)
    assert not consumer.manual_card_open
    assert consumer.manual_question == "Synthetic manual question?"  # not cleared by append


def test_pause_withholds_frame_and_resume_publishes_pending_first():
    consumer, publisher, events = _wire()
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Synthetic one.")
    _publish(canvas, publisher)
    before = len([e for e in events if e["type"] == "thread"])
    publisher.pause()
    canvas.paragraph("topic-1", "Release", "Synthetic while paused.")
    assert not _publish(canvas, publisher)
    assert len([e for e in events if e["type"] == "thread"]) == before
    assert publisher.resume()
    frames = [e for e in events if e["type"] == "thread"]
    assert len(frames) == before + 1 and "Synthetic while paused." in frames[-1]["text"]


def test_question_projection_reaches_consumer_and_stop_start_reset():
    consumer, publisher, _ = _wire()
    canvas = ThreadCanvas()
    qid = canvas.question("topic-1", "Release", "Synthetic question?")
    _publish(canvas, publisher, questions=[{"id": qid, "state": "open"}])
    assert consumer.questions == [{"id": qid, "state": "open"}]
    consumer.stop_meeting()
    assert consumer.stopped and "Synthetic question?" in consumer.thread
    consumer.reset_for_new_meeting()
    assert consumer.thread == "" and consumer.near_bottom and not consumer.stopped
    assert consumer.followed == 0 and consumer.questions == []
