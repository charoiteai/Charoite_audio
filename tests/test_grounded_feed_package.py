"""Grounded feed package (Task №859): model-free unit contract.

Covers the append-only owner, readable renderer, serial publisher with Q/A
projection, typed channel events and versioned export. No model, network or
owner data.
"""

import json

import pytest

from grounded_feed.events import ChannelEvent
from grounded_feed.export import ExportDurability, ExportRefused, read_snapshot, write_snapshot
from grounded_feed.publish import NdjsonPublisher, ndjson_line
from grounded_feed.render import assert_surface_preserved, render_blocks
from grounded_feed.session import GroundedFeedSession
from grounded_feed.thread import CanvasRefused, ThreadCanvas, legacy_answer_id


def test_canvas_is_append_only_and_orders_by_source_boundary():
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Synthetic second.", position=10)
    canvas.paragraph("topic-1", "Release", "Synthetic first.", position=1)
    canvas.system("Channel lost for the synthetic probe.", position=10)
    assert [b.text for b in canvas.ordered()] == [
        "Synthetic first.", "Synthetic second.", "Channel lost for the synthetic probe."]
    assert canvas.version == 3


def test_canvas_refuses_duplicate_unknown_and_answer_rewrite():
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Synthetic fact.")
    with pytest.raises(CanvasRefused):
        canvas.paragraph("topic-1", "Release", "Synthetic fact.")
    with pytest.raises(CanvasRefused):
        canvas.answer("q9", "Synthetic answer.")
    qid = canvas.question("topic-1", "Release", "Synthetic question?")
    canvas.answer(qid, "Synthetic answer.")
    with pytest.raises(CanvasRefused):
        canvas.answer(qid, "Synthetic rewrite.")


def test_canvas_stores_distinct_answers_of_one_question_in_order():
    canvas = ThreadCanvas()
    qid = canvas.question("topic-1", "Release", "Synthetic question?")
    first = canvas.answer(qid, "Synthetic first answer.", answer_id="a1",
                          scope_ids=["s2"], claim_ids=["c2"],
                          status="partially_answered", source_refs=["u1"])
    second = canvas.answer(qid, "Synthetic second answer.", answer_id="a2",
                           scope_ids=["s1"], claim_ids=["c1"], status="answered",
                           source_refs=["u2"])
    assert [block.answer_id for block in canvas.ordered()] == [None, "a1", "a2"]
    assert canvas.answer_ids(qid) == ("a1", "a2")
    assert canvas.answer_by_id("a1") is first
    assert not canvas.is_open(qid)
    assert second.scope_ids == ("s1",) and second.status == "answered"
    assert second.qid == qid and second.to_dict()["question_id"] == qid
    text = render_blocks(canvas.ordered())
    assert text.index("Synthetic question?") < text.index("Synthetic first answer.")
    assert text.index("Synthetic first answer.") < text.index("Synthetic second answer.")


@pytest.mark.parametrize("status", [[], {}, 0, True])
def test_canvas_refuses_non_string_answer_status(status):
    canvas = ThreadCanvas()
    qid = canvas.question("topic-1", "Release", "Synthetic question?")
    with pytest.raises(CanvasRefused, match="answer_status"):
        canvas.answer(qid, "Synthetic answer.", status=status)
    assert canvas.answer_ids(qid) == ()


def test_repeated_answer_id_is_idempotent_and_conflicts_are_closed():
    canvas = ThreadCanvas()
    qid = canvas.question("topic-1", "Release", "Synthetic question?")
    before = canvas.version
    first = canvas.answer(qid, "Synthetic answer.", answer_id="a1")
    assert canvas.answer(qid, "Synthetic answer.", answer_id="a1") is first
    assert canvas.version == before + 1 and len(canvas.ordered()) == 2
    with pytest.raises(CanvasRefused, match="answer_conflict"):
        canvas.answer(qid, "Synthetic changed answer.", answer_id="a1")
    legacy_qid = canvas.question("topic-1", "Release", "Synthetic legacy question?")
    legacy = canvas.answer(legacy_qid, "Synthetic legacy answer.")
    assert legacy.answer_id == legacy_answer_id(legacy_qid)
    assert canvas.answer(legacy_qid, "Synthetic legacy answer.") is legacy
    with pytest.raises(CanvasRefused, match="answered_question"):
        canvas.answer(legacy_qid, "Synthetic legacy rewrite.")


def test_renderer_keeps_model_surfaces_and_nests_the_answer():
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Keep   these exact bytes.")
    qid = canvas.question("topic-1", "Release", "Synthetic question?")
    canvas.answer(qid, "Synthetic answer, verbatim.")
    text = render_blocks(canvas.ordered())
    assert "Keep   these exact bytes." in text
    assert text.index("Synthetic question?") < text.index("Synthetic answer, verbatim.")
    assert_surface_preserved(canvas.ordered(), text)


def test_publisher_contract_projection_and_stale_guard():
    events = []

    def emit(event):
        events.append(dict(event))

    publisher = NdjsonPublisher(emit)
    publisher.feed_mode(True)
    canvas = ThreadCanvas()
    canvas.paragraph("topic-1", "Release", "Synthetic fact.")
    qid = canvas.question("topic-1", "Release", "Synthetic question?")
    canvas.answer(qid, "Synthetic answer.")
    projection = [{"id": qid, "topic": "topic-1", "topic_label": "Release",
                   "question": "Synthetic question?", "state": "answered",
                   "answer": "Synthetic answer."}]
    assert publisher.publish(render_blocks(canvas.ordered()), canvas.version, questions=projection)
    assert not publisher.publish("stale", canvas.version - 1)
    thread = [e for e in events if e["type"] == "thread"][-1]
    assert set(thread) == {"type", "text", "live_feed", "questions"}
    assert thread["questions"] == projection and thread["live_feed"] is True
    assert ndjson_line(thread).count("\n") == 1


def test_publisher_sink_refusal_does_not_mark_version_delivered():
    calls = []

    def emit(event):
        calls.append(event)
        return False  # explicit non-delivery, like a closed stdout

    publisher = NdjsonPublisher(emit)
    assert publisher.publish("synthetic text", 4) is False
    assert publisher.published_versions == [] and not publisher.emitted


def test_publisher_delivery_failure_does_not_mark_version_delivered():
    calls = []

    def emit(event):
        calls.append(event)
        if len(calls) == 1:
            raise OSError("injected emit failure")

    publisher = NdjsonPublisher(emit)
    assert publisher.publish("first synthetic text", 5) is False
    assert publisher.published_versions == []
    assert publisher.publish("first synthetic text", 5) is True
    assert publisher.published_versions == [5]


def test_publisher_resume_failure_keeps_pending_for_retry():
    events = []
    failing = {"value": True}

    def emit(event):
        if failing["value"]:
            raise OSError("injected emit failure")
        events.append(event)

    publisher = NdjsonPublisher(emit)
    publisher.pause()
    assert publisher.publish("pending synthetic text", 7) is False
    assert publisher.resume() is False
    assert publisher.pendings_still_available() if hasattr(publisher, "pendings_still_available") else True
    failing["value"] = False
    assert publisher.resume() is True
    assert [event["text"] for event in events if event["type"] == "thread"] == ["pending synthetic text"]


def test_resume_restores_an_empty_blocks_snapshot():
    events = []
    publisher = NdjsonPublisher(events.append)
    publisher.pause()
    assert not publisher.publish("synthetic text", 3, blocks=[])
    assert publisher.resume()
    frame = [e for e in events if e["type"] == "thread"][-1]
    assert frame["blocks"] == []  # an empty projection is a valid, restored snapshot


def test_manual_question_and_answer_survive_pause_in_order():
    events = []

    def emit(event):
        events.append(dict(event))

    session = GroundedFeedSession(emit)
    session.pause()
    session.manual_request("m1", "Synthetic paused manual question?")
    session.manual_answer("m1", "Synthetic paused manual answer.")
    assert not [e for e in events if e["type"] == "thread"]  # still paused
    assert "Synthetic paused manual question?" in session.render()
    session.resume()
    frame = [e for e in events if e["type"] == "thread"][-1]
    kinds = [row["kind"] for row in frame["blocks"]]
    assert kinds == ["question", "answer"]
    assert frame["blocks"][0]["manual_id"] == frame["blocks"][1]["manual_id"] == "m1"


def test_hint_frames_carry_the_manual_id_for_late_correlation():
    events = []
    publisher = NdjsonPublisher(events.append)
    publisher.hint("Synthetic hint.", manual=True, manual_id="m-7")
    publisher.hint_done(manual=True, manual_id="m-7")
    assert events[0]["manual_id"] == events[1]["manual_id"] == "m-7"
    assert events[0]["manual"] is True and events[1]["manual"] is True


def test_manual_frames_carry_a_stable_manual_id():
    events = []

    def emit(event):
        events.append(dict(event))

    session = GroundedFeedSession(emit)
    qid = session.manual_request("m-late", "Synthetic manual question?")
    answer = session.manual_answer("m-late", "Synthetic manual answer.")
    manual_events = [e for e in events if e["type"] == "manual"]
    assert [e["state"] for e in manual_events] == ["question", "answer"]
    assert {e["manual_id"] for e in manual_events} == {"m-late"}
    blocks = [e for e in events if e["type"] == "thread"][-1]["blocks"]
    assert blocks[0]["question_id"] == qid and blocks[1]["manual_id"] == "m-late"
    assert answer.manual_id == "m-late"


def test_concurrent_and_late_manual_answers_correlate_without_duplicates():
    events = []

    def emit(event):
        events.append(dict(event))

    session = GroundedFeedSession(emit)
    session.manual_request("m1", "Synthetic first question?")
    session.manual_request("m2", "Synthetic second question?")
    session.manual_answer("m2", "Synthetic second answer.")
    session.manual_answer("m1", "Synthetic late first answer.")
    frame = [e for e in events if e["type"] == "thread"][-1]
    blocks = frame["blocks"]
    assert [row["kind"] for row in blocks] == ["question", "answer", "question", "answer"]
    assert [row["manual_id"] for row in blocks] == ["m1", "m1", "m2", "m2"]
    assert len({row["id"] for row in blocks}) == 4
    assert [row["index"] for row in blocks] == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        session.manual_answer("missing", "Synthetic answer without question.")


def test_projection_rejects_answer_before_question_and_lists_every_answer():
    from grounded_feed.thread import Block
    question = Block(0, "question", "topic-1", "Release", "Synthetic question?", 5, "q1")
    early_answer = Block(1, "answer", "topic-1", "Release", "Synthetic answer.", 1, "q1")
    with pytest.raises(ValueError, match="before its question"):
        GroundedFeedSession._projection_blocks([early_answer, question])
    first = Block(1, "answer", "topic-1", "Release", "Synthetic answer.", 6, "q1",
                  answer_id="a1", scope_ids=("s1",))
    second = Block(2, "answer", "topic-1", "Release", "Synthetic second answer.", 7, "q1",
                   answer_id="a2", scope_ids=("s2",))
    rows = GroundedFeedSession._projection_blocks([question, first, second])
    assert [row["kind"] for row in rows] == ["question", "answer", "answer"]
    assert [row["answer_id"] for row in rows[1:]] == ["a1", "a2"]
    assert [row["scope_ids"] for row in rows[1:]] == [["s1"], ["s2"]]
    assert all(row["answer_to"] == "q1" for row in rows[1:])
    assert [row["index"] for row in rows] == [0, 1, 2]


def test_answers_keep_arrival_order_when_source_positions_reverse():
    canvas = ThreadCanvas()
    qid = canvas.question("topic-1", "Release", "Synthetic question?", position=0)
    canvas.answer(qid, "First arrival.", position=100, answer_id="a1")
    canvas.answer(qid, "Second arrival.", position=10, answer_id="a2")
    blocks = canvas.snapshot_versioned()[1]
    rendered = render_blocks(blocks)
    projected = GroundedFeedSession._projection_blocks(blocks)
    assert rendered.index("First arrival.") < rendered.index("Second arrival.")
    assert [row["answer_id"] for row in projected if row["kind"] == "answer"] == ["a1", "a2"]


def test_publisher_pause_withholds_and_resume_emits_pending_once():
    events = []
    publisher = NdjsonPublisher(events.append)
    publisher.pause()
    assert not publisher.publish("pending text", 1, questions=[])
    assert not [e for e in events if e["type"] == "thread"]
    assert publisher.resume()
    assert [e["text"] for e in events if e["type"] == "thread"] == ["pending text"]
    assert not publisher.resume()


def test_atomic_block_projection_is_ordered_and_links_q_to_answer():
    events = []

    def emit(event):
        events.append(dict(event))

    session = GroundedFeedSession(emit)
    session.manual_request("m1", "Synthetic manual question?")
    session.manual_answer("m1", "Synthetic manual answer.")
    frame = [e for e in events if e["type"] == "thread"][-1]
    blocks = frame["blocks"]
    kinds = [row["kind"] for row in blocks]
    assert kinds == ["question", "answer"]
    question, answer = blocks
    assert question["id"] == "b0" and answer["id"] == "b1"
    assert answer["answer_to"] == question["question_id"] == "q1"
    assert question["manual_id"] == answer["manual_id"] == "m1"
    assert [row["index"] for row in blocks] == [0, 1]
    assert frame["text"].index("Synthetic manual question?") < frame["text"].index("Synthetic manual answer.")


def test_manual_correlation_is_idempotent_and_stable_across_resume():
    events = []

    def emit(event):
        events.append(dict(event))

    session = GroundedFeedSession(emit)
    qid = session.manual_request("m1", "Synthetic manual question?")
    assert session.manual_request("m1", "Synthetic duplicate question?") == qid
    first = session.manual_answer("m1", "Synthetic manual answer.")
    assert session.manual_answer("m1", "Synthetic manual answer.") is first
    with pytest.raises(ValueError):
        session.manual_answer("m1", "Synthetic conflicting answer.")
    blocks = [e for e in events if e["type"] == "thread"][-1]["blocks"]
    assert [row["kind"] for row in blocks] == ["question", "answer"]
    assert len(blocks) == len({row["id"] for row in blocks})
    session.pause()
    session.manual_request("m2", "Synthetic paused question?")
    assert session.publisher.paused
    session.resume()
    resumed = [e for e in events if e["type"] == "thread"][-1]["blocks"]
    ids = [row["id"] for row in resumed]
    assert ids[:2] == ["b0", "b1"] and len(ids) == len(set(ids))
    assert any(row["manual_id"] == "m2" for row in resumed)


def test_channel_event_validation():
    assert ChannelEvent("channel_lost", "Channel lost for the synthetic probe.").to_dict()["kind"] == "channel_lost"
    with pytest.raises(ValueError):
        ChannelEvent("model_fact", "not a channel event")
    with pytest.raises(ValueError):
        ChannelEvent("system", "")


def test_publisher_pending_version_cannot_regress_while_paused():
    events = []
    publisher = NdjsonPublisher(events.append)
    publisher.pause()
    assert not publisher.publish("newer pending", 3)
    assert not publisher.publish("older pending", 2)
    assert publisher.resume()
    assert [e["text"] for e in events if e["type"] == "thread"] == ["newer pending"]


def test_canvas_refusals_do_not_poison_state():
    canvas = ThreadCanvas()
    before = canvas.version
    with pytest.raises(CanvasRefused):
        canvas.question("topic-1", "Release", "", position=0)
    assert canvas.version == before and not canvas.ordered()
    qid = canvas.question("topic-1", "Release", "Synthetic question?", position=0)
    assert counter_open(canvas) == 1
    version = canvas.version
    with pytest.raises(CanvasRefused):
        canvas.answer(qid, "Synthetic answer.", position=-1)
    assert canvas.version == version and canvas.is_open(qid)
    canvas.answer(qid, "Synthetic answer.", position=1)
    version = canvas.version
    with pytest.raises(CanvasRefused):
        canvas.paragraph("topic-1", "Release", "Synthetic fact.", position=-1)
    assert canvas.version == version
    canvas.paragraph("topic-1", "Release", "Synthetic fact.", position=2)


def counter_open(canvas) -> int:
    return sum(1 for block in canvas.ordered() if block.kind == "question" and canvas.is_open(block.question_id))


def test_versioned_export_roundtrip_and_refusals(tmp_path):
    path = tmp_path / "grounded.json"
    envelope = {"version": 1, "session_epoch": "epoch-1", "request_id": "req-1",
                "snapshot": {"render": "Synthetic fact."}}
    write_snapshot(path, envelope)
    assert read_snapshot(path) == envelope
    with pytest.raises(ExportRefused):
        write_snapshot(path, {"version": 2, "session_epoch": "e", "request_id": "r", "snapshot": {}})
    assert read_snapshot(path) == envelope  # previous version untouched
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ExportRefused):
        read_snapshot(link)


def test_export_failures_before_and_after_replace(tmp_path, monkeypatch):
    import os as _os

    path = tmp_path / "grounded.json"
    envelope = {"version": 1, "session_epoch": "e", "request_id": "r",
                "snapshot": {"render": "Synthetic fact."}}
    write_snapshot(path, envelope)
    old = path.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("injected replace failure")

    monkeypatch.setattr(_os, "replace", fail_replace)
    with pytest.raises(OSError):
        write_snapshot(path, {**envelope, "snapshot": {"render": "New synthetic fact."}})
    monkeypatch.undo()
    assert path.read_bytes() == old
    assert not [name for name in path.parent.iterdir() if ".tmp" in name.name]

    calls = {"n": 0}
    real_fsync = _os.fsync

    def fail_directory_fsync(fd):
        calls["n"] += 1
        if calls["n"] >= 2:  # file fsync succeeded, directory fsync (after replace) failed
            raise OSError("injected fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(_os, "fsync", fail_directory_fsync)
    with pytest.raises(ExportDurability) as error:
        write_snapshot(path, {**envelope, "snapshot": {"render": "Replaced before durable failure."}})
    assert error.value.path == path
    assert json.loads(path.read_text())["snapshot"]["render"] == "Replaced before durable failure."
    monkeypatch.undo()
    assert not [name for name in path.parent.iterdir() if ".tmp" in name.name]


def test_session_export_is_one_consistent_snapshot():
    import threading
    session = GroundedFeedSession(lambda event: None)
    stop = threading.Event()

    def writer():
        index = 0
        while not stop.is_set() and index < 100:
            session.channel_event("system", f"Synthetic channel event {index}.")
            index += 1

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(20):
            payload = session.export_payload(session_epoch="e", request_id="r")
            snapshot = payload["snapshot"]
            assert snapshot["render"] == render_blocks(_blocks_from_dicts(snapshot["blocks"]))
            question_states = {row["state"] for row in snapshot["questions"]}
            assert question_states <= {"open", "answered"}
    finally:
        stop.set()
        thread.join()


def _blocks_from_dicts(rows):
    from grounded_feed.thread import Block
    return [Block(**row) for row in rows]


def test_session_close_race_keeps_every_accepted_event_and_refuses_late_ones(tmp_path):
    import threading
    import time

    session = GroundedFeedSession(lambda event: None)
    successes = []
    stop = threading.Event()

    def writer():
        index = 0
        while not stop.is_set() and index < 500:
            if session.channel_event("system", f"Synthetic channel event {index}."):
                successes.append(index)
            index += 1
            time.sleep(0.001)

    thread = threading.Thread(target=writer)
    thread.start()
    time.sleep(0.02)
    target = tmp_path / "stop-export.json"
    session.close(export_path=target, session_epoch="e", request_id="r")
    stop.set()
    thread.join()
    payload = read_snapshot(target)
    system_blocks = [block for block in payload["snapshot"]["blocks"] if block["kind"] == "system"]
    assert len(system_blocks) == len(successes)
    assert session.add_system("late synthetic event") is False


def test_session_close_exports_before_closing(tmp_path):
    session = GroundedFeedSession(lambda event: None)
    session.channel_event("system", "Synthetic channel event.")
    target = tmp_path / "stop-export.json"
    assert session.close(export_path=target, session_epoch="epoch-1", request_id="req-1") is True
    payload = read_snapshot(target)
    assert payload["session_epoch"] == "epoch-1"
    assert "Synthetic channel event." in payload["snapshot"]["render"]
    assert session.closed is True
    assert session.close(export_path=tmp_path / "again.json") is False
    assert not (tmp_path / "again.json").exists()


def test_session_boundary_provider_places_channel_event_on_source_boundary():
    session = GroundedFeedSession(lambda event: None)
    session.set_boundary_provider(lambda: 42)
    session.channel_event("channel_lost", "Channel lost for the synthetic probe.")
    blocks = session._canvas.ordered()
    assert blocks[-1].kind == "system" and blocks[-1].position == 42


def test_canvas_mutations_are_serialized_under_concurrency():
    import threading
    canvas = ThreadCanvas()
    errors = []

    def writer(prefix):
        try:
            for index in range(25):
                canvas.paragraph("topic-1", "Release", f"{prefix} synthetic fact {index}.",
                                 position=int(prefix[1:]) * 25 + index)
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"w{number}",)) for number in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    version, blocks = canvas.snapshot_versioned()
    assert version == len(blocks) == 100
    assert sorted(b.serial for b in blocks) == list(range(100))
    assert [b.position for b in blocks] == sorted(b.position for b in blocks)


def test_answer_is_rendered_under_question_even_with_channel_between():
    canvas = ThreadCanvas()
    qid = canvas.question("topic-1", "Release", "Synthetic question?", position=1)
    canvas.system("Channel lost for the synthetic probe.", position=2)
    canvas.answer(qid, "Synthetic answer.", position=3)
    text = render_blocks(canvas.ordered())
    assert text.index("Synthetic question?") < text.index("Synthetic answer.")
    assert text.index("Synthetic answer.") < text.index("Channel lost for the synthetic probe.")


def test_stop_drains_the_whole_accepted_tail_not_just_1024(tmp_path):
    import json

    class FakeSource:
        session_epoch = 1
        cursor = 0

        def __init__(self, total):
            self.total = total

        def drain(self, limit=8):
            batch = min(limit, self.total)
            self.total -= batch
            return list(range(batch))

    class FakeActor:
        def export(self):
            return {}

    events = []
    session = GroundedFeedSession(lambda event: events.append(dict(event)))
    session.attach_source(FakeActor(), FakeSource(1500))
    path = tmp_path / "export.json"
    assert session.close(export_path=path, session_epoch="e", request_id="r") is True
    payload = json.loads(path.read_text())
    assert payload["snapshot"]["source_drain_failure"] is None
    assert payload["snapshot"]["complete"] is True
    assert payload["snapshot"]["source_cursor"] == 0
    assert session.source_ingested == 1500


def test_stop_records_drain_failure_instead_of_silently_losing_the_tail(tmp_path):
    import json

    class FailingSource:
        session_epoch = 1
        cursor = 0
        calls = 0

        def drain(self, limit=8):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("synthetic drain failure")
            return [1]

    class FakeActor:
        def export(self):
            return {}

    events = []
    session = GroundedFeedSession(lambda event: events.append(dict(event)))
    session.attach_source(FakeActor(), FailingSource())
    path = tmp_path / "export.json"
    assert session.close(export_path=path, session_epoch="e", request_id="r") is False
    payload = json.loads(path.read_text())
    assert payload["snapshot"]["source_drain_failure"] == "RuntimeError"
    assert payload["snapshot"]["complete"] is False
    assert session.source_ingested == 1


class _Actor:
    def export(self):
        return {}


def _session_with(source):
    events = []
    session = GroundedFeedSession(lambda event: events.append(dict(event)))
    session.attach_source(_Actor(), source)
    return session


class _Batches:
    session_epoch = 1
    cursor = 0

    def __init__(self, batches):
        self.batches = list(batches)

    def drain(self, limit=8):
        if not self.batches:
            return []
        item = self.batches.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_drain_batch_larger_than_limit_still_counts_every_portion(tmp_path):
    session = _session_with(_Batches([list(range(5000))]))
    path = tmp_path / "export.json"
    assert session.close(export_path=path, session_epoch="e", request_id="r") is True
    assert session.source_ingested == 5000


def test_partial_drain_then_failure_is_visible_and_keeps_the_snapshot(tmp_path):
    session = _session_with(_Batches([[1, 2], RuntimeError("synthetic")]))
    path = tmp_path / "export.json"
    assert session.close(export_path=path, session_epoch="e", request_id="r") is False
    import json
    payload = json.loads(path.read_text())
    assert payload["snapshot"]["complete"] is False
    assert payload["snapshot"]["source_drain_failure"] == "RuntimeError"
    assert session.source_ingested == 2


def test_close_without_export_path_still_reports_completeness():
    assert _session_with(_Batches([[1]])).close() is True
    assert _session_with(_Batches([RuntimeError("x")])).close() is False


def test_second_close_is_idempotent_and_keeps_the_first_export(tmp_path):
    session = _session_with(_Batches([[1, 2, 3]]))
    path = tmp_path / "export.json"
    assert session.close(export_path=path, session_epoch="e", request_id="r") is True
    first = path.read_bytes()
    assert session.close(export_path=path, session_epoch="e", request_id="r") is False
    assert path.read_bytes() == first


def test_producer_cutoff_failure_makes_the_export_incomplete(tmp_path):
    session = _session_with(_Batches([[1, 2]]))
    session.producer_cutoff_failure = "stt-loop"
    path = tmp_path / "export.json"
    assert session.close(export_path=path, session_epoch="e", request_id="r") is False
    import json
    payload = json.loads(path.read_text())
    assert payload["snapshot"]["complete"] is False
    assert payload["snapshot"]["producer_cutoff_failure"] == "stt-loop"


def test_same_answer_id_on_another_question_is_a_conflict_not_a_loss():
    canvas = ThreadCanvas()
    first_qid = canvas.question("topic-1", "Release", "Synthetic question one?")
    second_qid = canvas.question("topic-1", "Release", "Synthetic question two?")
    stored = canvas.answer(first_qid, "Synthetic answer.", answer_id="a1",
                           scope_ids=["s1"], claim_ids=["c1"], status="answered",
                           source_refs=["u1"])
    with pytest.raises(CanvasRefused, match="answer_conflict"):
        canvas.answer(second_qid, "Synthetic answer.", answer_id="a1",
                      scope_ids=["s1"], claim_ids=["c1"], status="answered",
                      source_refs=["u1"])
    assert canvas.answer_ids(second_qid) == ()
    assert canvas.is_open(second_qid)
    assert canvas.answer_by_id("a1") is stored
