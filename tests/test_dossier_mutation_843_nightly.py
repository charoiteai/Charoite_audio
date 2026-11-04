"""Invented publication races, retry accounting and metadata ownership."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import forget_meeting as forget
import nightly_dossier as night
import dossier_incremental as inc
from charoite_graph import dossier, frontmatter
from charoite_schema import CHAROITE
from test_dossier_incremental_mutation_behaviors import (
    A, B, FACT, LOCAL, THEME, _answer, _source, _transport, _world,
    isolated as isolated,
)


def _candidate(graph):
    group, queue = night._scan(graph, False, "2099-01-03", "synthetic-mutation-843")
    assert len(queue) == 1
    candidate = queue[0]
    decision = dossier.plan(candidate.loaded, candidate.members, group.snapshot,
                            schema=CHAROITE, on_disk=False, force=False)
    return group, candidate, decision


def test_locked_input_read_failure_is_propagated_instead_of_becoming_an_incomplete_inventory(isolated, monkeypatch):
    graph, path = _world(isolated)
    source = _source(graph)
    group, candidate, decision = _candidate(graph)
    before, read_bytes = path.read_bytes(), Path.read_bytes

    def unreadable(self):
        if self == source:
            raise PermissionError("Synthetic source became unreadable under the graph lock.")
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with night._graph_snapshot(graph, 0) as captured:
        assert captured is not None
        with pytest.raises(PermissionError, match="unreadable under the graph lock"):
            night._input_refusal(group, THEME, candidate.members, candidate.loaded, decision, captured)
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()


def test_source_retarget_between_the_last_scan_and_path_comparison_is_a_named_refusal(isolated, monkeypatch):
    graph, path = _world(isolated)
    _source(graph)
    source = graph / f"Встречи/{A}.md"
    private_fixture = graph / ".synthetic-fixture"
    private_fixture.mkdir()
    first, second = private_fixture / "first.md", private_fixture / "second.md"
    source.rename(first)
    second.write_text("A separate invented source.\n")
    source.symlink_to(first)
    group, candidate, decision = _candidate(graph)
    before, resolve, seen = path.read_bytes(), Path.resolve, []

    def retarget(self, strict=False):
        physical = resolve(self, strict=strict)
        if self == source:
            seen.append(physical)
            # The first two calls establish the fresh scan's identity. The
            # next resolution is the comparison immediately before publication.
            if len(seen) == 3:
                self.unlink()
                self.symlink_to(second)
        return physical

    monkeypatch.setattr(Path, "resolve", retarget)
    with night._graph_snapshot(graph, 0) as captured:
        assert captured is not None
        reason = night._input_refusal(group, THEME, candidate.members, candidate.loaded, decision, captured)
    assert reason == "изменился путь источника"
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()


def test_initial_planning_uses_its_captured_inventory_until_the_locked_recheck(isolated, monkeypatch):
    graph, path = _world(isolated)
    _source(graph)
    path.write_text(dossier.with_revision(path.read_text(), "2099-01-02"))
    original, before = dossier.scan_snapshot, path.read_bytes()
    removed = []

    def scan(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        if not removed:
            source = graph / f"Встречи/{A}.md"
            source.unlink()
            removed.append(source)
        return snapshot

    monkeypatch.setattr(dossier, "scan_snapshot", scan)
    group, queue = night._scan(graph, False, "2099-01-03", "synthetic-mutation-843")
    assert len(queue) == 1 and queue[0].theme == THEME
    assert group.revised == 0 and path.read_bytes() == before


def test_postscan_removal_is_a_retryable_publish_race_and_does_not_become_a_revision_hold(isolated, monkeypatch):
    graph, path = _world(isolated)
    _source(graph)
    path.write_text(dossier.with_revision(path.read_text(), "2099-01-02"))
    group, candidate, _ = _candidate(graph)
    before, judged = path.read_bytes(), []
    (graph / f"Встречи/{A}.md").unlink()
    monkeypatch.setattr(inc, "source_relevance", lambda theme, loaded, name, *a: judged.append(name) or False)
    monkeypatch.setattr(night, "generate", lambda *a, **k: pytest.fail("An append race reached a full rebuild."))
    elapsed = night._build(candidate, LOCAL, lock_wait=lambda: 0)
    assert elapsed is not None and judged == [B]
    assert group.changed == 1 and group.built == 0 and group.revised == 0
    assert path.read_bytes() == before and not (path.parent / ".backup").exists()


def test_a_failed_source_judgment_still_teaches_the_shared_queue_its_actual_cost(isolated, monkeypatch):
    first, first_path = _world(isolated, "first")
    second, second_path = _world(isolated, "second")
    _source(first)
    _source(second)
    before, ticks, calls = first_path.read_bytes(), {"now": 1000.0}, []
    monkeypatch.setattr(night, "clock", lambda: ticks["now"])

    def complete(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append(system)
        if len(calls) == 1:
            ticks["now"] += 20
            raise TimeoutError("Synthetic failed judgment consumed twenty seconds.")
        return _answer(payload, system)

    monkeypatch.setattr(inc, "local_complete", complete)
    result = night.run_all([first, second], LOCAL, full=False, dry=False, budget_s=55)
    assert result.attempts == 2 and [group.built for group in result.graphs] == [0, 1]
    assert first_path.read_bytes() == before and FACT in second_path.read_text()


def test_a_model_attempt_remains_counted_when_the_source_changes_after_backup(isolated, monkeypatch):
    graph, path = _world(isolated)
    source = _source(graph)
    before, backup = path.read_bytes(), dossier.backup
    _transport(monkeypatch)

    def changed_after_backup(*args, **kwargs):
        saved = backup(*args, **kwargs)
        source.write_text(source.read_text() + "A concurrent invented amendment.\n")
        return saved

    monkeypatch.setattr(dossier, "backup", changed_after_backup)
    result = night.run_all([graph], LOCAL, full=False, dry=False)
    assert result.attempts == 1 and result.graphs[0].changed == 1 and result.graphs[0].built == 0
    assert path.read_bytes() == before
    assert next(path.parent.glob(".backup/*/*.md")).read_bytes() == before


def test_the_final_append_gate_refuses_unsupported_newlines_even_with_a_complete_structure(isolated):
    graph, path = _world(isolated)
    _source(graph)
    group, _, _ = _candidate(graph)
    text = path.read_text() + "\u2028"
    loaded = dossier.DossierLoad("ok", text, text.encode())
    assert loaded.parsed.complete and loaded.newline is None
    with pytest.raises(inc.Refused, match="invalid rendered append"):
        night._append_result(loaded, None, [], [], group, "synthetic-fingerprint")
    assert "\u2028" not in path.read_text()


def test_the_final_append_refusal_keeps_the_specific_structural_diagnosis(isolated):
    graph, path = _world(isolated)
    _source(graph)
    group, _, _ = _candidate(graph)
    text = path.read_text().replace("## Решено\n", "## Решено extra\n")
    loaded = dossier.DossierLoad("ok", text, text.encode())
    with pytest.raises(inc.Refused, match="неполный или нарушенный порядок пяти разделов тела"):
        night._append_result(loaded, None, [], [], group, "synthetic-fingerprint")
    assert "## Решено extra" not in path.read_text()


def test_forget_never_reinterprets_a_document_authors_composition_field_as_dossier_membership(isolated):
    graph, _ = _world(isolated)
    root_source = isolated / "transcripts" / f"{A}.md"
    root_source.parent.mkdir(parents=True, exist_ok=True)
    root_source.write_text(f"# Invented meeting {A}\n")
    note = graph / "Документы/Inventory.md"
    text = frontmatter.set_list_field("---\nkind: synthetic-document\n---\nKeep this authored inventory.\n",
                                     dossier.COMPOSITION_FIELD, [A, "A separate authored item"])
    note.write_text(text)
    planned = forget.plan(A, isolated, graph)
    assert note not in planned.edit and note not in planned.delete
    assert note.read_text() == text and root_source.exists()


def test_captured_context_capped_output_cannot_publish_or_advance_membership(isolated, monkeypatch):
    """Replay a real server's incomplete output; this test makes zero model calls."""
    fixture = Path(__file__).parent / "fixtures/dossier_context_capped_output.json"
    raw = json.loads(fixture.read_text())["output"]
    with pytest.raises(inc.Refused, match="malformed model JSON"):
        inc._json(raw, {"body", "changes"})
    graph, path = _world(isolated)
    assert night.run_all([graph], LOCAL, full=False, dry=False).attempts == 0
    _source(graph)
    protected = [path, path.parent / dossier.INDEX_JSON, path.parent / dossier.INDEX_MD]
    before = {item: item.read_bytes() for item in protected}
    calls = []

    def captured_output(prompt, config, *, system, **kwargs):
        payload, _ = json.JSONDecoder().raw_decode(prompt)
        calls.append(system)
        return raw if "extend factual" in system else _answer(payload, system)

    monkeypatch.setattr(inc, "local_complete", captured_output)
    result = night.run_all([graph], LOCAL, full=False, dry=False)
    assert result.attempts == 1 and result.graphs[0].built == 0
    assert sum("extend factual" in role for role in calls) == 2
    assert {item: item.read_bytes() for item in protected} == before
    assert not (path.parent / ".backup").exists()
