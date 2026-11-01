"""A backup and its physical graph must belong to the same locked capture."""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

import charoite_paths as paths
import graphs

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import cloud_review as cloud  # noqa: E402
import fix_action_items as fix  # noqa: E402
import forget_meeting as forget  # noqa: E402

STAMP = "2030-01-01_1000"


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "data"
    for sub in ["config", "transcripts", "recordings"]:
        (root / sub).mkdir(parents=True)
    a, b = tmp_path / "vault" / "GraphA", tmp_path / "GraphB"
    for graph in [a, b]:
        (graph / "Встречи").mkdir(parents=True)
        (graph / "Ядра").mkdir()
    (a / "Встречи" / f"{STAMP}.md").write_text("# Synthetic meeting\n")
    (root / "transcripts" / f"{STAMP}.md").write_text("Synthetic transcript\n")
    (root / "config" / "config.yaml").write_text("sufler:\n  graph_dir: " + json.dumps(str(a)) + "\n")
    link = tmp_path / "selected-link"
    link.symlink_to(a, target_is_directory=True)
    paths.use_data_root(root, replace=True)
    monkeypatch.setattr(graphs, "ICLOUD", tmp_path / "unused-icloud")
    for name in graphs.ENV_GRAPH_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(forget, "brain_forget", lambda *a, **k: "")
    monkeypatch.setattr(forget, "LOCK_WAIT", 0)
    return root, a, b, link


def test_copy_binding_survives_aba_and_actual_forget_removes_it(world, monkeypatch):
    root, a, b, link = world
    minute = a / "Встречи-архив" / "2030-01-01 10-00 — Owned topic" / "Минутки.md"
    minute.parent.mkdir(parents=True)
    original = "**Поручения:**\n- Role — SYNTHETIC_BOUND_ORIGINAL\n"
    minute.write_text(original)
    real = paths.graph_backups
    foreign = real(b, "fix_action_items", root)
    own = real(a, "fix_action_items", root)

    def transient(graph, kind="cloud_backup", root=None):
        if pathlib.Path(graph) == link and kind == "fix_action_items":
            link.unlink()
            link.symlink_to(b, target_is_directory=True)
            try:
                return real(graph, kind, root)  # real native lookup, not a fake path
            finally:
                link.unlink()
                link.symlink_to(a, target_is_directory=True)
        return real(graph, kind, root)

    monkeypatch.setattr(paths, "graph_backups", transient)
    monkeypatch.setattr(sys, "argv", ["fix_action_items.py", "--graph", str(link), "--apply"])
    assert fix.main() == 0
    assert minute.read_text() != original
    own_copies = list(own.rglob("Минутки.md"))
    assert len(own_copies) == 1 and own_copies[0].read_text() == original
    assert not list(foreign.rglob("*.md"))
    assert forget.apply(forget.plan(STAMP, root), yes=True)
    assert not minute.exists() and not own_copies[0].exists()
    assert not list(foreign.rglob("*.md"))


def test_scope_captures_immediately_and_bool_adapter_stays_exact(world):
    root, a, b, link = world
    scope = graphs.mutation_snapshot(link, root, 0)
    link.unlink()
    link.symlink_to(b, target_is_directory=True)
    with scope as captured:
        assert captured is None
    link.unlink()
    link.symlink_to(a, target_is_directory=True)
    boolean = graphs.mutation_lock(link, root, 0)
    with boolean as taken:
        assert taken is True
    boolean = graphs.mutation_lock(link, root, 0)
    link.unlink()
    link.symlink_to(b, target_is_directory=True)
    with boolean as taken:
        assert taken is False


def test_bound_paths_and_roots_do_not_repeat_native_resolution(world, monkeypatch):
    root, a, b, link = world
    with graphs.mutation_snapshot(link, root, 0) as captured:
        assert captured is not None

        def denied(*args, **kwargs):
            raise AssertionError("a captured destination must not resolve again")

        monkeypatch.setattr(paths, "physical_graph", denied)
        monkeypatch.setattr(paths, "graph_backups", denied)
        assert captured.bind(link / "Досье" / "Synthetic.md") == captured.graph / "Досье" / "Synthetic.md"
        assert captured.backup_root("cloud_backup") == captured.primary / "cloud_backup"
        with pytest.raises(ValueError):
            captured.bind(b / "unrelated.md")
        with pytest.raises(ValueError):
            captured.bind(link / ".." / "escape.md")
        with pytest.raises(ValueError):
            captured.backup_root("unknown_copy_kind")


def test_cloud_snapshot_sandbox_quarantine_and_cleanup_share_capture(world, monkeypatch):
    root, a, b, link = world
    with graphs.mutation_snapshot(link, root, 0) as captured:
        assert captured is not None

        def denied(*args, **kwargs):
            raise AssertionError("mutation used an independent path calculator")

        monkeypatch.setattr(paths, "graph_backups", denied)
        backup = cloud.backup_graph(link, STAMP, captured=captured)
        sandbox = cloud.backup_graph(link, STAMP + "-облако", source=backup, captured=captured)
        assert backup.parent == sandbox.parent == captured.backup_root("cloud_backup")
        assert cloud.quarantine_root(link, captured=captured) == captured.backup_root("cloud_quarantine")
        unrelated = b / "keep.md"
        unrelated.write_text("Synthetic other graph")
        cloud._drop_own_snapshots(link, STAMP, captured=captured)
        assert not backup.exists() and not sandbox.exists()
        assert unrelated.read_text() == "Synthetic other graph"


def test_standalone_cloud_backup_also_captures_before_destinations(world, monkeypatch):
    _root, a, _b, link = world

    def denied(*args, **kwargs):
        raise AssertionError("standalone backup used an independent calculator")

    monkeypatch.setattr(paths, "graph_backups", denied)
    backup = cloud.backup_graph(link, STAMP)
    assert (backup / "Встречи" / f"{STAMP}.md").read_text() == (a / "Встречи" / f"{STAMP}.md").read_text()
    cloud._drop_own_snapshots(link, STAMP)
    assert not backup.exists()


def test_factory_refuses_foreign_resolved_legacy_spelling(world, monkeypatch):
    root, a, b, link = world
    real = pathlib.Path.resolve
    swaps = []

    def transient(path, *args, **kwargs):
        if path == link:
            link.unlink()
            link.symlink_to(b, target_is_directory=True)
            try:
                resolved = real(path, *args, **kwargs)
                swaps.append(resolved)
                return resolved
            finally:
                link.unlink()
                link.symlink_to(a, target_is_directory=True)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", transient)
    with pytest.raises(OSError, match="legacy spelling changed"):
        paths.graph_addresses(link, root=root)
    assert swaps and swaps[0] == real(b)
    with graphs.mutation_snapshot(link, root, 0) as captured:
        assert captured is None
    assert not (root / "backups").exists()


def test_dedup_registered_copy_uses_the_captured_destination(world, monkeypatch):
    root, a, _b, link = world
    import dedup_graph
    original = a / "Встречи-архив" / "2030-01-01 10-00 — Owned topic" / "Минутки.md"
    original.parent.mkdir(parents=True)
    original.write_text("Synthetic owned minutes\n")
    duplicate = original.with_name("Минутки 2.md")
    duplicate.write_bytes(original.read_bytes())
    own = paths.graph_backups(a, "dedup_copies", root)

    def denied(*args, **kwargs):
        raise AssertionError("dedup destination used an independent path calculator")

    monkeypatch.setattr(paths, "graph_backups", denied)
    dedup_graph.park_copies(link, True)
    assert not duplicate.exists()
    copies = list(own.rglob("Минутки 2.md"))
    assert len(copies) == 1 and copies[0].read_bytes() == original.read_bytes()


def test_nested_alias_is_removed_from_the_bound_physical_source(world):
    root, a, _b, _link = world
    nested = a / "self-link"
    nested.symlink_to(a, target_is_directory=True)
    with graphs.mutation_snapshot(nested, root, 0) as captured:
        assert captured is not None
        assert captured.bind(nested / "Досье" / "Synthetic.md") == captured.graph / "Досье" / "Synthetic.md"


def test_resolved_address_calculator_performs_no_graph_lookup(world, monkeypatch):
    root, a, _b, _link = world
    import hashlib
    resolved = paths.physical_graph(a)[0]
    expected = root / "backups" / (resolved.name + "-" + hashlib.sha256(str(resolved).encode()).hexdigest()[:8])

    def denied(*args, **kwargs):
        raise AssertionError("resolved identity must not be looked up again")

    monkeypatch.setattr(pathlib.Path, "resolve", denied)
    assert paths._graph_backups_resolved(resolved, "cloud_backup", root) == expected / "cloud_backup"
