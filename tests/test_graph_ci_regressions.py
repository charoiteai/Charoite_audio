"""Observable graph/backup/no-op contracts selected after exact CI survivors."""
from __future__ import annotations

import pathlib
import os
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


@pytest.mark.parametrize("intermediate", [False, True])
def test_rebuild_refuses_dossier_folder_outside_graph_before_reading_or_publishing(tmp_path, intermediate):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    folder = graph / CHAROITE.dossier_dir
    outside = tmp_path / "outside"
    outside.mkdir()
    relocated = outside / "dossiers"
    folder.rename(relocated)
    if intermediate:
        bridge = graph / "bridge"
        bridge.symlink_to(outside, target_is_directory=True)
        folder.symlink_to(bridge / "dossiers", target_is_directory=True)
    else:
        folder.symlink_to(relocated, target_is_directory=True)
    before = {path.name: path.read_bytes() for path in relocated.iterdir()}
    reads = []

    def read(path):
        reads.append(path)
        return dossier.load_dossier(path)

    refused = False
    try:
        dossier.rebuild_index(graph, schema=CHAROITE, read=read)
    except OSError:
        refused = True
    assert {path.name: path.read_bytes() for path in relocated.iterdir()} == before
    assert refused and reads == []


@pytest.mark.parametrize("index_name", [dossier.INDEX_JSON, dossier.INDEX_MD])
def test_rebuild_refuses_occupied_temporary_index_without_overwriting_external_target(tmp_path, index_name):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    outside = tmp_path / "outside-index"
    original = b"Keep synthetic external bytes\n"
    outside.write_bytes(original)
    temp = graph / CHAROITE.dossier_dir / f"{index_name}.{os.getpid()}.tmp"
    temp.symlink_to(outside)
    refused = False
    try:
        dossier.rebuild_index(graph, schema=CHAROITE)
    except OSError:
        refused = True
    assert outside.read_bytes() == original
    assert refused and temp.is_symlink()


@pytest.mark.parametrize("index_name", [dossier.INDEX_JSON, dossier.INDEX_MD])
@pytest.mark.parametrize("current_pid", [False, True])
def test_index_publication_preserves_existing_fresh_temporary_file(tmp_path, index_name, current_pid):
    folder = tmp_path / "standalone-dossiers"
    folder.mkdir()
    pid = os.getpid() if current_pid else os.getpid() + 1
    temp = folder / f"{index_name}.{pid}.tmp"
    original = b"Keep synthetic temporary bytes\n"
    temp.write_bytes(original)
    refused = False
    try:
        dossier.write_index(folder, [])
    except FileExistsError:
        refused = True
    assert temp.read_bytes() == original
    assert refused == current_pid


@pytest.mark.parametrize("alias", [False, True])
def test_rebuild_publishes_healthy_graph_through_its_own_root_or_alias(tmp_path, alias):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    dossier.write_index(graph / CHAROITE.dossier_dir, [])
    entrypoint = graph
    if alias:
        entrypoint = tmp_path / "graph-alias"
        entrypoint.symlink_to(graph, target_is_directory=True)
    dossier.rebuild_index(entrypoint, schema=CHAROITE)
    folder = graph / CHAROITE.dossier_dir
    assert {entry["тема"] for entry in dossier.load_index(folder)} == {THEME}
    assert THEME in (folder / dossier.INDEX_MD).read_text(encoding="utf-8")


@pytest.mark.parametrize("index_name", [dossier.INDEX_JSON, dossier.INDEX_MD])
def test_rebuild_replaces_final_index_link_and_preserves_its_external_target(tmp_path, index_name):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    outside = tmp_path / "outside-index"
    original = b"Keep synthetic external bytes\n"
    outside.write_bytes(original)
    index = graph / CHAROITE.dossier_dir / index_name
    index.unlink()
    index.symlink_to(outside)
    dossier.rebuild_index(graph, schema=CHAROITE)
    assert outside.read_bytes() == original and not index.is_symlink()
    assert {entry["тема"] for entry in dossier.load_index(index.parent)} == {THEME}
    assert THEME in (index.parent / dossier.INDEX_MD).read_text(encoding="utf-8")


@pytest.mark.parametrize("refresh", [False, True])
def test_standalone_index_api_keeps_the_supplied_folder_as_its_boundary(tmp_path, refresh):
    folder = tmp_path / "standalone-dossiers"
    folder.mkdir()
    if refresh:
        dossier.refresh_index(folder, [], strict=True)
    else:
        dossier.write_index(folder, [])
    assert dossier.load_index(folder) == []
    assert "досье: 0" in (folder / dossier.INDEX_MD).read_text(encoding="utf-8")
