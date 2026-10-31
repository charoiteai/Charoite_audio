"""Observable graph/backup/no-op contracts selected after exact CI survivors."""
from __future__ import annotations

import pathlib
import socket
import sys

import pytest

from test_forget_night_processes import STAMP, OTHER, THEME, _world, _seed_indexes, _path
from test_forget_index_recovery import _neighbor, NEIGHBOR
from test_plan_revalidation import _pair, _refused_without_effects
from charoite_graph import dossier, safe_write
from charoite_schema import CHAROITE
import charoite_paths
import file_locks
import forget_meeting as forget
import nightly_dossier as night


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    calls = []
    monkeypatch.setattr(forget, "brain_forget", lambda *a, **k: calls.append((a, k)) or "")

    def denied(*args, **kwargs):
        raise AssertionError("network/model access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(night, "generate", denied)
    return calls


def test_late_dossier_is_copied_before_successful_forget_deletes_it(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    p = forget.plan(STAMP, root, graph)
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=root))
    with file_locks.graph_lock(lock_dir, 0) as taken:
        assert taken
        _seed_indexes(graph)
        original = _path(graph).read_bytes()
    assert forget.apply(p, yes=True) and not p.refused
    assert not _path(graph).exists()
    backup = graph / forget.BACKUP_DIR / STAMP / CHAROITE.dossier_dir / f"{THEME}.md"
    assert backup.read_bytes() == original
    assert dossier.load_index(graph / CHAROITE.dossier_dir) == []


def test_empty_manual_plan_is_noop_even_with_busy_scope_metadata(tmp_path, monkeypatch, isolated):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=root))
    p = forget.Plan(stamp=STAMP, graph_scope=(graph,), lock_dirs={lock_dir: graph})
    monkeypatch.setattr(forget, "LOCK_WAIT", 0)
    with file_locks.graph_lock(lock_dir, 0) as taken:
        assert taken
        assert not forget.apply(p, yes=True)
    assert not p.refused
    assert (root / "transcripts" / f"{STAMP}.md").exists()
    assert (graph / "Встречи" / f"{STAMP}.md").exists()
    assert not (graph / forget.BACKUP_DIR).exists() and isolated == []


@pytest.mark.parametrize("malformed", ["deletion-mapping", "scope-list", "declared-refusal"])
def test_unusable_refresh_does_not_turn_into_a_successful_deletion(tmp_path, isolated, malformed):
    root, graph, note, p, fresh = _pair(tmp_path)
    if malformed == "deletion-mapping":
        fresh.delete = {path: "unsupported operation entry" for path in fresh.delete}
    elif malformed == "scope-list":
        fresh.graph_scope = list(fresh.graph_scope)
    else:
        fresh.refused = True
    p.rescan = lambda: fresh
    _refused_without_effects(p, tmp_path, graph, isolated)
    assert (root / "transcripts" / f"{STAMP}.md").exists()


@pytest.mark.parametrize("same_path", [False, True])
def test_conflicting_new_edit_and_delete_preserve_all_original_files(tmp_path, isolated, same_path):
    root, graph, note, p, fresh = _pair(tmp_path)
    folder = graph / "New synthetic folder"
    folder.mkdir()
    child = folder / "Keep.md"
    child.write_text("# Preserve synthetic source\n", encoding="utf-8")
    fresh.delete.append(child if same_path else folder)
    fresh.edit[child] = "# Replacement must not be written\n"
    fresh.snaps[child] = safe_write.stat_snapshot(child)
    p.rescan = lambda: fresh
    _refused_without_effects(p, tmp_path, graph, isolated)
    assert child.read_text(encoding="utf-8") == "# Preserve synthetic source\n"


def test_permissive_snapshot_can_publish_healthy_cluster_despite_unreadable_source(tmp_path, monkeypatch):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    dossier.write_index(graph / CHAROITE.dossier_dir, [])
    unreadable = graph / "Встречи" / f"{OTHER}.md"
    original = pathlib.Path.read_bytes

    def read(path):
        if path == unreadable:
            raise PermissionError("synthetic source is temporarily unreadable")
        return original(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    snapshot = dossier.scan_snapshot(graph, schema=CHAROITE)
    current = dossier.clusters(snapshot.files, snapshot.backlinks, schema=CHAROITE)
    dossier.refresh_index(graph / CHAROITE.dossier_dir, current, strict=True)
    assert unreadable.stem not in snapshot.files and THEME in current
    assert {entry["тема"] for entry in dossier.load_index(graph / CHAROITE.dossier_dir)} == {THEME}


def test_permissive_refresh_keeps_new_healthy_neighbor_searchable(tmp_path, monkeypatch):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    _neighbor(graph)
    unreadable = _path(graph)
    original = pathlib.Path.read_bytes

    def read(path):
        if path == unreadable:
            raise PermissionError("synthetic dossier is temporarily unreadable")
        return original(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    dossier.refresh_index(graph / CHAROITE.dossier_dir, [THEME, NEIGHBOR])
    entries = dossier.load_index(graph / CHAROITE.dossier_dir)
    assert NEIGHBOR in {entry["тема"] for entry in entries}
    assert NEIGHBOR in (graph / CHAROITE.dossier_dir / dossier.INDEX_MD).read_text(encoding="utf-8")


def test_all_graphs_continues_past_missing_graph_even_with_explicit_graph_option(tmp_path, monkeypatch):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    _seed_indexes(graph)
    dossier.write_index(graph / CHAROITE.dossier_dir, [])
    monkeypatch.setattr(night, "cfg", lambda: {})
    monkeypatch.setattr(night, "all_graphs", lambda: [tmp_path / "missing-graph", graph])
    monkeypatch.setattr(night.signal, "signal", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["nightly_dossier.py", "--graph", str(tmp_path / "ignored-option"),
                                     "--all-graphs", "--limit", "0"])
    assert night.main() == 0
    assert {entry["тема"] for entry in dossier.load_index(graph / CHAROITE.dossier_dir)} == {THEME}
