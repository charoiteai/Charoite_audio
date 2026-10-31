"""Whole-plan compatibility: temporary files, real apply, no model or network."""
from __future__ import annotations

import copy
import dataclasses
import json
import socket
import sys

import pytest

from test_forget_night_processes import STAMP, OTHER, _world, _seed_indexes
from test_graph_risk_regressions import _night_cli
from charoite_graph import safe_write
import charoite_paths
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


def _pair(tmp_path):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    _seed_indexes(graph)
    note = graph / "Ядра" / "Mutable note.md"
    note.write_text(f"# Keep\n- [[Встречи/{STAMP}]] synthetic fact\n", encoding="utf-8")
    p = forget.plan(STAMP, root, graph)
    return root, graph, note, p, copy.deepcopy(p)


def _intent(p):
    return copy.deepcopy({f.name: getattr(p, f.name) for f in dataclasses.fields(p) if f.name != "refused"})


def _refused_without_effects(p, tmp_path, graph, calls):
    before = _intent(p)
    files = {path: path.read_bytes() for path in tmp_path.rglob("*")
             if path.is_file() and path.name != "cloud.lock"}
    backup = graph / forget.BACKUP_DIR
    had_backup = backup.exists() or backup.is_symlink()
    assert not forget.apply(p, yes=True) and p.refused
    assert _intent(p) == before, "an incompatible refresh partially changed the confirmed Plan"
    assert {path: path.read_bytes() for path in tmp_path.rglob("*")
            if path.is_file() and path.name != "cloud.lock"} == files
    assert (backup.exists() or backup.is_symlink()) is had_backup
    assert calls == []


@pytest.mark.parametrize("changed", [
    "outside-delete", "missing-edit", "changed-edit", "missing-copy", "brain-keys", "brain-sent",
    "target", "root", "scope", "lock-map", "new-external", "partial-merge", "invalid-object", "invalid-fields",
    "invalid-text", "invalid-path", "invalid-diagnostics",
])
def test_incompatible_refresh_refuses_the_whole_plan_before_any_effect(tmp_path, isolated, changed):
    root, graph, note, p, fresh = _pair(tmp_path)
    if changed == "outside-delete":
        fresh.delete.remove(root / "transcripts" / f"{STAMP}.md")
    elif changed == "missing-edit":
        del fresh.edit[note]
    elif changed == "changed-edit":
        fresh.edit[note] += "different replacement\n"
    elif changed == "missing-copy":
        fresh.copy_first.clear()
    elif changed == "brain-keys":
        fresh.brain_keys.append(STAMP + "01")
    elif changed == "brain-sent":
        fresh.brain_sent.add(STAMP)
    elif changed == "target":
        fresh.stamp = OTHER
    elif changed == "root":
        fresh.data_root = tmp_path / "different-data"
    elif changed == "scope":
        fresh.graph_scope = ()
    elif changed == "lock-map":
        fresh.lock_dirs = {d: graph / "different-graph" for d in p.lock_dirs}
    elif changed in {"new-external", "partial-merge"}:
        outside = root / "recordings" / "Late synthetic.wav"
        outside.write_bytes(b"synthetic audio")
        fresh.delete.append(outside)
        if changed == "partial-merge":
            added = graph / "Встречи" / "Late synthetic.md"
            added.write_text("# Late synthetic note\n", encoding="utf-8")
            fresh.delete.insert(0, added)
    elif changed == "invalid-object":
        fresh = object()
    elif changed == "invalid-fields":
        fresh.edit = None
    elif changed == "invalid-text":
        fresh.edit[graph / "New.md"] = 42
    elif changed == "invalid-path":
        fresh.delete.append("not a Path")
    elif changed == "invalid-diagnostics":
        fresh.notes.append(42)
    p.rescan = lambda: fresh
    _refused_without_effects(p, tmp_path, graph, isolated)
    assert (root / "transcripts" / f"{STAMP}.md").exists()


def test_live_sidecar_owner_change_preserves_outside_transcript_and_stale_edit(tmp_path, isolated):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    target, neighbor = STAMP + "01", STAMP + "02"
    (root / "transcripts" / f"{STAMP}.md").unlink()
    (graph / "Встречи" / f"{STAMP}.md").unlink()
    minute = root / "transcripts" / f"{STAMP}_Synthetic.md"
    minute.write_text("# Встреча synthetic\n", encoding="utf-8")
    sidecar = minute.with_name(minute.name + ".live.json")
    sidecar.write_text(json.dumps({"stamp": target}), encoding="utf-8")
    (root / "transcripts" / f"{target}.md").write_text("# Synthetic target\n", encoding="utf-8")
    note = graph / "Ядра" / "Ownership note.md"
    note.write_text(f"# Keep\n- [[Встречи/{STAMP}]] synthetic fact\n", encoding="utf-8")
    p = forget.plan(target, root, graph)
    assert minute in p.delete and note in p.edit
    sidecar.write_text(json.dumps({"stamp": neighbor}), encoding="utf-8")
    fresh = forget.plan(target, root, graph)
    assert minute not in fresh.delete and note not in fresh.edit
    _refused_without_effects(p, tmp_path, graph, isolated)


@pytest.mark.parametrize("changed", [False, True])
def test_empty_keep_graph_refreshes_without_locks_before_noop(tmp_path, monkeypatch, isolated, changed):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    transcript = root / "transcripts" / f"{STAMP}.md"
    transcript.unlink()
    p = forget.plan(STAMP, root, graph, keep_graph=True)
    assert not p.delete and not p.edit and p.graph_scope is None
    refresh, calls = p.rescan, []

    def rescan():
        calls.append(1)
        return refresh()

    def lock(*args, **kwargs):
        raise AssertionError("keep_graph must not acquire graph locks")

    p.rescan = rescan
    monkeypatch.setattr(forget.file_locks, "graph_lock", lock)
    if changed:
        transcript.write_text("# Late outside transcript\n", encoding="utf-8")
    assert not forget.apply(p, yes=True)
    assert calls == [1]
    assert p.refused is changed
    assert transcript.exists() is changed
    assert isolated == []


def test_new_recursive_delete_through_intermediate_symlink_refuses(tmp_path, isolated):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    p = forget.plan(STAMP, root, graph)
    outside = tmp_path / "outside-archive"
    meeting = outside / "2030-01-01 10-00 — Synthetic"
    meeting.mkdir(parents=True)
    (meeting / "meeting.meta.json").write_text(json.dumps({"meeting_id": STAMP}), encoding="utf-8")
    (meeting / "note.md").write_text("# Preserve outside archive\n", encoding="utf-8")
    (graph / forget.ARCHIVE_DIR).symlink_to(outside, target_is_directory=True)
    fresh = p.rescan()
    assert graph / forget.ARCHIVE_DIR / meeting.name in fresh.delete
    _refused_without_effects(p, tmp_path, graph, isolated)


@pytest.mark.parametrize("operation", ["edit", "copy"])
def test_new_write_or_copy_through_intermediate_symlink_refuses(tmp_path, isolated, operation):
    root, graph, note, p, fresh = _pair(tmp_path)
    outside = tmp_path / "outside-notes"
    outside.mkdir()
    actual = outside / "Outside.md"
    actual.write_text("# Preserve outside file\n", encoding="utf-8")
    folder = graph / "External notes"
    folder.symlink_to(outside, target_is_directory=True)
    path = folder / actual.name
    if operation == "edit":
        fresh.edit[path] = "# New replacement\n"
    else:
        fresh.delete.append(path)
        fresh.copy_first.append(path)
    p.rescan = lambda: fresh
    _refused_without_effects(p, tmp_path, graph, isolated)
    assert actual.read_text(encoding="utf-8") == "# Preserve outside file\n"


def test_terminal_symlink_unlink_keeps_its_external_target(tmp_path):
    root, graph, note, p, fresh = _pair(tmp_path)
    outside = tmp_path / "Outside.md"
    outside.write_text("# Preserve target\n", encoding="utf-8")
    link = graph / "Встречи" / "Late symlink.md"
    link.symlink_to(outside)
    fresh.delete.append(link)
    p.rescan = lambda: fresh
    assert forget.apply(p, yes=True) and not p.refused
    assert not link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "# Preserve target\n"


@pytest.mark.parametrize("location", ["temporary", "backup"])
def test_new_write_rejects_external_temporary_or_backup_destination(tmp_path, isolated, location):
    root, graph, note, p, fresh = _pair(tmp_path)
    added = graph / "Ядра" / "Added.md"
    added.write_text("# Existing synthetic note\n", encoding="utf-8")
    fresh.edit[added] = "# New replacement\n"
    outside = tmp_path / "outside-destination"
    outside.mkdir()
    if location == "temporary":
        target = outside / "Preserve.md"
        target.write_text("# Preserve outside target\n", encoding="utf-8")
        added.with_suffix(".md.tmp").symlink_to(target)
    else:
        (graph / forget.BACKUP_DIR).symlink_to(outside, target_is_directory=True)
    p.rescan = lambda: fresh
    _refused_without_effects(p, tmp_path, graph, isolated)
    assert added.read_text(encoding="utf-8") == "# Existing synthetic note\n"


def test_external_mirror_control_already_refuses_changed_archive_owner(tmp_path, isolated):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    archive = (charoite_paths.graph_backups(graph, "cloud_backup", root=root)
               / "synthetic-run" / forget.ARCHIVE_DIR / "2030-01-01 10-00 — Synthetic")
    archive.mkdir(parents=True)
    meta = archive / "meeting.meta.json"
    meta.write_text(json.dumps({"meeting_id": STAMP}), encoding="utf-8")
    (archive / "note.md").write_text("# Keep mirror\n", encoding="utf-8")
    p = forget.plan(STAMP, root, graph)
    assert archive in p.delete
    assert any(forget._under(archive, d, g) for d, g in p.lock_dirs.items())
    meta.write_text(json.dumps({"meeting_id": OTHER}), encoding="utf-8")
    assert archive not in p.rescan().delete
    _refused_without_effects(p, tmp_path, graph, isolated)


@pytest.mark.parametrize("kind", ["missing", "file", "existing"])
def test_explicit_recovery_graph_must_be_a_directory(tmp_path, monkeypatch, kind):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    target = graph if kind == "existing" else tmp_path / "unusable-graph"
    if kind == "file":
        target.write_text("not a graph directory\n", encoding="utf-8")
    charoite_paths.use_data_root(root, replace=True)
    monkeypatch.setattr(night, "cfg", lambda: {})
    monkeypatch.setattr(night.signal, "signal", lambda *args: None)
    monkeypatch.setattr(sys, "argv", ["nightly_dossier.py", "--graph", str(target), "--limit", "0"])
    assert (night.main() == 0) is (kind == "existing")
    result = _night_cli(root, target)
    assert (result.returncode == 0) is (kind == "existing"), result.stdout + result.stderr


def test_old_edit_snapshot_cannot_be_replaced_by_a_fresh_snapshot(tmp_path, isolated):
    root, graph, note, p, fresh = _pair(tmp_path)
    original = p.snaps[note]
    note.write_text("# Author change after preview\n", encoding="utf-8")
    fresh.snaps[note] = safe_write.stat_snapshot(note)
    p.rescan = lambda: fresh
    _refused_without_effects(p, tmp_path, graph, isolated)
    assert p.snaps[note] == original
