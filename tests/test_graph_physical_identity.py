"""Physical graph addresses, bounded history and complete mutation lock groups."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import pathlib
import sys

import pytest

import charoite_paths as paths
import file_locks
import graphs

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import forget_meeting as forget  # noqa: E402

STAMP = "2030-01-01_1000"
ARCHIVE = "2030-01-01 10-00 — Synthetic topic"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in graphs.ENV_GRAPH_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(forget, "brain_forget", lambda *a, **kw: "")
    monkeypatch.setattr(forget, "LOCK_WAIT", 0)


def _world(tmp_path):
    root = tmp_path / "data"
    (root / "config").mkdir(parents=True)
    (root / "transcripts").mkdir()
    (root / "transcripts" / f"{STAMP}.md").write_text("Synthetic transcript\n")
    graph = tmp_path / "vault" / "WorkCase"
    (graph / "Встречи").mkdir(parents=True)
    (graph / "Встречи" / f"{STAMP}.md").write_text("# Synthetic meeting\n")
    (graph / "Встречи-архив" / ARCHIVE).mkdir(parents=True)
    (graph / "Встречи-архив" / ARCHIVE / "Минутки.md").write_text("Synthetic minutes\n")
    paths.use_data_root(root, replace=True)
    alias = graph.with_name(graph.name.lower())
    configured = alias if alias.exists() else graph
    (root / "config" / "config.yaml").write_text(f'sufler:\n  graph_dir: "{configured}"\n')
    return root, graph


@pytest.mark.parametrize("left,right", [("WorkCase", "workcase"), ("Graph-é", "Graph-e\u0301"),
                                          ("Graph-e\u0301", "Graph-é")])
def test_native_aliases_converge_but_distinct_directories_do_not(tmp_path, left, right):
    graph = tmp_path / left
    graph.mkdir()
    alias = tmp_path / right
    if not alias.exists():
        alias.mkdir()  # Linux case/Unicode-sensitive control, never fold names.
        assert not alias.samefile(graph)
        assert paths.physical_graph(alias)[0] != paths.physical_graph(graph)[0]
        return
    assert alias.samefile(graph)
    parent_alias = tmp_path.with_name(tmp_path.name.swapcase())
    choices = [graph, alias]
    if parent_alias.exists():
        choices.append(parent_alias / right)
    link = tmp_path / "graph-link"
    link.symlink_to(graph, target_is_directory=True)
    choices.append(link)
    assert len({paths.physical_graph(p) for p in choices}) == 1
    assert len({paths.graph_backups(p, root=tmp_path) for p in choices}) == 1


def test_old_formula_is_exact_and_missing_calculator_is_pure(tmp_path):
    graph = tmp_path / "absent"
    old = tmp_path / "backups" / (graph.name + "-" + hashlib.sha256(str(graph.resolve()).encode()).hexdigest()[:8])
    assert paths.legacy_graph_backups(graph, "fix_action_items", tmp_path) == old / "fix_action_items"
    assert paths.graph_backups(graph, root=tmp_path) == old / "cloud_backup"
    assert not (tmp_path / "backups").exists()
    with pytest.raises(FileNotFoundError):
        paths.graph_addresses(graph, root=tmp_path)


def test_required_alias_is_revalidated_and_unrelated_optional_is_excluded(tmp_path):
    graph, other = tmp_path / "graph", tmp_path / "other"
    graph.mkdir()
    other.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(graph, target_is_directory=True)
    group = paths.graph_addresses(alias, root=tmp_path, aliases=[other, tmp_path / "missing"])
    assert other not in group.aliases
    assert group.lock_dirs == (group.primary,)
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)
    with pytest.raises(OSError):
        group.revalidate()
    with paths.graph_lock_group(group, 0) as taken:
        assert not taken


def test_native_error_closes_fd_and_never_returns_lexical_identity(tmp_path, monkeypatch):
    graph = tmp_path / "graph"
    graph.mkdir()
    original = paths.os.open
    opened = []

    def record(*args, **kwargs):
        fd = original(*args, **kwargs)
        opened.append(fd)
        return fd

    def unavailable(*args, **kwargs):
        raise OSError(errno.EIO, "synthetic native path failure")

    monkeypatch.setattr(paths.os, "open", record)
    if sys.platform == "darwin":
        monkeypatch.setattr(paths.fcntl, "fcntl", unavailable)
    else:
        monkeypatch.setattr(paths.os, "readlink", unavailable)
    with pytest.raises(OSError, match="synthetic native"):
        paths.graph_addresses(graph, root=tmp_path)
    assert len(opened) == 1
    with pytest.raises(OSError) as exc:
        paths.os.fstat(opened[0])
    assert exc.value.errno == errno.EBADF


@pytest.mark.parametrize("error", [errno.EACCES, errno.ENOENT, errno.ENOTDIR, errno.EIO])
def test_strict_mutation_errors_never_enter_the_body(tmp_path, monkeypatch, error):
    graph = tmp_path / "graph"
    graph.mkdir()
    keep = graph / "keep.md"
    keep.write_bytes(b"Synthetic original bytes")

    def denied(*args, **kwargs):
        raise OSError(error, "synthetic identity unavailable")

    monkeypatch.setattr(paths.os, "open", denied)
    with graphs.mutation_lock(graph, tmp_path, 0, cfg={}) as taken:
        assert not taken
    assert keep.read_bytes() == b"Synthetic original bytes"
    assert not (tmp_path / "backups").exists()


def test_lock_group_partial_failure_releases_acquired_siblings(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    with file_locks.graph_lock(second, 0) as held:
        assert held
        with file_locks.graph_locks([second, first], 0) as acquired:
            assert not acquired
        with file_locks.graph_lock(first, 0) as released:
            assert released


def test_replaced_directory_during_native_lookup_refuses_identity(tmp_path, monkeypatch):
    graph = tmp_path / "graph"
    graph.mkdir()
    kept = graph / "keep.md"
    kept.write_text("Synthetic old graph")
    old = tmp_path / "old-graph"

    def replace_after_lookup(original):
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            graph.rename(old)
            graph.mkdir()
            return result
        return changed

    if sys.platform == "darwin":
        monkeypatch.setattr(paths.fcntl, "fcntl", replace_after_lookup(paths.fcntl.fcntl))
    else:
        monkeypatch.setattr(paths.os, "readlink", replace_after_lookup(paths.os.readlink))
    with pytest.raises(OSError) as exc:
        paths.physical_graph(graph)
    assert exc.value.errno == errno.ESTALE
    assert (old / "keep.md").read_text() == "Synthetic old graph"
    assert list(graph.iterdir()) == []


def test_lock_group_has_one_deadline_and_reverse_release(tmp_path, monkeypatch):
    clock, waits, releases = [0], [], []

    @contextlib.contextmanager
    def capture(directory, wait, **kwargs):
        waits.append(wait)
        clock[0] += 2
        try:
            yield True
        finally:
            releases.append(directory.name)

    monkeypatch.setattr(file_locks, "graph_lock", capture)
    with file_locks.graph_locks([tmp_path / n for n in "cba"], 5, now=lambda: clock[0]) as taken:
        assert taken
    assert waits == [5, 3, 1]
    assert releases == ["c", "b", "a"]


def test_any_action_activates_whole_graph_lock_group(tmp_path):
    graph, primary, legacy = (tmp_path / n for n in ["graph", "primary", "legacy"])
    p = forget.Plan(STAMP, delete=[primary / "copy.md"], lock_dirs={primary: graph, legacy: graph})
    assert forget._plan_locks(p) == sorted([primary, legacy])


def test_forget_reads_every_proven_mirror_and_preserves_other_rows(tmp_path):
    root, graph = _world(tmp_path)
    group = graphs.mutation_addresses(graph, root=root)
    original = "Synthetic original\n"
    mine, manifests, snapshots, quarantines = [], [], [], []
    for base in group.lock_dirs:
        for kind, inner in paths.GRAPH_BACKUP_KINDS.items():
            if inner is None:
                q = base / kind / (STAMP + "-010101")
                q.mkdir(parents=True)
                (q / "original.md").write_text(original)
                quarantines.append(q)
                displaced = base / kind / forget.DISPLACED_DIR / (STAMP + "-010102")
                displaced.mkdir(parents=True)
                (displaced / "original.md").write_text(original)
                quarantines.append(displaced)
                continue
            run = base / kind / "run"
            mirror = run / inner if inner else run
            path = mirror / "Встречи-архив" / ARCHIVE / "Минутки.md"
            path.parent.mkdir(parents=True)
            path.write_text(original)
            mine.append(path)
            manifest = run / "manifest.tsv"
            rel = path.relative_to(mirror).as_posix()
            manifest.write_text("файл\tsha256 до\tsha256 после\n" + rel + "\tbefore\tafter\nother.md\tkeep\tkeep\n")
            manifests.append(manifest)
            if kind == "cloud_backup":
                snapshot = mirror / "Ядра" / "Synthetic topic.md"
                snapshot.parent.mkdir()
                snapshot.write_text(f"# Keep\n- [[Встречи/{STAMP}]] forgotten\n- Preserve synthetic unrelated fact\n")
                snapshots.append(snapshot)
    old = graph / forget.CLOUD_BACKUP_DIR / "old-run" / "Встречи-архив" / ARCHIVE / "Минутки.md"
    old.parent.mkdir(parents=True)
    old.write_text(original)
    assert forget.apply(forget.plan(STAMP, root), yes=True)
    assert all(not p.exists() for p in [*mine, *quarantines, old])
    assert all(ARCHIVE not in p.read_text() and "other.md" in p.read_text() for p in manifests)
    assert all(STAMP not in p.read_text() and "Preserve synthetic unrelated fact" in p.read_text() for p in snapshots)


def test_alias_evidence_changed_after_plan_refuses_before_effects(tmp_path):
    root, graph = _world(tmp_path)
    p = forget.plan(STAMP, root, graph)
    before = {path: path.read_bytes() for path in graph.rglob("*") if path.is_file()}
    link = tmp_path / "config-link"
    link.symlink_to(graph, target_is_directory=True)
    (root / "config" / "config.yaml").write_text(f'sufler:\n  graph_dir: "{link}"\n')
    assert not forget.apply(p, yes=True) and p.refused
    assert all(path.read_bytes() == body for path, body in before.items())
    assert (root / "transcripts" / f"{STAMP}.md").exists()


def test_explicit_environment_and_supplied_config_never_load_ambient(tmp_path, monkeypatch):
    graph = tmp_path / "graph"
    graph.mkdir()

    def denied():
        raise AssertionError("ambient config must not be read")

    monkeypatch.setattr(graphs, "load_config", denied)
    assert graphs.mutation_addresses(graph, root=tmp_path, cfg={}).graph == paths.physical_graph(graph)[0]
    for name in graphs.ENV_GRAPH_NAMES:
        monkeypatch.setenv(name, str(graph))
        assert graphs.mutation_addresses(graph, root=tmp_path).graph == paths.physical_graph(graph)[0]
        monkeypatch.delenv(name)
