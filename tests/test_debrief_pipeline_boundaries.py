"""Identity and recovery boundaries for synthetic, local refinement transactions."""
import errno
import hashlib
import json

import pytest

import debrief_pipeline as pipeline
import live_sidecar
import meeting_stamp
from debrief_refinement import RefinementRefused
from test_debrief_pipeline import CFG, make_meeting, model, previous_debrief, run  # noqa: F401


pytestmark = pytest.mark.usefixtures("model")


@pytest.fixture
def operation_dir(tmp_path):
    path = tmp_path / "operation"
    path.mkdir()
    return path


# Stop identity, exclusive receipts and delivery deadlines


def test_handoff_retry_accepts_only_the_original_stop_nonce(tmp_path):
    stamp = "2026-01-10_093001"
    live = tmp_path / f"{stamp}.md"
    original = pipeline.new_handoff(live, stamp)
    pipeline.remember_handoff(live, original)
    path = pipeline.handoff_path(live)
    original_file = path.stat().st_ino
    pipeline.remember_handoff(live, original.copy())
    assert path.stat().st_ino == original_file
    with pytest.raises(RefinementRefused, match="handoff_conflict"):
        pipeline.remember_handoff(live, pipeline.new_handoff(live, stamp))
    assert pipeline.pending_handoff(live) == original


def test_background_handoff_round_trip_with_other_cli_options(tmp_path):
    stamp = "2026-01-10_093001"
    live = tmp_path / f"{stamp}.md"
    handoff = pipeline.new_handoff(live, stamp)
    args = ["--input", str(live), *pipeline.arguments(handoff), "--graph"]
    assert pipeline.from_arguments(args) == handoff
    with pytest.raises(RefinementRefused, match="stop_arguments"):
        pipeline.from_arguments(["--graph", "--debrief-handoff"])


@pytest.mark.parametrize("change", [
    {"nonce": "A" * 48}, {"nonce": "0" * 47}, {"stamp": 10}, {"stamp": "invalid"},
    {"owner": "someone.md"}, {"file": "wrong.json"}, {"extra": 1},
])
def test_invalid_stop_identity_is_refused_before_creating_a_receipt(operation_dir, change):
    stamp = "2026-01-10_093001"
    live = operation_dir / f"{stamp}.md"
    with pytest.raises(RefinementRefused, match="stop_identity"):
        pipeline.remember_handoff(live, {**pipeline.new_handoff(live, stamp), **change})
    assert not list(operation_dir.iterdir())


def test_identity_writer_and_reader_round_trip_the_maximum_unicode_document(tmp_path):
    path = tmp_path / "receipt.json"
    prefix = {"label": "Повтор", "payload": ""}
    overhead = len(json.dumps(prefix, ensure_ascii=False).encode("utf-8"))
    value = {**prefix, "payload": "a" * (65536 - overhead)}
    pipeline._exclusive_json(path, value)
    assert path.stat().st_size == 65536 and pipeline._read_json(path) == value
    with pytest.raises(RefinementRefused, match="identity_too_large"):
        pipeline._exclusive_json(tmp_path / "oversized.json", {**value, "payload": value["payload"] + "я"})
    assert not (tmp_path / "oversized.json").exists()


def test_exclusive_receipt_temp_collision_leaves_foreign_file_in_place(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    monkeypatch.setattr(pipeline.secrets, "token_hex", lambda _: "collision")
    foreign = tmp_path / "receipt.json.tmp-collision"
    foreign.write_bytes(b"another writer is still preparing this file")
    before = foreign.stat().st_ino
    with pytest.raises(FileExistsError):
        pipeline._exclusive_json(path, {"owner": "ours"})
    assert foreign.stat().st_ino == before
    assert foreign.read_bytes() == b"another writer is still preparing this file"
    assert not path.exists()


def test_overlapping_receipt_writers_publish_one_complete_document(operation_dir, monkeypatch):
    path = operation_dir / "receipt.json"
    first, second = {"receipt": "first"}, {"receipt": "second"}
    original_link = pipeline.os.link
    competing = False

    def publish_competitor_then_link(temp, target):
        nonlocal competing
        if not competing:
            competing = True
            pipeline._exclusive_json(path, second)
        return original_link(temp, target)

    monkeypatch.setattr(pipeline.os, "link", publish_competitor_then_link)
    with pytest.raises(FileExistsError):
        pipeline._exclusive_json(path, first)
    assert pipeline._read_json(path) == second and list(operation_dir.iterdir()) == [path]


def test_receipt_remains_successful_if_temp_link_is_removed_after_publication(operation_dir, monkeypatch):
    path = operation_dir / "receipt.json"
    link = pipeline.os.link

    def publish_and_cleanup_temp(temp, target):
        link(temp, target)
        temp.unlink()

    monkeypatch.setattr(pipeline.os, "link", publish_and_cleanup_temp)
    pipeline._exclusive_json(path, {"status": "ready"})
    assert pipeline._read_json(path) == {"status": "ready"} and list(operation_dir.iterdir()) == [path]


@pytest.mark.parametrize("entry", ["terminal", "prepare", "run"])
def test_terminal_wait_accepts_receipt_arriving_before_deadline(tmp_path, monkeypatch, entry):
    live, handoff = make_meeting(tmp_path)
    receipt = pipeline.receipt_path(live, handoff)
    saved = tmp_path / "not-yet-delivered"
    receipt.rename(saved)
    now = 0.0

    def advance(seconds):
        nonlocal now
        assert seconds > 0
        now += seconds
        if now >= 0.12 and saved.exists():
            saved.rename(receipt)

    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now)
    monkeypatch.setattr(pipeline.time, "sleep", advance)
    if entry == "terminal":
        assert pipeline._terminal(live, handoff, timeout=0.15)["handoff"] == handoff
        assert 0.12 <= now <= 0.15
    elif entry == "prepare":
        assert pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=live_sidecar.sha(live.read_text()))
    else:
        result = pipeline.run(live, CFG, handoff=handoff, final_sha=live_sidecar.sha(live.read_text()),
                              before_request=lambda: None)
        assert result.is_file() and pipeline.verify_publication(live) == result


def test_unavailable_receipt_with_zero_timeout_fails_without_sleep(tmp_path, monkeypatch):
    stamp = "2026-01-10_093001"
    live = tmp_path / f"{stamp}.md"
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: 12.5)
    monkeypatch.setattr(pipeline.time, "sleep", lambda _: pytest.fail("unexpected wait with zero timeout"))
    with pytest.raises(RefinementRefused, match="identity_unreadable") as error:
        pipeline._terminal(live, pipeline.new_handoff(live, stamp), timeout=0)
    assert isinstance(error.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("field,value", [
    ("stamp", "2026-01-01_120002"), ("file", "neighbor.grounded.json"),
    ("handoff", {"nonce": "foreign"}), ("status", "failed"),
])
def test_terminal_requires_matching_stop_and_ready_export(tmp_path, field, value):
    live, handoff = make_meeting(tmp_path)
    path = pipeline.receipt_path(live, handoff)
    record = pipeline._read_json(path)
    if field in {"handoff", "status"}:
        record[field] = value
        reason = "stop_incomplete"
    else:
        record["export"][field] = value
        reason = "stop_export_identity"
    path.write_text(json.dumps(record))
    with pytest.raises(RefinementRefused, match=reason):
        pipeline._terminal(live, handoff)


# Canonical speech, binding and graph proof


def bound_source(tmp_path):
    live, handoff = make_meeting(tmp_path)
    pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=live_sidecar.sha(live.read_text()), timeout=0)
    return live


def test_graph_preparation_accepts_reordered_equivalent_receipt(tmp_path):
    live, handoff = make_meeting(tmp_path)
    proof = pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=live_sidecar.sha(live.read_text()))
    path = pipeline.receipt_path(live, handoff)
    receipt = pipeline._read_json(path)
    path.write_text(json.dumps(dict(reversed(receipt.items()))))
    assert pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None) == proof


def test_repeated_stop_can_reuse_only_the_original_established_binding(tmp_path):
    live, handoff = make_meeting(tmp_path)
    sha = live_sidecar.sha(live.read_text())
    proof = pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=sha, timeout=0)
    path = pipeline.binding_path(live)
    original = path.read_bytes()
    assert pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=sha, timeout=0) == proof
    replacement = pipeline.new_handoff(live, handoff["stamp"])
    pipeline.finish_stop(live, replacement, complete=True)
    with pytest.raises(RefinementRefused, match="binding_conflict"):
        pipeline.prepare_for_graph(live, CFG, handoff=replacement, final_sha=sha, timeout=0)
    assert path.read_bytes() == original


@pytest.mark.parametrize("boundary", ["during_stop_validation", "after_binding"])
def test_header_only_change_is_detected_at_each_source_boundary(tmp_path, monkeypatch, boundary):
    live, handoff = make_meeting(tmp_path)
    original = live.read_text()
    hook = "_terminal" if boundary == "during_stop_validation" else "_establish"
    operation = getattr(pipeline, hook)

    def edit_header_on_return(*args, **kwargs):
        value = operation(*args, **kwargs)
        live.write_text(original.replace("\n", " — Новый заголовок\n", 1))
        return value

    monkeypatch.setattr(pipeline, hook, edit_header_on_return)
    with pytest.raises(RefinementRefused, match="source_changed"):
        pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=live_sidecar.sha(original), timeout=0)
    assert pipeline.binding_path(live).exists() == (boundary == "after_binding")


def test_only_a_proven_rebuild_refreshes_a_stale_binding(tmp_path):
    live = bound_source(tmp_path)
    binding = pipeline.binding_path(live)
    previous = binding.read_bytes()
    live.write_text(live.read_text().replace("\n", " — Новый заголовок\n", 1))
    sha = live_sidecar.sha(live.read_text())
    passport = live.with_name(live.name + ".live.json")
    passport.write_text(json.dumps({**pipeline._read_json(passport), "transcript_sha256": sha}))
    with pytest.raises(RefinementRefused, match="canonical_final_unproven"):
        pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None, timeout=0)
    assert binding.read_bytes() == previous
    proof = pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=sha, timeout=0)
    assert pipeline._read_json(binding)["full_sha256"] == sha
    assert pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None, timeout=0) == proof


@pytest.mark.parametrize("field,value", [("stamp", "2026-01-01_120002"), ("transcript_sha256", "0" * 64)])
def test_each_passport_identity_must_match_the_bound_source(tmp_path, field, value):
    live, handoff = make_meeting(tmp_path)
    passport = live.with_name(live.name + ".live.json")
    passport.write_text(json.dumps({**pipeline._read_json(passport), field: value}))
    with pytest.raises(RefinementRefused, match="canonical_passport"):
        pipeline.prepare_for_graph(live, CFG, handoff=handoff, final_sha=live_sidecar.sha(live.read_text()), timeout=0)


# Installing the candidate and recovering an interrupted publication


def interrupted_publication(tmp_path, *, claimed=True, **state):
    live = tmp_path / "meeting.md"
    destination = tmp_path / "summary.md"
    passport = live.with_name(live.name + ".live.json")
    backup = tmp_path / ".prev" / "previous.md"
    backup.parent.mkdir()
    previous = b"prior document"
    candidate = b"candidate document"
    backup.write_bytes(previous)
    destination.write_bytes(candidate)
    previous_sha = hashlib.sha256(previous).hexdigest()
    candidate_sha = hashlib.sha256(candidate).hexdigest()
    old_binding = {"owner": live.name}
    binding = {**old_binding, "publication_id": "tx"} if claimed else old_binding
    pipeline._exclusive_json(pipeline.binding_path(live), binding)
    pipeline._exclusive_json(passport, {"debrief_sha256": candidate_sha, "manual": "preserve"})
    journal = {"owner": live.name, "id": "tx", "file": destination.name, "backup": backup.name,
               "binding": old_binding, "passport": {"debrief_sha256": previous_sha},
               "installed_identity": pipeline._file_identity(destination.stat()),
               "candidate_sha256": candidate_sha, "previous_sha256": previous_sha, **state}
    pipeline._exclusive_json(pipeline.publication_path(live), journal)
    return live, destination, passport, journal


def test_journal_updates_use_the_utf8_byte_limit_inclusively(tmp_path):
    live = tmp_path / "meeting.md"
    path = pipeline.publication_path(live)
    initial = {"title": "Разбор", "note": ""}
    pipeline._exclusive_json(path, initial)
    overhead = len(json.dumps(initial, ensure_ascii=False).encode("utf-8"))
    updated = pipeline._update_publication(live, initial, {"note": "a" * (65536 - overhead)})
    data = path.read_bytes()
    assert len(data) == 65536
    assert pipeline._read_json(path) == updated
    with pytest.raises(RefinementRefused, match="identity_too_large"):
        pipeline._update_publication(live, updated, {"note": updated["note"] + "b"})
    assert path.read_bytes() == data


@pytest.mark.parametrize("changes", [
    {"owner": "another.md"}, {"file": 2}, {"file": "elsewhere/summary.md"},
    {"backup": False}, {"backup": "elsewhere/previous.md"},
])
def test_malformed_journal_cannot_consume_rollback_evidence(tmp_path, changes):
    live = tmp_path / "meeting.md"
    path = pipeline.publication_path(live)
    backup = tmp_path / ".prev" / "previous.md"
    backup.parent.mkdir()
    backup.write_bytes(b"synthetic previous document")
    journal = {"owner": live.name, "file": "summary.md", "backup": backup.name, **changes}
    pipeline._exclusive_json(path, journal)
    with pytest.raises(RefinementRefused, match="publication_journal"):
        pipeline.recover_publication(live)
    assert pipeline._read_json(path) == journal
    assert backup.read_bytes() == b"synthetic previous document"


@pytest.mark.parametrize("backup_removed", [False, True])
def test_journal_creation_failure_cleans_unclaimed_backup_and_preserves_user_file(tmp_path, monkeypatch,
                                                                                  backup_removed):
    live, handoff = make_meeting(tmp_path)
    destination, data = previous_debrief(live)
    original = pipeline._exclusive_json

    def no_space_for_journal(path, value):
        if path == pipeline.publication_path(live):
            if backup_removed:
                (tmp_path / ".prev" / value["backup"]).unlink()
            raise OSError(errno.ENOSPC, "synthetic full disk")
        return original(path, value)

    monkeypatch.setattr(pipeline, "_exclusive_json", no_space_for_journal)
    with pytest.raises(OSError) as error:
        run(live, handoff)
    assert error.value.errno == errno.ENOSPC
    assert destination.read_bytes() == data
    assert not pipeline.publication_path(live).exists() and not list((tmp_path / ".prev").glob("*.rollback-*"))


def test_duplicate_private_backup_is_preserved_before_publication(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    nonce = "ab" * 16
    foreign = tmp_path / ".prev" / (destination.name + ".rollback-" + nonce)
    foreign.parent.mkdir(exist_ok=True)
    foreign.write_bytes(b"orphan owned by a different transaction")
    token = pipeline.secrets.token_hex
    monkeypatch.setattr(pipeline.secrets, "token_hex", lambda n: nonce if n == 16 else token(n))
    with pytest.raises(RefinementRefused, match="rollback_backup_conflict"):
        run(live, handoff)
    assert foreign.read_bytes() == b"orphan owned by a different transaction"
    assert destination.read_bytes() == previous and not pipeline.publication_path(live).exists()


def test_disappeared_private_backup_preserves_original_sync_error(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    sync = pipeline._sync_files_and_parent

    def fail_private_backup(path, *siblings):
        if path.parent.name == ".prev":
            path.unlink()
            raise OSError(errno.EIO, "backup lost before fsync")
        return sync(path, *siblings)

    monkeypatch.setattr(pipeline, "_sync_files_and_parent", fail_private_backup)
    with pytest.raises(OSError) as error:
        run(live, handoff)
    assert error.value.errno == errno.EIO and destination.read_bytes() == previous
    assert not pipeline.publication_path(live).exists()


def test_failure_before_installation_cleans_unclaimed_journal_despite_read_error(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    journal = pipeline.publication_path(live)
    read = pipeline._read_json

    def storage_read_error(path):
        if journal.exists() and path in {pipeline.binding_path(live), journal}:
            raise OSError(errno.EIO, "metadata read failed", str(path))
        return read(path)

    monkeypatch.setattr(pipeline, "_read_json", storage_read_error)
    with pytest.raises(OSError) as error:
        run(live, handoff)
    assert error.value.filename == str(pipeline.binding_path(live))
    assert destination.read_bytes() == previous and not journal.exists()
    assert not list((tmp_path / ".prev").glob("*.rollback-*"))


def test_candidate_losing_its_passport_before_hook_cannot_reach_the_hook(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    passport = live.with_name(live.name + ".live.json")
    write = live_sidecar.write_derivative

    def revise_passport(*args, **kwargs):
        result = write(*args, **kwargs)
        meta = pipeline._read_json(passport)
        passport.write_text(json.dumps({**meta, "debrief_sha256": "0" * 64}))
        return result

    monkeypatch.setattr(live_sidecar, "write_derivative", revise_passport)
    with pytest.raises(RefinementRefused, match="publication_unconfirmed"):
        run(live, handoff, before_commit=lambda: pytest.fail("hook received an unconfirmed candidate"))
    assert destination.read_bytes() == previous


def test_stop_export_replaced_at_binding_commit_invalidates_candidate(tmp_path, monkeypatch):
    from grounded_feed.export import write_snapshot

    live, handoff = make_meeting(tmp_path)
    destination, previous = previous_debrief(live)
    write = pipeline.safe_write.write_text

    def replace_stop_after_binding(path, data, **kwargs):
        written = write(path, data, **kwargs)
        if written and path == pipeline.binding_path(live) and "publication_id" in json.loads(data):
            export = tmp_path / handoff["file"]
            payload = json.loads(export.read_text())
            payload["snapshot"]["blocks"][0]["text"] = "Уточнили другой вопрос."
            write_snapshot(export, payload)
            receipt = pipeline.receipt_path(live, handoff)
            record = pipeline._read_json(receipt)
            record["export"] = pipeline.refinement.receipt(export, handoff["stamp"])
            receipt.write_text(json.dumps(record))
        return written

    monkeypatch.setattr(pipeline.safe_write, "write_text", replace_stop_after_binding)
    with pytest.raises(RefinementRefused, match="source_changed"):
        run(live, handoff)
    assert destination.read_bytes() == previous
    assert "publication_id" not in pipeline._read_json(pipeline.binding_path(live))
    assert not pipeline.publication_path(live).exists()


@pytest.mark.parametrize("field,value", [
    ("owner", "neighbor.md"), ("full_sha256", "0" * 64), ("source_sha256", "0" * 64),
    ("publication_id", ""), ("debrief_file", "neighbor/summary.md"),
])
def test_publication_readback_requires_each_binding_identity(tmp_path, field, value):
    live, handoff = make_meeting(tmp_path)
    destination = run(live, handoff)
    expected = destination.read_bytes()
    path = pipeline.binding_path(live)
    path.write_text(json.dumps({**pipeline._read_json(path), field: value}))
    with pytest.raises(RefinementRefused, match="publication_source_changed"):
        pipeline.verify_publication(live)
    assert destination.read_bytes() == expected


@pytest.mark.parametrize("previous_flag", ["rejected", "rollback_started"])
def test_retry_does_not_need_to_rewrite_a_durable_rejection(tmp_path, monkeypatch, previous_flag):
    live, destination, passport, journal = interrupted_publication(tmp_path, **{previous_flag: True})
    write = pipeline.safe_write.write_text
    marks = []

    def record(path, text, **kwargs):
        if path == pipeline.publication_path(live):
            marks.append(json.loads(text))
        return write(path, text, **kwargs)

    monkeypatch.setattr(pipeline.safe_write, "write_text", record)
    pipeline.recover_publication(live, rejected=True)
    assert all(mark != journal for mark in marks)
    assert len(marks) == (1 if previous_flag == "rejected" else 0)
    assert destination.read_bytes() == b"prior document"
    assert pipeline._read_json(passport) == {"debrief_sha256": journal["previous_sha256"], "manual": "preserve"}


def test_existing_rollback_marker_prevents_readmission_even_on_finalization(tmp_path, monkeypatch):
    live, destination, _, _ = interrupted_publication(tmp_path, rollback_started=True)
    monkeypatch.setattr(pipeline, "verify_publication", lambda *_: pytest.fail("admitted a transaction in rollback"))
    pipeline.recover_publication(live, finalize=True)
    assert destination.read_bytes() == b"prior document"
    assert "publication_id" not in pipeline._read_json(pipeline.binding_path(live))


@pytest.mark.parametrize("started", [False, True])
def test_unclaimed_replaced_document_does_not_restore_a_passport_for_different_bytes(tmp_path, started):
    live, destination, passport, journal = interrupted_publication(tmp_path, claimed=False, rollback_started=started)
    replacement = tmp_path / "replacement"
    # A matching previous body alone carries no right to compensate unless
    # this transaction had actually started rollback.
    expected = b"human revision" if started else b"prior document"
    replacement.write_bytes(expected)
    replacement.replace(destination)
    original_passport = passport.read_bytes()
    pipeline.recover_publication(live)
    assert destination.read_bytes() == expected and passport.read_bytes() == original_passport
    assert not pipeline.publication_path(live).exists()
    assert pipeline._read_json(passport)["debrief_sha256"] == journal["candidate_sha256"]


@pytest.mark.parametrize("cause,transient", [(PermissionError, True), (FileNotFoundError, False)])
def test_committed_recovery_distinguishes_unreadable_from_missing_evidence(tmp_path, monkeypatch, cause, transient):
    live, destination, _, journal = interrupted_publication(tmp_path)

    def unavailable(_live):
        raise RefinementRefused("identity_unreadable") from cause("synthetic evidence")

    monkeypatch.setattr(pipeline, "verify_publication", unavailable)
    with pytest.raises(RefinementRefused, match="identity_unreadable" if transient else "publication_unconfirmed"):
        pipeline.recover_publication(live)
    if transient:
        assert pipeline._read_json(pipeline.publication_path(live)) == journal
        assert destination.read_bytes() == b"candidate document"
    else:
        assert not pipeline.publication_path(live).exists()
        assert destination.read_bytes() == b"prior document"


@pytest.mark.parametrize("missing", ["destination", "passport"])
def test_committed_recovery_distinguishes_disappeared_candidate_from_other_file(tmp_path, missing):
    live, destination, passport, journal = interrupted_publication(tmp_path)
    if missing == "destination":
        destination.unlink()
        with pytest.raises(RefinementRefused, match="publication_unconfirmed"):
            pipeline.recover_publication(live)
        assert not destination.exists() and not pipeline.publication_path(live).exists()
        assert "publication_id" not in pipeline._read_json(pipeline.binding_path(live))
    else:
        passport.unlink()
        with pytest.raises(FileNotFoundError) as error:
            pipeline.recover_publication(live)
        assert error.value.filename == str(passport)
        assert pipeline._read_json(pipeline.publication_path(live)) == journal
        assert destination.read_bytes() == b"candidate document"


def test_cleanup_retry_with_already_removed_backup_needs_no_operator_warning(tmp_path, caplog):
    live, _, _, journal = interrupted_publication(tmp_path)
    backup = tmp_path / ".prev" / journal["backup"]
    backup.unlink()
    pipeline._discard_publication(live, journal)
    assert not pipeline.publication_path(live).exists() and not backup.exists()
    assert not any(record.name == pipeline.__name__ and record.levelname == "WARNING" for record in caplog.records)


# Parent acknowledgement


@pytest.mark.parametrize("proof", [None, "../neighbor", "A" * 64, "a" * 63])
def test_parent_reject_requires_a_valid_proof_even_when_the_pointer_is_absent(operation_dir, proof):
    with pytest.raises(RefinementRefused, match="parent_publication_identity"):
        pipeline.parent_publication(operation_dir / "meeting.md", proof, reject=True)
    assert not list(operation_dir.iterdir())


@pytest.mark.parametrize("journal_change,ack_change", [
    ({"pending_finalization": False}, {}), ({"binding": {}}, {}), ({}, {"id": "other-child"}),
    ({}, {"owner": "neighbor/file.md"}), ({}, {"owner": 1}), ({}, {"proof": "b" * 64}), ({}, {"extra": True}),
])
def test_parent_reject_requires_both_pending_transaction_and_exact_child(tmp_path, journal_change, ack_change):
    stamp = "2026-01-10_093001"
    live = tmp_path / f"{stamp}.md"
    handoff = pipeline.new_handoff(live, stamp)
    proof = "a" * 64
    journal_path = pipeline.publication_path(live)
    pointer = pipeline._parent_ack_path(live, proof)
    journal = {"owner": live.name, "id": "child-id", "graph_input_sha": proof, "binding": {"handoff": handoff},
               "file": "summary.md", "pending_finalization": True, **journal_change}
    ack = {"owner": live.name, "id": "child-id", "proof": proof, "handoff": handoff, **ack_change}
    pipeline._exclusive_json(journal_path, journal)
    pipeline._exclusive_json(pointer, ack)
    with pytest.raises(RefinementRefused, match="parent_publication_identity"):
        pipeline.parent_publication(live, proof, reject=True)
    assert pipeline._read_json(journal_path) == journal and pipeline._read_json(pointer) == ack


@pytest.mark.parametrize("reject", [False, True])
def test_parent_may_ignore_only_an_absent_pointer_on_rejection(tmp_path, reject):
    live = tmp_path / "meeting.md"
    proof = "b" * 64
    if reject:
        assert pipeline.parent_publication(live, proof, reject=True) is None
    else:
        with pytest.raises(RefinementRefused, match="identity_unreadable") as error:
            pipeline.parent_publication(live, proof)
        assert isinstance(error.value.__cause__, FileNotFoundError)
    pointer = pipeline._parent_ack_path(live, proof)
    pointer.write_bytes(b"{")
    with pytest.raises(RefinementRefused, match="identity_unreadable"):
        pipeline.parent_publication(live, proof, reject=reject)
    assert pointer.read_bytes() == b"{"


@pytest.mark.parametrize("decision", ["reject", "accept", "verification_error", "withdrawn"])
def test_parent_cannot_resolve_a_replaced_or_withdrawn_child_after_its_first_read(tmp_path, monkeypatch, decision):
    from test_debrief_finalization import pending

    live, destination, _, first_proof = pending(tmp_path, monkeypatch)
    journal = pipeline.publication_path(live)
    read = pipeline._read_json
    switched = False
    second_files = {}

    def read_before_next_publication(path):
        nonlocal switched
        value = read(path)
        if path == journal and not switched:
            switched = True
            pipeline.parent_publication(live, first_proof, reject=True)
            if decision == "withdrawn":
                second_files[destination] = destination.read_bytes()
                return value
            proof = pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None, timeout=0)
            pipeline.run(live, CFG, handoff=None, final_sha=None, before_request=lambda: None, timeout=0,
                         graph_input_sha=proof, defer_finalization=True)
            second = read(journal)
            assert value["id"] != second["id"]
            paths = [journal, destination, pipeline.binding_path(live), live.with_name(live.name + ".live.json"),
                     pipeline._parent_ack_path(live, proof), tmp_path / ".prev" / second["backup"]]
            second_files.update({item: item.read_bytes() for item in paths})
            if decision == "verification_error":
                def unavailable(_live):
                    raise OSError(errno.EIO, "synthetic readback failure")
                monkeypatch.setattr(pipeline, "verify_publication", unavailable)
        return value

    monkeypatch.setattr(pipeline, "_read_json", read_before_next_publication)
    with pytest.raises(RefinementRefused, match="parent_publication_identity"):
        pipeline.parent_publication(live, first_proof, reject=decision == "reject")
    assert switched and second_files
    assert all(item.read_bytes() == body for item, body in second_files.items())
    if decision == "withdrawn":
        assert not journal.exists() and not pipeline._parent_ack_path(live, first_proof).exists()


# Retitle reservation, compensation and ownership


def test_unbound_unchanged_retitle_completes_without_a_binding(operation_dir):
    live = operation_dir / "legacy.md"
    live.write_bytes(b"synthetic speech")
    pipeline.retitle_binding(live, live)
    assert list(operation_dir.iterdir()) == [live] and live.read_bytes() == b"synthetic speech"


@pytest.mark.parametrize("damage", ["source", "bad_name", "human_edit", "occupied", "unreadable", "foreign_binding",
                                    "old_stop", "new_publication"])
def test_bound_retitle_refuses_unproven_derivative_or_transcript(tmp_path, damage):
    old, handoff = make_meeting(tmp_path)
    origin, previous = previous_debrief(old)
    new = tmp_path / "2026-01-01_1200_Тема.md"
    pipeline.prepare_for_graph(old, CFG, handoff=handoff, final_sha=live_sidecar.sha(old.read_text()))
    assert pipeline.can_retitle(old, new) is True
    path = pipeline.binding_path(old)
    if damage == "source":
        old.write_text(old.read_text() + "Новая речь.\n")
    elif damage == "bad_name":
        neighbor = tmp_path / "neighbor" / "summary.md"
        neighbor.parent.mkdir()
        neighbor.write_bytes(previous)
        path.write_text(json.dumps({**pipeline._read_json(path), "debrief_file": "neighbor/summary.md"}))
    elif damage == "occupied":
        target = new.with_name(new.stem + meeting_stamp.DEBRIEF_FILE_SUFFIX)
        target.write_bytes(b"neighbor")
    elif damage == "human_edit":
        origin.write_bytes(b"human revision")
    elif damage == "foreign_binding":
        pipeline._exclusive_json(pipeline.binding_path(new), {"owner": "another.md"})
    elif damage == "old_stop":
        pipeline.remember_handoff(old, handoff)
    elif damage == "new_publication":
        pipeline._exclusive_json(pipeline.publication_path(new), {"owner": new.name})
    else:
        path.write_bytes(b"{")
    assert pipeline.can_retitle(old, new) is False
    assert origin.read_bytes() == (b"human revision" if damage == "human_edit" else previous)
    assert not new.exists()


@pytest.mark.parametrize("which", ["origin", "target"])
def test_retitle_move_preserves_symlink_and_its_referent(tmp_path, which):
    origin, target = tmp_path / "old.md", tmp_path / "new.md"
    foreign = tmp_path / "foreign.md"
    foreign.write_bytes(b"neighbor")
    if which == "origin":
        origin.symlink_to(foreign)
    else:
        origin.write_bytes(b"owner")
        target.symlink_to(foreign)
    with pytest.raises(RefinementRefused, match="retitle_file_conflict"):
        pipeline.move_retitle_file(origin, target)
    assert foreign.read_bytes() == b"neighbor"
    if which == "origin":
        assert origin.is_symlink() and not target.exists()
    else:
        assert target.is_symlink() and origin.read_bytes() == b"owner"


def test_retitle_cannot_reserve_a_binding_in_another_directory(tmp_path):
    live = bound_source(tmp_path)
    other = tmp_path / "neighbor"
    other.mkdir()
    before = pipeline.binding_path(live).read_bytes()
    with pytest.raises(RefinementRefused, match="retitle_source_changed"):
        pipeline.prepare_retitle(live, other / live.name)
    assert not list(other.iterdir()) and pipeline.binding_path(live).read_bytes() == before


@pytest.mark.parametrize("remaining", [0, -1])
def test_in_place_retitle_reservation_obeys_the_utf8_receipt_limit(tmp_path, remaining):
    live = bound_source(tmp_path)
    path = pipeline.binding_path(live)
    bound = {**pipeline._read_json(path), "note": "Тема"}
    pending = {**bound, "retitle_header": None}
    size = len(json.dumps(pending, ensure_ascii=False).encode("utf-8"))
    bound["note"] += "x" * (65536 - size - remaining)
    path.write_text(json.dumps(bound, ensure_ascii=False))
    if remaining < 0:
        previous = path.read_bytes()
        with pytest.raises(RefinementRefused, match="identity_too_large"):
            pipeline.prepare_retitle(live, live)
        assert path.read_bytes() == previous
    else:
        pipeline.prepare_retitle(live, live)
        assert path.stat().st_size == 65536 and pipeline._read_json(path) == {**bound, "retitle_header": None}
        pipeline.retitle_binding(live, live)
        assert pipeline._read_json(path) == bound


@pytest.mark.parametrize("with_debrief", [False, True])
@pytest.mark.parametrize("final_bytes", [65536, 65537])
def test_retitle_measures_completed_binding_before_reserving_a_new_name(operation_dir, with_debrief, final_bytes):
    old, handoff = make_meeting(operation_dir)
    if with_debrief:
        previous_debrief(old)
    proof = pipeline.prepare_for_graph(old, CFG, handoff=handoff, final_sha=live_sidecar.sha(old.read_text()), timeout=0)
    new = old.with_name("2026-01-01_1200_" + "Тема" * 8 + ".md")
    path = pipeline.binding_path(old)
    original = {**pipeline._read_json(path), "note": ""}
    final = {**original, "owner": new.name}
    if with_debrief:
        final["debrief_file"] = new.stem + meeting_stamp.DEBRIEF_FILE_SUFFIX
    final_size = len(json.dumps(final, ensure_ascii=False).encode("utf-8"))
    final["note"] = original["note"] = "x" * (final_bytes - final_size)
    path.write_text(json.dumps(original, ensure_ascii=False))
    assert len(json.dumps({**original, "retitle_header": None}, ensure_ascii=False).encode("utf-8")) < 65536
    assert len(json.dumps(final, ensure_ascii=False).encode("utf-8")) == final_bytes
    if final_bytes > 65536:
        files = {p: p.read_bytes() for p in operation_dir.iterdir()}
        with pytest.raises(RefinementRefused, match="identity_too_large"):
            pipeline.prepare_retitle(old, new)
        assert set(operation_dir.iterdir()) == set(files)
        assert all(p.read_bytes() == body for p, body in files.items())
        assert pipeline.prepare_for_graph(old, CFG, handoff=None, final_sha=None, timeout=0) == proof
    else:
        pipeline.prepare_retitle(old, new)
        pipeline.move_retitle_file(old, new)
        pipeline.recover_retitle(new)
        completed = pipeline.binding_path(new)
        assert completed.stat().st_size == final_bytes and pipeline._read_json(completed) == final


@pytest.mark.parametrize("changes,reason", [
    ({"retitle_header": ["one"]}, "retitle_header"),
    ({"retitle_header": ["a", ""]}, "retitle_header"),
    ({"owner": 1}, "retitle_owner"),
    ({"owner": "neighbor/file.md"}, "retitle_owner"),
    ({"owner": "file.json"}, "retitle_owner"),
])
def test_recovery_rejects_damaged_retitle_evidence_without_moving_files(tmp_path, changes, reason):
    live = bound_source(tmp_path)
    body = live.read_bytes()
    path = pipeline.binding_path(live)
    pipeline.prepare_retitle(live, live)
    value = {**pipeline._read_json(path), **changes}
    path.write_text(json.dumps(value))
    with pytest.raises(RefinementRefused, match=reason):
        pipeline.recover_retitle(live)
    assert live.read_bytes() == body and pipeline._read_json(path) == value


@pytest.mark.parametrize("bad_name", [123, "neighbor/summary.md"])
def test_retitle_recovery_refuses_malformed_derivative_before_moving_it(tmp_path, bad_name):
    old, handoff = make_meeting(tmp_path)
    origin, previous = previous_debrief(old)
    pipeline.prepare_for_graph(old, CFG, handoff=handoff, final_sha=live_sidecar.sha(old.read_text()))
    path = pipeline.binding_path(old)
    value = {**pipeline._read_json(path), "debrief_file": bad_name}
    path.write_text(json.dumps(value))
    pipeline.prepare_retitle(old, old)
    pending = path.read_bytes()
    with pytest.raises(RefinementRefused, match="retitle_debrief_conflict"):
        pipeline.recover_retitle(old)
    assert origin.read_bytes() == previous and path.read_bytes() == pending


@pytest.mark.parametrize("damage", ["derivative", "stamp", "transcript_sha256"])
def test_reserved_retitle_rechecks_derivative_and_passport_proofs(tmp_path, damage):
    old, handoff = make_meeting(tmp_path)
    derivative, prior_bytes = previous_debrief(old)
    pipeline.prepare_for_graph(old, CFG, handoff=handoff, final_sha=live_sidecar.sha(old.read_text()), timeout=0)
    pipeline.prepare_retitle(old, old)
    passport = old.with_name(old.name + ".live.json")
    if damage == "derivative":
        derivative.write_bytes(b"human revision")
    else:
        passport.write_text(json.dumps({**pipeline._read_json(passport), damage: "other meeting"}))
    before = passport.read_bytes()
    reason = "retitle_debrief_changed" if damage == "derivative" else "retitle_passport_changed"
    with pytest.raises(RefinementRefused, match=reason):
        pipeline.recover_retitle(old)
    assert derivative.read_bytes() == (b"human revision" if damage == "derivative" else prior_bytes)
    assert passport.read_bytes() == before
    assert "retitle_header" in pipeline._read_json(pipeline.binding_path(old))


def test_retitle_recovery_cannot_unlink_old_name_after_its_binding_disappears(tmp_path):
    old = bound_source(tmp_path)
    new = old.with_name("2026-01-01_1200_Тема.md")
    pipeline.prepare_retitle(old, new)
    new.hardlink_to(old)
    pipeline.binding_path(old).unlink()
    with pytest.raises(RefinementRefused, match="retitle_source_changed"):
        pipeline.recover_retitle(new)
    assert old.samefile(new)
    assert "retitle_header" in pipeline._read_json(pipeline.binding_path(new))


def test_passport_updated_during_rename_keeps_the_manual_edit(tmp_path, monkeypatch):
    old = bound_source(tmp_path)
    new = old.with_name("2026-01-01_1200_Тема.md")
    header = old.read_text().splitlines()[0]
    pipeline.prepare_retitle(old, new, header=(header, header + " — Тема"))
    pipeline.move_retitle_file(old, new)
    move = pipeline.move_retitle_file
    passport = new.with_name(new.name + ".live.json")

    def edit_after_move(origin, target):
        move(origin, target)
        if target == passport:
            passport.write_text(json.dumps({**pipeline._read_json(passport), "manual": "Сохранить"}))

    monkeypatch.setattr(pipeline, "move_retitle_file", edit_after_move)
    with pytest.raises(RefinementRefused, match="retitle_passport_changed"):
        pipeline.recover_retitle(new)
    assert pipeline._read_json(passport)["manual"] == "Сохранить"
    assert "retitle_header" in pipeline._read_json(pipeline.binding_path(new))


def test_retitle_retry_refuses_source_equivalent_header_edits_beyond_reserved_change(tmp_path):
    live = bound_source(tmp_path)
    old = live.read_text()
    original = old.splitlines()[0]
    requested = original + " — Запрошенная тема"
    pipeline.prepare_retitle(live, live, header=(original, requested))
    changed = old.replace(original, requested + " — Посторонняя тема", 1)
    live.write_text(changed)
    marker = pipeline.binding_path(live)
    before = marker.read_bytes()
    with pytest.raises(RefinementRefused, match="retitle_source_changed"):
        pipeline.recover_retitle(live)
    assert live.read_text() == changed and marker.read_bytes() == before

def test_header_retitle_preserves_human_readable_passport_text(tmp_path):
    old = bound_source(tmp_path)
    passport = old.with_name(old.name + ".live.json")
    note = "Сохранить имена и произношение"
    meta = {**pipeline._read_json(passport), "note": note}
    passport.write_text(json.dumps(meta, ensure_ascii=False))
    header = old.read_text().splitlines()[0]
    pipeline.prepare_retitle(old, old, header=(header, header + " — Тема"))
    pipeline.retitle_binding(old, old)
    assert pipeline._read_json(passport) == {**meta, "transcript_sha256": live_sidecar.sha(old.read_text())}
    assert note in passport.read_text()
