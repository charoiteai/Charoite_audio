"""CLI-контроли индекса после забывания: синтетика, fault injection и восстановление."""
from __future__ import annotations

import fcntl
import os
import pathlib
import shlex
import socket
import subprocess
import sys
import time

import pytest

from test_forget_night_processes import (
    REPO, STAMP, OTHER, THIRD, THEME, _world, _seed_indexes, _body,
    charoite_paths, CHAROITE,
)
from charoite_graph import dossier
import forget_meeting as forget
import nightly_dossier as night

NEIGHBOR = "Neighbor dossier"
TIMEOUT_S = 15


def _neighbor(graph):
    (graph / "Ядра" / f"{NEIGHBOR}.md").write_text("# Neighbor core\n", encoding="utf-8")
    for stamp in (OTHER, THIRD):
        path = graph / "Встречи" / f"{stamp}.md"
        path.write_text(path.read_text(encoding="utf-8") + f"[[Ядра/{NEIGHBOR}]]\n", encoding="utf-8")
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    members = dossier.clusters(files, backlinks, schema=CHAROITE)[NEIGHBOR]
    body = _body("neighborword").replace(STAMP, OTHER)
    (graph / CHAROITE.dossier_dir / f"{NEIGHBOR}.md").write_text(
        dossier.render(NEIGHBOR, body, members, files, dossier.fingerprint(members, files), "2030-01-04"),
        encoding="utf-8")


def _cli(root, graph, *, fail_md=False):
    config = root / "config" / "config.yaml"
    config.parent.mkdir(exist_ok=True)
    config.write_text('llm:\n  model: ""\n', encoding="utf-8")
    command = [sys.executable, str(pathlib.Path(__file__).resolve()), "cli", str(root), str(graph)]
    if fail_md:
        command.append("fail-md")
    env = dict(os.environ, CHAROITE_ROOT=str(root))
    return subprocess.run(command, capture_output=True, text=True, cwd=REPO, env=env, timeout=TIMEOUT_S)


def _assert_neighbor_only(graph):
    folder = graph / CHAROITE.dossier_dir
    assert {e["тема"] for e in dossier.load_index(folder)} == {NEIGHBOR}
    md = (folder / dossier.INDEX_MD).read_text(encoding="utf-8")
    assert NEIGHBOR in md and THEME not in md


@pytest.mark.parametrize("index_state", ["missing", "broken", "outdated"])
def test_forget_cli_preserves_live_dossier_with_missing_broken_or_outdated_json(tmp_path, index_state):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    _neighbor(graph)  # Живой файл ещё не включён в старый JSON.
    detached = graph / CHAROITE.dossier_dir / "Detached dossier.md"
    detached.write_text("## Сейчас\nManual dossier outside clusters.\n", encoding="utf-8")
    index = graph / CHAROITE.dossier_dir / dossier.INDEX_JSON
    if index_state == "missing":
        index.unlink()
    elif index_state == "broken":
        index.write_text("{broken", encoding="utf-8")
    result = _cli(root, graph)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (graph / "Встречи" / f"{STAMP}.md").exists()
    assert (graph / CHAROITE.dossier_dir / f"{NEIGHBOR}.md").exists()
    assert detached.exists()
    _assert_neighbor_only(graph)


def test_markdown_failure_has_a_working_repair_command_using_the_same_lock(tmp_path):
    root, graphs = _world(tmp_path / "with spaces")
    graph = graphs[0]
    _seed_indexes(graph)
    _neighbor(graph)
    dossier.refresh_index(graph / CHAROITE.dossier_dir, [THEME, NEIGHBOR])
    failed = _cli(root, graph, fail_md=True)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert not (graph / "Встречи" / f"{STAMP}.md").exists()
    assert {e["тема"] for e in dossier.load_index(graph / CHAROITE.dossier_dir)} == {NEIGHBOR}
    assert THEME in (graph / CHAROITE.dossier_dir / dossier.INDEX_MD).read_text(encoding="utf-8")
    # Повтор прежнего forget после удаления ключа ничего не восстанавливает.
    retried = _cli(root, graph)
    assert retried.returncode == 1 and "не найдена" in retried.stdout
    prefix = "обновить индексы: "
    instructions = [line.removeprefix(prefix) for line in failed.stdout.splitlines() if line.startswith(prefix)]
    assert len(instructions) == 1, failed.stdout
    command = shlex.split(instructions[0])
    env = dict(os.environ)
    assignment = command.pop(0)
    key, value = assignment.split("=", 1)
    assert key == "CHAROITE_ROOT" and pathlib.Path(value) == root
    env[key] = value
    assert command[-2:] == ["--limit", "0"], "восстановление индекса не должно звать модель"
    folder = graph / CHAROITE.dossier_dir
    before = {name: (folder / name).read_bytes() for name in (dossier.INDEX_JSON, dossier.INDEX_MD)}
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=root))
    with (lock_dir / "cloud.lock").open("a+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        busy = subprocess.run(command, capture_output=True, text=True, env=env, cwd=REPO, timeout=TIMEOUT_S)
        assert busy.returncode == 0, busy.stdout + busy.stderr
        assert all((folder / name).read_bytes() == data for name, data in before.items())
        fcntl.flock(held, fcntl.LOCK_UN)
    repaired = subprocess.run(command, capture_output=True, text=True, env=env, cwd=REPO, timeout=TIMEOUT_S)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    _assert_neighbor_only(graph)


@pytest.mark.parametrize("source_change", ["deleted", "unlinked"])
def test_nightly_legacy_fallback_drops_a_source_removed_after_scan(tmp_path, monkeypatch, source_change):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    charoite_paths.use_data_root(root, replace=True)
    source = graph / "Встречи" / "Retired source.md"
    source.write_text(f"# Synthetic source\n[[Ядра/{THEME}]]\n", encoding="utf-8")
    (graph / CHAROITE.dossier_dir / f"{THEME}.md").write_text(
        "## Сейчас\nLegacy dossier without metadata.\n## Правки автора\n\nAuthor note.\n", encoding="utf-8")

    def generate(*args, **kwargs):
        with night._graph_lock(graph, 0) as taken:
            assert taken
            if source_change == "deleted":
                source.unlink()
            else:
                seen = source.stat()
                source.write_text("# Synthetic source without a topic link\n", encoding="utf-8")
                os.utime(source, ns=(seen.st_atime_ns, seen.st_mtime_ns + 1_000_000))
        return _body()

    monkeypatch.setattr(night, "generate", generate)
    monkeypatch.setattr(night.live_gate, "night_window_open", lambda *args, **kwargs: True)
    result = night.run(graph, {}, full=True, dry=False, limit=None)
    assert result["собрано"] == 0 and result["досье_изменилось"] == 1
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    current = dossier.clusters(files, backlinks, schema=CHAROITE)[THEME]
    entry = dossier.load_index(graph / CHAROITE.dossier_dir)[0]
    assert entry["источников"] == len(current), entry
    assert not set(dossier.keywords("Retired source")).intersection(entry["ключи"]), entry


def test_rebuild_index_uses_one_measured_synthetic_graph_pass(tmp_path, monkeypatch):
    root, graphs = _world(tmp_path)
    graph = graphs[0]
    _seed_indexes(graph)
    _neighbor(graph)
    original_scan = dossier.scan
    original_read = pathlib.Path.read_bytes
    scans, source_reads = [], []

    def scan(*args, **kwargs):
        scans.append(1)
        return original_scan(*args, **kwargs)

    def read(path):
        if path.is_relative_to(graph) and not path.is_relative_to(graph / CHAROITE.dossier_dir):
            source_reads.append(path)
        return original_read(path)

    monkeypatch.setattr(dossier, "scan", scan)
    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    lock_dir = charoite_paths.secure_dir(charoite_paths.graph_lock_dir(graph, root=root))
    with (lock_dir / "cloud.lock").open("a+b") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.perf_counter()
        dossier.rebuild_index(graph, schema=CHAROITE)
        elapsed = time.perf_counter() - started
        fcntl.flock(held, fcntl.LOCK_UN)
    assert len(scans) == 1
    assert len(source_reads) == len(set(source_reads)) == 5
    assert {e["тема"] for e in dossier.load_index(graph / CHAROITE.dossier_dir)} == {THEME, NEIGHBOR}
    print(f"synthetic rebuild: scan_calls={len(scans)}, source_files={len(source_reads)}, elapsed_s={elapsed:.6f}")


def _cli_worker(root, graph, fail_md):
    charoite_paths.use_data_root(root, replace=True)

    def denied(*args, **kwargs):
        raise AssertionError("network is forbidden in the synthetic CLI")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    forget.brain_forget = lambda *args, **kwargs: ""
    if fail_md:
        original = pathlib.Path.replace

        def replace(path, target):
            if pathlib.Path(target) == graph / CHAROITE.dossier_dir / dossier.INDEX_MD:
                raise OSError("synthetic Markdown publication failure")
            return original(path, target)

        pathlib.Path.replace = replace
    sys.argv = ["forget_meeting.py", STAMP, "--graph", str(graph), "--yes"]
    return forget.main()


if __name__ == "__main__":
    raise SystemExit(_cli_worker(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), len(sys.argv) > 4))
