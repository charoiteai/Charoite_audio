"""The macOS app receives honest, recoverable post-meeting state."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from meeting_processing import (  # noqa: E402
    MeetingStatusStore,
    STATUS_KEEP_DAYS,
    find_final_transcript,
    find_meeting_note,
)


def _transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "transcripts" / "2026-07-31_141501.md"
    path.parent.mkdir()
    path.write_text("# Встреча\n" + "текст " * 80, encoding="utf-8")
    return path


def test_processing_status_is_atomic_and_preserves_start_time(tmp_path):
    live = _transcript(tmp_path)
    clock = iter([100.0, 120.0])
    store = MeetingStatusStore(tmp_path, now=lambda: next(clock))

    path = store.processing(live, "waiting_for_audio")
    store.processing(live, "updating_graph")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["state"] == "processing"
    assert data["stage"] == "updating_graph"
    assert data["started_at"] == 100.0
    assert data["updated_at"] == 120.0
    assert not list(path.parent.glob(".*.json.*")), "atomic temp file leaked"


def test_ready_status_points_to_exact_note(tmp_path):
    live = _transcript(tmp_path)
    note = tmp_path / "graph" / "Встречи" / "2026-07-31_1415.md"
    note.parent.mkdir(parents=True)
    note.write_text("готово", encoding="utf-8")
    clock = iter([10.0, 20.0])
    store = MeetingStatusStore(tmp_path, now=lambda: next(clock))
    store.processing(live, "rebuilding_transcript")

    path = store.ready(live, note)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["state"] == "ready"
    assert data["note_path"] == str(note.resolve())
    assert data["transcript_path"] == str(live.resolve())


def test_failure_keeps_recovery_transcript(tmp_path):
    live = _transcript(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)

    path = store.failed(live, "Ollama недоступна")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["state"] == "error"
    assert data["transcript_path"] == str(live.resolve())
    assert "Ollama" in data["error"]
    assert live.exists(), "reporting a failure must not consume the source"


def test_renamed_transcript_remains_recoverable(tmp_path):
    live = _transcript(tmp_path)
    renamed = live.with_name("2026-07-31_1415_План_релиза.md")
    live.rename(renamed)

    assert find_final_transcript(live) == renamed.resolve()
    data_path = MeetingStatusStore(tmp_path, now=lambda: 10.0).failed(live, "later failure")
    data = json.loads(data_path.read_text(encoding="utf-8"))
    assert data["transcript_path"] == str(renamed.resolve())


def test_auxiliary_file_is_not_mistaken_for_renamed_transcript(tmp_path):
    live = _transcript(tmp_path)
    live.unlink()
    minutes = live.with_name("2026-07-31_1415_minutes.md")
    minutes.write_text("minutes", encoding="utf-8")

    assert find_final_transcript(live) == live.resolve()


def test_review_file_is_not_mistaken_for_renamed_transcript(tmp_path):
    """Инцидент 04.08: «_разбор» не было в списке производных, файл разбора
    свежее стенограммы — и он выигрывал по mtime. Тема встречи в приложении
    превращалась в «… разбор», а «Стенограмма» открывала разбор."""
    import os

    live = _transcript(tmp_path)
    live.unlink()
    renamed = live.with_name("2026-07-31_1415_Отчет_по_задачам.md")
    renamed.write_text("стенограмма", encoding="utf-8")
    review = live.with_name("2026-07-31_1415_Отчет_по_задачам_разбор.md")
    review.write_text("разбор", encoding="utf-8")
    os.utime(renamed, (100, 100))
    os.utime(review, (200, 200))  # разбор всегда моложе стенограммы

    assert find_final_transcript(live) == renamed.resolve()


def test_note_is_found_in_project_graph(tmp_path):
    configured = tmp_path / "vault" / "Рабочий"
    configured.mkdir(parents=True)
    project = configured.parent / "Charoite"
    note = project / "Встречи" / "2026-07-31_1415.md"
    note.parent.mkdir(parents=True)
    note.write_text("note", encoding="utf-8")
    live = _transcript(tmp_path)

    found = find_meeting_note({"sufler": {"graph_dir": str(configured)}}, live)

    assert found == note.resolve()


def test_graph_env_override_wins(tmp_path, monkeypatch):
    configured = tmp_path / "wrong"
    override = tmp_path / "right"
    note = override / "Встречи" / "2026-07-31_1415.md"
    note.parent.mkdir(parents=True)
    note.write_text("note", encoding="utf-8")
    monkeypatch.setenv("SUFLER_GRAPH_DIR", str(override))

    found = find_meeting_note({"sufler": {"graph_dir": str(configured)}}, _transcript(tmp_path))

    assert found == note.resolve()


def test_old_note_from_same_minute_is_not_reported_ready(tmp_path):
    graph = tmp_path / "graph"
    note = graph / "Встречи" / "2026-07-31_1415.md"
    note.parent.mkdir(parents=True)
    note.write_text("previous meeting", encoding="utf-8")
    os.utime(note, (100, 100))

    found = find_meeting_note(
        {"sufler": {"graph_dir": str(graph)}},
        _transcript(tmp_path),
        newer_than=200,
    )

    assert found is None


def test_old_status_files_are_pruned(tmp_path):
    status_dir = tmp_path / "logs" / "meeting-status"
    status_dir.mkdir(parents=True)
    old = status_dir / "old.json"
    old.write_text("{}", encoding="utf-8")
    old_time = time.time() - (STATUS_KEEP_DAYS + 1) * 86400
    os.utime(old, (old_time, old_time))

    MeetingStatusStore(tmp_path)._prune(time.time())

    assert not old.exists()
