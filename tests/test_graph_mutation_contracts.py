"""Descriptor identity and captured-scope refusal contracts, synthetic files only."""
from __future__ import annotations

import dataclasses
import os
import pathlib
import sys
import types

import pytest

import charoite_paths as paths
import graphs

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import cloud_review as cloud  # noqa: E402
import forget_meeting as forget  # noqa: E402


STAMP = "2030-01-01_1000"


def _graph(tmp_path, name):
    graph = tmp_path / name
    (graph / "Встречи").mkdir(parents=True)
    note = graph / "Встречи" / f"{STAMP}.md"
    note.write_text(f"# Synthetic {name}\n", encoding="utf-8")
    return graph, note


def test_native_descriptor_identifies_the_actual_directory(tmp_path):
    graph, _ = _graph(tmp_path, "Physical")
    alias = tmp_path / "Selected"
    alias.symlink_to(graph, target_is_directory=True)
    representative, identity = paths.physical_graph(alias)
    assert representative == graph.resolve()
    assert representative.samefile(alias)
    seen = graph.stat()
    assert identity == (seen.st_dev, seen.st_ino)


def test_darwin_kernel_contract_uses_real_fd_and_nul_terminated_path(tmp_path, monkeypatch):
    graph, _ = _graph(tmp_path, "Physical")
    expected, identity = paths.physical_graph(graph)
    opened = []

    def kernel(fd, opcode, request):
        # On Linux this is an explicit Darwin kernel adapter, not native F_GETPATH.
        seen = os.fstat(fd)
        assert (seen.st_dev, seen.st_ino) == identity
        assert opcode == 50
        assert request == b"\0" * 1024
        opened.append(fd)
        raw = os.fsencode(expected)
        assert len(raw) < len(request)
        return raw + b"\0" * (len(request) - len(raw))

    # No F_GETPATH attribute: exercise the documented native fallback opcode too.
    monkeypatch.setattr(paths, "sys", types.SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(paths, "fcntl", types.SimpleNamespace(fcntl=kernel))
    assert paths.physical_graph(graph) == (expected, identity)
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_missing_graph_plan_refuses_apply_without_losing_other_files(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "transcripts").mkdir(parents=True)
    transcript = root / "transcripts" / f"{STAMP}.md"
    transcript.write_text("# Synthetic transcript\n", encoding="utf-8")
    original = transcript.read_bytes()
    missing = tmp_path / "Missing"
    monkeypatch.setenv("CHAROITE_ROOT", str(root))
    p = forget.plan(STAMP, root, missing)
    assert missing in p.graph_errors and transcript in p.delete
    assert not forget.apply(p, yes=True)
    assert p.refused
    assert transcript.read_bytes() == original
    assert not missing.exists()


def test_forget_reads_captured_physical_scope_after_selected_alias_moves(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    a, a_note = _graph(tmp_path, "A")
    b, b_note = _graph(tmp_path, "B")
    alias = tmp_path / "Selected"
    alias.symlink_to(a, target_is_directory=True)
    monkeypatch.setenv("CHAROITE_ROOT", str(root))
    real = graphs.mutation_addresses
    captures = []

    def capture_then_move(graph, **kwargs):
        group = real(graph, **kwargs)
        captures.append(group)
        alias.unlink()
        alias.symlink_to(b, target_is_directory=True)
        return group

    monkeypatch.setattr(graphs, "mutation_addresses", capture_then_move)
    p = forget.plan(STAMP, root, alias)
    assert len(captures) == 1 and alias.samefile(b)
    assert captures[0].graph == a.resolve()
    assert a_note.resolve() in p.delete
    assert b_note.resolve() not in p.delete
    assert all(not path.is_relative_to(alias) for path in [*p.delete, *p.edit])
    # The stale selected alias also refuses effects, preserving both real graphs.
    assert not forget.apply(p, yes=True) and p.refused
    assert a_note.exists() and b_note.exists()


@pytest.mark.parametrize("may_edit,deliver", [(True, False), (False, True), (True, True)])
def test_each_cloud_mutation_role_requires_capture_before_cloud_policy(tmp_path, monkeypatch,
                                                                    may_edit, deliver):
    calls = []

    def denied(cfg):
        calls.append(cfg)
        raise AssertionError("uncaptured mutation reached the cloud boundary")

    monkeypatch.setattr(cloud.privacy, "cloud_enrich_enabled", denied)
    with pytest.raises(ValueError, match="captured snapshot"):
        cloud._run_locked(STAMP, tmp_path / "transcript", tmp_path / "graph",
                          tmp_path / "revision", tmp_path / "log", {},
                          may_edit=may_edit, graph_available=False, deliver=deliver,
                          after_unlock=[])
    assert calls == []


def test_pure_text_cloud_mode_still_reaches_its_policy_without_capture(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cloud.privacy, "cloud_enrich_enabled", lambda cfg: calls.append(cfg) or False)
    assert cloud._run_locked(STAMP, tmp_path / "transcript", tmp_path / "graph",
                             tmp_path / "revision", tmp_path / "log", {},
                             may_edit=False, graph_available=False, deliver=False,
                             after_unlock=[]) == 1
    assert calls == [{}]


@pytest.mark.parametrize("field", ["graph", "primary", "lock_dirs"])
def test_captured_authority_cannot_be_repointed_after_proof(tmp_path, field):
    graph, _ = _graph(tmp_path, "A")
    other, _ = _graph(tmp_path, "B")
    group = paths.graph_addresses(graph, root=tmp_path / "data")
    before = dataclasses.astuple(group)
    replacement = (other,) if field == "lock_dirs" else other
    with pytest.raises(AttributeError):
        setattr(group, field, replacement)
    assert dataclasses.astuple(group) == before
    assert group.backup_root("cloud_backup").parent == group.primary


def test_mirrors_accept_every_registered_kind_and_reject_unknown_kind(tmp_path):
    graph, _ = _graph(tmp_path, "A")
    group = paths.graph_addresses(graph, root=tmp_path / "data")
    for kind in paths.GRAPH_BACKUP_KINDS:
        assert group.mirrors(kind) == tuple(base / kind for base in group.lock_dirs)
    with pytest.raises(ValueError, match="unknown graph backup kind"):
        group.mirrors("not_registered")
