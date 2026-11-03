"""Real source races and exact rebuild-input bounds on invented files."""
from __future__ import annotations

from pathlib import Path

import pytest

from charoite_graph import dossier
from charoite_schema import CHAROITE


@pytest.mark.parametrize("resolution", [1, 2])
def test_a_source_vanishing_during_either_identity_resolution_aborts_the_snapshot(tmp_path, monkeypatch, resolution):
    graph = tmp_path / "graph"
    source = graph / "Встречи/SyntheticSource.md"
    source.parent.mkdir(parents=True)
    original = b"# Invented source\nA synthetic cover was fitted.\n"
    source.write_bytes(original)
    parked = source.with_suffix(".parked")
    resolve, calls = Path.resolve, []

    def transient(self, strict=False):
        if self == source:
            calls.append(self)
            if len(calls) == resolution:
                self.rename(parked)
                try:
                    return resolve(self, strict=strict)
                finally:
                    parked.rename(self)
        return resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", transient)
    with pytest.raises(OSError):
        dossier.scan_snapshot(graph, schema=CHAROITE, strict=True)
    assert len(calls) == resolution and source.read_bytes() == original and not parked.exists()


def test_changed_source_bytes_cannot_be_admitted_just_because_its_path_is_stable(tmp_path, monkeypatch):
    graph = tmp_path / "graph"
    source = graph / "Встречи/SyntheticSource.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# Invented source\nBefore.\n")
    read_bytes = Path.read_bytes

    def racing_read(self):
        raw = read_bytes(self)
        if self == source:
            self.write_bytes(raw + b"After the captured read.\n")
        return raw

    monkeypatch.setattr(Path, "read_bytes", racing_read)
    with pytest.raises(OSError, match="source changed during scan"):
        dossier.scan_snapshot(graph, schema=CHAROITE, strict=True)
    assert read_bytes(source).endswith(b"After the captured read.\n")


def test_default_source_preview_is_bounded_while_full_rebuild_input_keeps_the_tail():
    text = "---\nkind: synthetic\n---\n" + "invented note " * dossier.SRC_CHARS + "COMPLETE_SYNTHETIC_TAIL"
    metadata = {"text": text}
    bounded = dossier.source_text(metadata)
    assert len(bounded) == dossier.SRC_CHARS and "COMPLETE_SYNTHETIC_TAIL" not in bounded
    assert not bounded.startswith("---")
    assert dossier.source_text(metadata, full=True).endswith("COMPLETE_SYNTHETIC_TAIL")
    assert metadata["text"] == text


def test_a_complete_source_block_exactly_filling_the_rebuild_budget_is_selected(monkeypatch):
    files = {"First": {"kind": "Встречи", "text": "A synthetic cover was fitted."},
             "Second": {"kind": "Встречи", "text": "A synthetic drain was inspected."}}
    first_block = "### [[First]] (Встречи)\nA synthetic cover was fitted.\n"
    monkeypatch.setattr(dossier, "PROMPT_CHARS", len(first_block))
    assert dossier.prompt_members(["First", "Second"], files) == ["First"]
