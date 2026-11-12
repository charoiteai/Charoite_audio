"""Byte preservation and atomic publication of dossier preimages."""
from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import shutil
import stat

import pytest

from charoite_graph import dossier

STAMP = "2030-01-01_010101"
A = b"first synthetic preimage\n"
B = b"second synthetic preimage\n"


@pytest.mark.parametrize("second", [A, B])
def test_same_stamp_refuses_without_overwriting_preimage(tmp_path, second):
    source = tmp_path / "Sample.md"
    source.write_bytes(A)
    first = dossier.backup(tmp_path, STAMP, source)
    source.write_bytes(second)
    with pytest.raises(FileExistsError):
        dossier.backup(tmp_path, STAMP, source)
    restored = tmp_path / "restored.md"
    shutil.copyfile(first, restored)
    assert restored.read_bytes() == A
    assert source.read_bytes() == second
    assert list(first.parent.iterdir()) == [first]


@pytest.mark.parametrize("kind", ["directory", "symlink", "dangling_symlink"])
def test_occupied_destination_is_not_followed_or_replaced(tmp_path, kind):
    source = tmp_path / "Sample.md"
    source.write_bytes(B)
    dest = tmp_path / ".backup" / STAMP / source.name
    dest.parent.mkdir(parents=True)
    target = tmp_path / "target.md"
    if kind == "directory":
        dest.mkdir()
        (dest / "keep").write_bytes(A)
    else:
        if kind == "symlink":
            target.write_bytes(A)
        dest.symlink_to(target)
    before = dest.lstat()
    with pytest.raises(FileExistsError):
        dossier.backup(tmp_path, STAMP, source)
    assert dest.lstat().st_ino == before.st_ino
    assert source.read_bytes() == B
    assert list(dest.parent.iterdir()) == [dest]
    if kind == "directory":
        assert (dest / "keep").read_bytes() == A
    else:
        assert dest.is_symlink() and dest.readlink() == target
        assert target.read_bytes() == A if kind == "symlink" else not target.exists()


def test_complete_copy_metadata_and_distinct_names(tmp_path, monkeypatch):
    source = tmp_path / "Sample.md"
    source.write_bytes(A)
    source.chmod(0o640)
    os.utime(source, ns=(1_600_000_000_000_000_000, 1_600_000_000_123_456_789))
    expected_stat = source.stat()
    final = tmp_path / ".backup" / STAMP / source.name
    real_copy = shutil.copy2

    def staged_copy(src, dst):
        assert not final.exists(), "publication preceded completion"
        result = real_copy(src, dst)
        assert not final.exists(), "copy overwrote final destination directly"
        return result

    with monkeypatch.context() as patch:
        patch.setattr(dossier.shutil, "copy2", staged_copy)
        copy = dossier.backup(tmp_path, STAMP, source)
    assert copy.read_bytes() == A
    assert copy.stat().st_mtime_ns == expected_stat.st_mtime_ns
    assert stat.S_IMODE(copy.stat().st_mode) == 0o640
    assert copy.stat().st_nlink == 1
    other = tmp_path / "Other.md"
    other.write_bytes(B)
    second = dossier.backup(tmp_path, STAMP, other)
    assert second.read_bytes() == B and copy.read_bytes() == A
    assert set(copy.parent.iterdir()) == {copy, second}


@pytest.mark.parametrize("failure", ["copy", "metadata", "link"])
def test_failed_copy_publishes_nothing_and_cleans_staging(tmp_path, monkeypatch, failure):
    source = tmp_path / "Sample.md"
    source.write_bytes(A)
    final = tmp_path / ".backup" / STAMP / source.name

    def fail(*args, **kwargs):
        raise PermissionError("synthetic failure")

    def partial(src, dst):
        Path(dst).write_bytes(b"partial")
        assert not final.exists()
        fail()

    if failure == "copy":
        monkeypatch.setattr(dossier.shutil, "copy2", partial)
    elif failure == "metadata":
        monkeypatch.setattr(dossier.shutil, "copystat", fail)
    else:
        monkeypatch.setattr(dossier.os, "link", fail)
    with pytest.raises(PermissionError):
        dossier.backup(tmp_path, STAMP, source)
    assert source.read_bytes() == A
    assert not final.exists()
    assert list(final.parent.iterdir()) == []


def test_collision_does_not_prune_and_missing_source_stays_missing(tmp_path):
    source = tmp_path / "Sample.md"
    assert dossier.backup(tmp_path, STAMP, source) is None
    assert not (tmp_path / ".backup").exists()
    source.write_bytes(A)
    copy = dossier.backup(tmp_path, STAMP, source)
    newer = tmp_path / ".backup" / "2030-01-02_010101"
    newer.mkdir()
    (newer / source.name).write_bytes(B)
    with pytest.raises(FileExistsError):
        dossier.backup(tmp_path, STAMP, source, keep=1)
    assert copy.read_bytes() == A
    assert (newer / source.name).read_bytes() == B


def _publish_competitor(folder, source, barrier, results):
    """Separate processes reach publication after both complete their copies."""
    real_copy = dossier.shutil.copy2

    def stage(*args, **kwargs):
        result = real_copy(*args, **kwargs)
        barrier.wait(timeout=10)
        return result

    dossier.shutil.copy2 = stage
    try:
        copy = dossier.backup(Path(folder), STAMP, Path(source))
        results.put(("published", Path(source).read_bytes(), copy.read_bytes()))
    except FileExistsError:
        results.put(("collision", Path(source).read_bytes(), None))
    finally:
        dossier.shutil.copy2 = real_copy


def test_concurrent_publication_has_one_whole_winner(tmp_path):
    # No retention pruner participates: production callers serialize pruning
    # using their graph mutation lock; this tests the publication primitive.
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(2), ctx.Queue()
    sources = []
    for name, data in (("a", A), ("b", B)):
        source = tmp_path / name / "Sample.md"
        source.parent.mkdir()
        source.write_bytes(data)
        sources.append(source)
    workers = [ctx.Process(target=_publish_competitor,
                           args=(str(tmp_path), str(p), barrier, results)) for p in sources]
    try:
        for worker in workers:
            worker.start()
        observations = [results.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert sorted(r[0] for r in observations) == ["collision", "published"]
        winner = next(r for r in observations if r[0] == "published")
        copy = tmp_path / ".backup" / STAMP / "Sample.md"
        assert copy.read_bytes() == winner[1] == winner[2]
        assert list(copy.parent.iterdir()) == [copy]
        assert [p.read_bytes() for p in sources] == [A, B]
    finally:
        for worker in workers:
            if worker.pid is not None:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=10)
        results.close()
