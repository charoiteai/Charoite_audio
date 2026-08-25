"""Общий flock-примитив (партия D-П6 карты оздоровления).

Тесты держат границу: helper различает занятость и системную ошибку, shared
probes не мешают друг другу, а политики ожидания остаются в call-site.
"""
from __future__ import annotations

import ast
import fcntl
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import file_lock  # noqa: E402


def test_try_acquire_distinguishes_busy_from_system_error(tmp_path, monkeypatch):
    path = tmp_path / "lock"
    first = path.open("a+")
    second = path.open("a+")
    try:
        assert file_lock.try_acquire(first) is True
        assert file_lock.try_acquire(second) is False
    finally:
        first.close()
        second.close()

    def unavailable(_fd, _mode):
        raise OSError("flock unavailable")

    monkeypatch.setattr(file_lock.fcntl, "flock", unavailable)
    with pytest.raises(OSError, match="flock unavailable"):
        file_lock.try_acquire(0)


def test_shared_probes_coexist_but_conflict_with_writer(tmp_path):
    path = tmp_path / "lock"
    path.write_text("", encoding="utf-8")
    first = path.open("r")
    second = path.open("r")
    writer = path.open("a+")
    try:
        assert file_lock.try_acquire(first, shared=True) is True
        assert file_lock.try_acquire(second, shared=True) is True
        assert file_lock.try_acquire(writer) is False
    finally:
        first.close()
        second.close()
        writer.close()


def test_blocking_acquire_forwards_mode_and_system_error(monkeypatch):
    calls = []
    monkeypatch.setattr(file_lock.fcntl, "flock",
                        lambda fd, mode: calls.append((fd, mode)))

    file_lock.acquire(7)
    file_lock.acquire(8, shared=True)

    assert calls == [(7, fcntl.LOCK_EX), (8, fcntl.LOCK_SH)]

    def unavailable(_fd, _mode):
        raise OSError("blocking flock unavailable")

    monkeypatch.setattr(file_lock.fcntl, "flock", unavailable)
    with pytest.raises(OSError, match="blocking flock unavailable"):
        file_lock.acquire(9)


def test_is_held_detects_writer_and_releases_probe(tmp_path):
    path = tmp_path / "lock"
    path.write_text("", encoding="utf-8")

    assert file_lock.is_held(path) is False
    writer = path.open("a+")
    fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert file_lock.is_held(path) is True
    finally:
        writer.close()
    assert file_lock.is_held(path) is False


def test_is_held_does_not_invent_busy_when_probe_is_unavailable(tmp_path, monkeypatch):
    assert file_lock.is_held(tmp_path / "missing") is False
    path = tmp_path / "lock"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(file_lock, "try_acquire",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("no flock")))

    assert file_lock.is_held(path) is False


def _raw_flock_calls(path: pathlib.Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "fcntl":
                    modules.add(name.asname or "fcntl")
        elif isinstance(node, ast.ImportFrom) and node.module == "fcntl":
            for name in node.names:
                if name.name == "flock":
                    functions.add(name.asname or "flock")
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ((isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id in modules and node.func.attr == "flock")
                 or (isinstance(node.func, ast.Name) and node.func.id in functions))]


def test_production_flock_calls_live_only_in_helper():
    offenders = {}
    for folder in (ROOT / "src", ROOT / "scripts"):
        for path in sorted(folder.glob("*.py")):
            if path.name == "file_lock.py":
                continue
            lines = _raw_flock_calls(path)
            if lines:
                offenders[str(path.relative_to(ROOT))] = lines

    assert offenders == {}, f"ручные flock в обход file_lock: {offenders}"


def test_each_owner_uses_only_the_primitive_it_needs():
    expected = {
        "src/live_gate.py": {"file_lock.is_held(": 1},
        "src/busy_signals.py": {"file_lock.is_held(": 1,
                                "file_lock.try_acquire(": 1},
        "src/daemon.py": {"file_lock.try_acquire(": 1},
        "src/rebuild_transcript.py": {"file_lock.try_acquire(": 1,
                                      "file_lock.acquire(": 1},
        "scripts/cloud_review.py": {"file_lock.try_acquire(": 1},
    }
    wrong = {}
    for rel, calls in expected.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        for call, count in calls.items():
            if source.count(call) != count:
                wrong[f"{rel}:{call}"] = (source.count(call), count)

    assert wrong == {}, f"политика лока ушла от владельца или вернулась копией: {wrong}"
