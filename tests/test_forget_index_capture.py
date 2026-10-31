"""Index publication and recovery use the graph selected by the locked plan."""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import sys

import pytest

import charoite_paths
from charoite_graph import dossier
from charoite_schema import CHAROITE

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import forget_meeting as forget  # noqa: E402

STAMP = "2030-01-01_1000"


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "data"
    for name in ["config", "transcripts", "recordings"]:
        (root / name).mkdir(parents=True)
    a, b = tmp_path / "GraphA", tmp_path / "GraphB"
    indexes = {}
    for graph in [a, b]:
        for sub in ["Встречи", "Ядра", CHAROITE.dossier_dir]:
            (graph / sub).mkdir(parents=True)
        for index in [dossier.INDEX_JSON, dossier.INDEX_MD]:
            path = graph / CHAROITE.dossier_dir / index
            path.write_bytes(("KEEP " + graph.name + " " + index).encode())
            indexes[path] = path.read_bytes()
    link = tmp_path / "selected"
    link.symlink_to(a, target_is_directory=True)
    configured = a.with_name(a.name.lower())
    if not configured.exists():
        configured = a  # Distinct case paths on Linux are not invented as aliases.
    (root / "config" / "config.yaml").write_text("sufler:\n  graph_dir: " + json.dumps(str(configured)) + "\n")
    (a / "Встречи" / f"{STAMP}.md").write_text("# Synthetic meeting\n")
    (root / "transcripts" / f"{STAMP}.md").write_text("Synthetic transcript\n")
    charoite_paths.use_data_root(root, replace=True)
    monkeypatch.setattr(forget, "brain_forget", lambda *a, **k: "")
    monkeypatch.setattr(forget, "LOCK_WAIT", 0)
    for name in forget.graphs.ENV_GRAPH_NAMES:
        monkeypatch.delenv(name, raising=False)
    return root, a, b, link, indexes


def _retarget_after_real_apply(monkeypatch, link, target):
    real = forget._apply_locked

    def apply_then_retarget(plan):
        result = real(plan)
        link.unlink()
        link.symlink_to(target, target_is_directory=True)
        return result

    monkeypatch.setattr(forget, "_apply_locked", apply_then_retarget)


def test_alias_retarget_after_real_apply_only_rebuilds_captured_indexes(world, monkeypatch):
    root, a, b, link, before = world
    p = forget.plan(STAMP, root, link)
    _retarget_after_real_apply(monkeypatch, link, b)
    assert forget.apply(p, yes=True)
    assert not (a / "Встречи" / f"{STAMP}.md").exists()
    assert link.samefile(b)
    assert all(path.read_bytes() != data for path, data in before.items() if path.is_relative_to(a))
    assert all(path.read_bytes() == data for path, data in before.items() if path.is_relative_to(b))


def test_all_captured_lock_siblings_get_one_real_index_rebuild(world, monkeypatch):
    root, a, _b, link, _before = world
    p = forget.plan(STAMP, root, link)
    captured = p.graph_groups[link]
    # On APFS the current lowercase config is a second proven legacy address.
    if a.with_name(a.name.lower()).exists():
        assert len(captured.lock_dirs) == 2
    calls = []
    real = dossier.rebuild_index

    def observed(graph, **kwargs):
        calls.append(graph)
        return real(graph, **kwargs)

    monkeypatch.setattr(dossier, "rebuild_index", observed)
    assert forget.apply(p, yes=True)
    assert calls == [captured.graph]


def test_actual_index_failure_recovery_names_the_captured_graph(world, monkeypatch, capsys):
    root, a, b, link, before = world
    p = forget.plan(STAMP, root, link)
    captured = p.graph_groups[link]
    occupied = a / CHAROITE.dossier_dir / f"{dossier.INDEX_JSON}.{os.getpid()}.tmp"
    occupied.write_bytes(b"Synthetic occupied path must be preserved")
    _retarget_after_real_apply(monkeypatch, link, b)
    # The existing API reports work already applied; refused makes the CLI fail.
    assert forget.apply(p, yes=True) and p.refused
    output = capsys.readouterr().out
    recovery = next(line for line in output.splitlines() if line.startswith("обновить индексы:"))
    args = shlex.split(recovery)
    assert args[args.index("--graph") + 1] == str(captured.graph)
    assert str(captured.graph / CHAROITE.dossier_dir) in p.check[-1]
    assert occupied.read_bytes() == b"Synthetic occupied path must be preserved"
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not (a / "Встречи" / f"{STAMP}.md").exists()


def test_preexisting_manual_plan_without_capture_keeps_its_target(world, monkeypatch):
    root, a, _b, _link, _before = world
    target = a / "manual.md"
    target.write_text("Synthetic manual operation\n")
    first, second = root / "manual-first-lock", root / "manual-second-lock"
    p = forget.Plan(STAMP, delete=[target], lock_dirs={first: a, second: a}, data_root=root)
    assert not p.graph_groups
    calls = []
    real = dossier.rebuild_index

    def observed(graph, **kwargs):
        calls.append(graph)
        return real(graph, **kwargs)

    monkeypatch.setattr(dossier, "rebuild_index", observed)
    assert forget.apply(p, yes=True)
    assert not target.exists() and calls == [a]
