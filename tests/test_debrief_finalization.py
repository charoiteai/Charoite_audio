"""Synthetic parent/child publication barriers; no models or real meeting data."""
import json

import pytest

import debrief_pipeline as pipeline
import debrief_refinement as refinement
from test_debrief_pipeline import CFG, make_meeting, model, run  # noqa: F401

pytestmark = pytest.mark.usefixtures("model")


def pending(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination = run(live, handoff)
    previous = destination.read_bytes()
    proof = pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None, timeout=0)
    monkeypatch.setattr(refinement, "refine", lambda *a, **kw: "# Разбор встречи\n\nНовая формулировка.\n")
    pipeline.run(live, CFG, handoff=None, final_sha=None, before_request=lambda: None,
                 timeout=0, graph_input_sha=proof, defer_finalization=True)
    return live, destination, previous, proof


def test_parent_checks_keep_rollback_until_final_acceptance(tmp_path, monkeypatch):
    live, destination, previous, proof = pending(tmp_path, monkeypatch)
    assert destination.read_bytes() != previous
    assert pipeline.publication_path(live).exists()
    assert pipeline._parent_ack_path(live, proof).exists()
    assert pipeline.parent_publication(live, proof) == live
    pipeline.verify_publication(live)
    assert not pipeline.publication_path(live).exists()
    assert not pipeline._parent_ack_path(live, proof).exists()


def test_missing_note_or_parent_failure_restores_previous(tmp_path, monkeypatch):
    live, destination, previous, proof = pending(tmp_path, monkeypatch)
    pipeline.parent_publication(live, proof, reject=True)
    assert destination.read_bytes() == previous
    assert not pipeline.publication_path(live).exists()


def test_post_child_speech_change_refuses_and_restores_previous(tmp_path, monkeypatch):
    live, destination, previous, proof = pending(tmp_path, monkeypatch)
    live.write_text(live.read_text() + "\nНовая речь после пользовательского хука.\n")
    with pytest.raises(refinement.RefinementRefused):
        pipeline.parent_publication(live, proof)
    assert destination.read_bytes() == previous
    assert not pipeline.publication_path(live).exists()


def test_unacknowledged_child_recovery_rolls_back(tmp_path, monkeypatch):
    live, destination, previous, proof = pending(tmp_path, monkeypatch)
    pipeline.recover_publication(live)
    assert destination.read_bytes() == previous
    assert not pipeline._parent_ack_path(live, proof).exists()


def test_failed_parent_never_restores_over_human_debrief(tmp_path, monkeypatch):
    live, destination, previous, proof = pending(tmp_path, monkeypatch)
    human = "# Моя заметка\n\nПравка человека.\n"
    destination.write_text(human)
    pipeline.parent_publication(live, proof, reject=True)
    assert destination.read_text() == human


def test_parent_uses_exact_pointer_not_neighbor_or_original_stem(tmp_path, monkeypatch):
    live, destination, previous, proof = pending(tmp_path, monkeypatch)
    pointer = pipeline._parent_ack_path(live, proof)
    ack = json.loads(pointer.read_text())
    pointer.write_text(json.dumps({**ack, "proof": "f" * 64}))
    with pytest.raises(refinement.RefinementRefused, match="parent_publication_identity"):
        pipeline.parent_publication(live, proof)
    assert destination.read_bytes() != previous
    assert pipeline.publication_path(live).exists()
    pointer.write_text(json.dumps(ack))
    pipeline.parent_publication(live, proof, reject=True)
    assert destination.read_bytes() == previous


def test_hook_source_mutation_is_rolled_back_before_parent_ack(tmp_path, monkeypatch):
    live, handoff = make_meeting(tmp_path)
    destination = run(live, handoff)
    previous = destination.read_bytes()
    proof = pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None, timeout=0)
    calls = []
    def hook():
        calls.append(1)
        live.write_text(live.read_text() + "\nХук изменил речь.\n")
    with pytest.raises(refinement.RefinementRefused, match="source_changed"):
        pipeline.run(live, CFG, handoff=None, final_sha=None, before_request=lambda: None,
                     timeout=0, before_commit=hook, graph_input_sha=proof, defer_finalization=True)
    assert calls == [1]
    assert destination.read_bytes() == previous
    assert not pipeline.publication_path(live).exists()


def test_parent_pointer_survives_original_path_disappearing(tmp_path, monkeypatch):
    live, destination, previous, proof = pending(tmp_path, monkeypatch)
    original = live.with_name("2026-01-01_120001_ПрежнееИмя.md")
    assert not original.exists()
    assert pipeline.parent_publication(original, proof) == live
    assert destination.read_bytes() != previous


@pytest.mark.parametrize("parent_ack", [False, True])
def test_graph_child_defers_only_for_explicit_parent_ack(tmp_path, monkeypatch, parent_ack):
    import sys
    import graph_updater as graph
    proof = "a" * 64
    args = ["graph_updater.py", str(tmp_path / "synthetic.md"), "--refine-before-archive", proof]
    if parent_ack:
        args.append("--debrief-parent-ack")
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(graph, "_report", lambda *a, **kw: None)
    seen = []
    monkeypatch.setattr(pipeline, "run", lambda *a, **kw: seen.append(kw))
    hook = lambda: None
    graph._finish_refinement(tmp_path / "synthetic.md", CFG, args[2:], before_commit=hook)
    assert len(seen) == 1
    assert seen[0]["before_commit"] is hook
    assert seen[0]["defer_finalization"] is parent_ack
    assert seen[0]["graph_input_sha"] == proof


def test_first_publication_retry_after_interrupted_ack_cleanup(tmp_path, monkeypatch):
    from pathlib import Path
    import live_sidecar

    live, handoff = make_meeting(tmp_path)
    proof = pipeline.prepare_for_graph(live, CFG, handoff=handoff,
                                       final_sha=live_sidecar.sha(live.read_text()), timeout=0)
    options = dict(handoff=None, final_sha=None, before_request=lambda: True,
                   timeout=0, graph_input_sha=proof, defer_finalization=True)
    destination = pipeline.run(live, CFG, **options)
    journal = pipeline.publication_path(live)
    unlink = Path.unlink

    def stop_at_journal(path, *args, **kwargs):
        if path == journal:
            raise OSError("interrupted cleanup")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", stop_at_journal)
        with pytest.raises(OSError, match="interrupted cleanup"):
            pipeline.parent_publication(live, proof, reject=True)
    assert journal.exists() and not pipeline._parent_ack_path(live, proof).exists()
    assert not destination.exists()
    assert pipeline.prepare_for_graph(live, CFG, handoff=None, final_sha=None, timeout=0) == proof
    assert pipeline.run(live, CFG, **options) == destination
    assert pipeline.parent_publication(live, proof) == live
