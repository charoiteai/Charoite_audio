"""Only parent-accepted synthetic debriefs reach the two document copies."""
from types import SimpleNamespace

import pytest

import debrief_pipeline as pipeline
import graph_updater as graph
import meeting_archive
import meeting_stamp
from test_debrief_finalization import pending
from test_debrief_pipeline import model  # noqa: F401

pytestmark = pytest.mark.usefixtures("model")


@pytest.mark.parametrize("accept", [False, True])
def test_previous_source_finder_and_vault_survive_until_parent_acceptance(tmp_path, monkeypatch, accept):
    live, source, previous, proof = pending(tmp_path, monkeypatch)
    accepted_bytes = source.read_bytes()
    gpath = tmp_path / "graph"
    (gpath / "Документация").mkdir(parents=True)
    stamp = meeting_stamp.graph_key(live.parent, live.stem, gpath)
    folder = gpath / meeting_archive.ARCHIVE_DIR / f"{stamp[:10]} {meeting_archive.archive_time(stamp)} — встреча"
    folder.mkdir(parents=True)
    finder = folder / "Разбор.md"
    docs = gpath / "Документация" / "Стенограммы встреч"
    docs.mkdir()
    vault = docs / source.name
    finder.write_bytes(previous)
    vault.write_bytes(previous)
    untouched = folder / "Минутки.md"
    untouched.write_text("Прежний канон минуток.\n")
    previous_minutes = untouched.read_bytes()
    archive_calls = []

    def child_archive(*args, **kwargs):
        archive_calls.append(kwargs)
        assert kwargs["exclude_debrief"] is True
        return SimpleNamespace(folder=folder, canon=None)

    monkeypatch.setattr(meeting_archive, "archive_meeting", child_archive)
    monkeypatch.setattr(graph, "archive_alarms", lambda archived: [])
    graph.archive_and_publish(live, gpath, stamp, exclude_debrief=True)
    assert finder.read_bytes() == vault.read_bytes() == previous
    assert source.read_bytes() == accepted_bytes
    assert len(archive_calls) == 1

    if accept:
        accepted = pipeline.parent_publication(live, proof)
        graph.publish_accepted_debrief(accepted, gpath, stamp)
        assert source.read_bytes() == finder.read_bytes() == vault.read_bytes() == accepted_bytes
    else:
        pipeline.parent_publication(live, proof, reject=True)
        assert source.read_bytes() == finder.read_bytes() == vault.read_bytes() == previous
    assert len(archive_calls) == 1  # delivery never runs the whole archive again
    assert untouched.read_bytes() == previous_minutes


def test_accepted_delivery_failure_is_optional_and_other_copy_still_updates(tmp_path, monkeypatch):
    live, source, previous, proof = pending(tmp_path, monkeypatch)
    pipeline.parent_publication(live, proof)
    gpath = tmp_path / "graph"
    (gpath / "Документация").mkdir(parents=True)
    stamp = meeting_stamp.graph_key(live.parent, live.stem, gpath)
    folder = gpath / meeting_archive.ARCHIVE_DIR / f"{stamp[:10]} {meeting_archive.archive_time(stamp)} — встреча"
    folder.mkdir(parents=True)
    original_copy = graph.safe_write.copy_if_changed
    def fail_archive(src, dst):
        if dst.name == "Разбор.md":
            raise OSError("synthetic archive unavailable")
        return original_copy(src, dst)
    monkeypatch.setattr(graph.safe_write, "copy_if_changed", fail_archive)
    graph.publish_accepted_debrief(live, gpath, stamp)
    pipeline.verify_publication(live)
    assert (gpath / "Документация" / "Стенограммы встреч" / source.name).read_bytes() == source.read_bytes()


def test_foreign_symlink_copy_is_not_followed(tmp_path, monkeypatch):
    live, source, previous, proof = pending(tmp_path, monkeypatch)
    pipeline.parent_publication(live, proof)
    gpath = tmp_path / "graph"
    docs = gpath / "Документация" / "Стенограммы встреч"
    docs.mkdir(parents=True)
    human = tmp_path / "human.md"
    human.write_text("Чужие байты.\n")
    (docs / source.name).symlink_to(human)
    graph.publish_accepted_debrief(live, gpath, meeting_stamp.graph_key(live.parent, live.stem, gpath))
    assert human.read_text() == "Чужие байты.\n"
