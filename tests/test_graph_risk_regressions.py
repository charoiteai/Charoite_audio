"""Synthetic checks for late graph scope and failed authoritative index publication."""
from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys

import pytest

from test_forget_night_processes import (
    REPO, STAMP, OTHER, THEME, _world, _seed_indexes, charoite_paths, CHAROITE,
)
from charoite_graph import dossier
import file_locks
import forget_meeting as forget
import nightly_dossier as night


@pytest.fixture(autouse=True)
def _no_brain_requests(monkeypatch):
    monkeypatch.setattr(forget, "brain_forget", lambda *args, **kwargs: "")


@pytest.mark.parametrize("second_graph", [False, True])
def test_late_meeting_in_known_graph_without_preview_actions_is_forgotten(tmp_path, monkeypatch, second_graph):
    root, graphs = _world(tmp_path, second_graph=second_graph)
    charoite_paths.use_data_root(root, replace=True)
    monkeypatch.setattr(forget, "_graph_roots", lambda explicit: [explicit] if explicit else list(graphs))
    graph = graphs[-1]
    node = graph / "Встречи" / f"{STAMP}.md"
    node.unlink(missing_ok=True)
    p = forget.plan(STAMP, root, graph=None)
    assert not any(forget._under(q, graph) for q in [*p.delete, *p.edit])
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=root))
    with file_locks.graph_lock(lock_dir, 0) as taken:
        assert taken
        node.write_text("# Synthetic late meeting\n", encoding="utf-8")
    assert forget.apply(p, yes=True) and not p.refused
    assert not node.exists(), "a known graph without preview actions retained the late meeting"
    assert not (root / "transcripts" / f"{STAMP}.md").exists()


@pytest.mark.parametrize("initial_graph", [False, True])
def test_new_graph_scope_is_refused_before_reading_its_files(tmp_path, monkeypatch, initial_graph):
    root, graphs = _world(tmp_path, second_graph=True)
    charoite_paths.use_data_root(root, replace=True)
    discovered = [graphs[0]] if initial_graph else []
    monkeypatch.setattr(forget, "_graph_roots", lambda explicit: list(discovered))
    p = forget.plan(STAMP, root)
    discovered.append(graphs[1])
    original = pathlib.Path.read_bytes

    def read(path):
        assert not path.is_relative_to(graphs[1]), "new graph was read without its lock"
        return original(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    assert not forget.apply(p, yes=True) and p.refused
    assert (root / "transcripts" / f"{STAMP}.md").exists()
    assert (graphs[0] / "Встречи" / f"{STAMP}.md").exists()


def test_busy_known_graph_without_preview_actions_refuses_before_deletion(tmp_path, monkeypatch):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    node = graph / "Встречи" / f"{STAMP}.md"
    node.unlink()
    p = forget.plan(STAMP, root, graph)
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=root))
    monkeypatch.setattr(forget, "LOCK_WAIT", 0)
    with file_locks.graph_lock(lock_dir, 0) as taken:
        assert taken
        node.write_text("# Synthetic late meeting\n", encoding="utf-8")
        assert not forget.apply(p, yes=True) and p.refused
    assert node.exists()
    assert (root / "transcripts" / f"{STAMP}.md").exists()


def test_keep_graph_does_not_acquire_a_graph_lock(tmp_path, monkeypatch):
    root, graphs = _world(tmp_path)
    p = forget.plan(STAMP, root, graphs[0], keep_graph=True)

    def lock(*args, **kwargs):
        raise AssertionError("keep_graph must not acquire a graph lock")

    monkeypatch.setattr(file_locks, "graph_lock", lock)
    assert forget.apply(p, yes=True)
    assert (graphs[0] / "Встречи" / f"{STAMP}.md").exists()
    assert not (root / "transcripts" / f"{STAMP}.md").exists()


def test_explicit_plan_keeps_action_based_lock_scope(tmp_path, monkeypatch):
    target = tmp_path / "synthetic-transcript.md"
    target.write_text("Synthetic transcript\n", encoding="utf-8")
    p = forget.Plan(stamp=STAMP, delete=[target], lock_dirs={tmp_path / "lock": tmp_path / "other-graph"})

    def lock(*args, **kwargs):
        raise AssertionError("an explicit plan must not acquire an unrelated graph lock")

    monkeypatch.setattr(file_locks, "graph_lock", lock)
    assert forget.apply(p, yes=True)
    assert not target.exists()


def _indexes(graph):
    folder = graph / CHAROITE.dossier_dir
    return {name: (folder / name).read_bytes() for name in (dossier.INDEX_JSON, dossier.INDEX_MD)}


@pytest.mark.parametrize("unreadable", ["source", "dossier"])
def test_authoritative_rebuild_refuses_unreadable_input_without_publishing(tmp_path, monkeypatch, unreadable):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    before = _indexes(graph)
    blocked = (graph / "Встречи" / f"{OTHER}.md" if unreadable == "source"
               else graph / CHAROITE.dossier_dir / f"{THEME}.md")
    original = pathlib.Path.read_bytes

    def read(path):
        if path == blocked:
            raise PermissionError("synthetic read failure")
        return original(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    with pytest.raises(OSError):
        dossier.rebuild_index(graph, schema=CHAROITE)
    assert _indexes(graph) == before


def _night_cli(root, graph, fault=""):
    config = root / "config" / "config.yaml"
    config.parent.mkdir(exist_ok=True)
    config.write_text('llm:\n  model: ""\n', encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve()), "night-cli", str(root), str(graph), fault],
        capture_output=True, text=True, cwd=REPO, env=dict(os.environ, CHAROITE_ROOT=str(root)), timeout=15)


@pytest.mark.parametrize("fault", ["json", "markdown", "source", "dossier"])
def test_recovery_cli_fails_on_publication_or_read_error_and_can_retry(tmp_path, fault):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    before = _indexes(graph)
    failed = _night_cli(root, graph, fault)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert "индекс не обновлён" in failed.stdout, failed.stdout
    if fault in {"source", "dossier", "json"}:
        assert _indexes(graph) == before
    repaired = _night_cli(root, graph)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert {e["тема"] for e in dossier.load_index(graph / CHAROITE.dossier_dir)} == {THEME}


def _night_worker(root, graph, fault):
    charoite_paths.use_data_root(root, replace=True)

    def denied(*args, **kwargs):
        raise AssertionError("network/model access is forbidden in this synthetic worker")

    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    night.generate = denied
    if fault in {"json", "markdown"}:
        original = pathlib.Path.replace
        name = dossier.INDEX_JSON if fault == "json" else dossier.INDEX_MD

        def replace(path, target):
            if pathlib.Path(target) == graph / CHAROITE.dossier_dir / name:
                raise OSError("synthetic index publication failure")
            return original(path, target)

        pathlib.Path.replace = replace
    elif fault in {"source", "dossier"}:
        original = pathlib.Path.read_bytes
        blocked = (graph / "Встречи" / f"{OTHER}.md" if fault == "source"
                   else graph / CHAROITE.dossier_dir / f"{THEME}.md")

        def read(path):
            if path == blocked:
                raise PermissionError("synthetic index input failure")
            return original(path)

        pathlib.Path.read_bytes = read
    sys.argv = ["nightly_dossier.py", "--graph", str(graph), "--limit", "0"]
    return night.main()


if __name__ == "__main__":
    raise SystemExit(_night_worker(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), sys.argv[4]))
