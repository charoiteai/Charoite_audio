"""Упавшую встречу должен кто-то подобрать.

03.08 разбор упал на вставшей LLM в 10:33 — статус `error`, стенограмма на
месте, всё остальное не сделано. Никакой механизм к ней больше не вернулся:
встреча пролежала полдня, пока владелец не заметил, что папки перестали
появляться. Молчаливая потеря результата — худший исход из возможных.

Здесь проверяется отбор кандидатов на повтор: кого берём, кого не трогаем и
где останавливаемся.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meeting_processing import RETRY_LIMIT, MeetingStatusStore  # noqa: E402

NOW = 1_800_000_000.0


@pytest.fixture()
def store(tmp_path: Path) -> MeetingStatusStore:
    return MeetingStatusStore(tmp_path, now=lambda: NOW)


def _transcript(root: Path, stem: str) -> Path:
    d = root / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{stem}.md"
    p.write_text("# встреча\n", encoding="utf-8")
    return p


def _put(store: MeetingStatusStore, stem: str, **fields) -> Path:
    """Статус напрямую: нужны состояния, которых обычным путём не получить."""
    store.directory.mkdir(parents=True, exist_ok=True)
    transcript = _transcript(store.root, stem)
    payload = {"schema_version": 1, "meeting_id": stem, "started_at": NOW - 600,
               "updated_at": NOW, "transcript_path": str(transcript)}
    payload.update(fields)
    (store.directory / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")
    return transcript


def test_failed_meeting_is_picked_up(store):
    _put(store, "2026-08-03_1030", state="error", stage="failed", attempts=1)
    got = store.unfinished()
    assert [d["meeting_id"] for d in got] == ["2026-08-03_1030"]


def test_ready_meeting_is_left_alone(store):
    _put(store, "2026-08-03_1030", state="ready", stage="complete")
    assert store.unfinished() == []


def test_running_meeting_is_not_disturbed(store):
    """Идущую обработку трогать нельзя — получим две модели в памяти разом."""
    _put(store, "2026-08-03_1130", state="processing", stage="updating_graph",
         updated_at=NOW - 60)
    assert store.unfinished() == []


def test_abandoned_processing_is_picked_up(store):
    """Процесс умер, не дописав ни ready, ни error: SIGKILL, перезагрузка, крышка."""
    _put(store, "2026-08-02_0900", state="processing", stage="updating_graph",
         updated_at=NOW - 7200)
    assert [d["meeting_id"] for d in store.unfinished()] == ["2026-08-02_0900"]


def test_exhausted_attempts_stop_the_circle(store):
    _put(store, "2026-08-01_1200", state="error", attempts=RETRY_LIMIT)
    assert store.unfinished() == [], "после предела повторять бессмысленно"


def test_missing_transcript_is_not_retried(store):
    p = _put(store, "2026-07-30_1000", state="error", attempts=1)
    p.unlink()
    assert store.unfinished() == [], "стенограммы нет — повторять нечего"


def test_freshest_goes_first(store):
    _put(store, "2026-07-28_1000", state="error", attempts=1, updated_at=NOW - 86400)
    _put(store, "2026-08-03_1030", state="error", attempts=1, updated_at=NOW - 60)
    assert [d["meeting_id"] for d in store.unfinished()][0] == "2026-08-03_1030", \
        "вчерашняя встреча человеку нужнее недельной"


def test_failure_counts_attempts(store, tmp_path):
    """Счётчик живёт в статусе: повторять будет уже другой процесс."""
    transcript = _transcript(tmp_path, "2026-08-03_1030")
    store.failed(transcript, "ReadTimeout")
    store.failed(transcript, "ReadTimeout")
    data = json.loads((store.directory / "2026-08-03_1030.json").read_text(encoding="utf-8"))
    assert data["attempts"] == 2


def test_attempts_survive_a_processing_pass(store, tmp_path):
    """Повтор проходит через processing — счётчик не должен обнуляться,
    иначе падающая встреча крутится вечно."""
    transcript = _transcript(tmp_path, "2026-08-03_1030")
    store.failed(transcript, "ReadTimeout")
    store.processing(transcript, "updating_graph")
    store.failed(transcript, "ReadTimeout")
    data = json.loads((store.directory / "2026-08-03_1030.json").read_text(encoding="utf-8"))
    assert data["attempts"] == 2


def test_garbage_status_does_not_break_the_scan(store):
    store.directory.mkdir(parents=True, exist_ok=True)
    (store.directory / "broken.json").write_text("{не json", encoding="utf-8")
    _put(store, "2026-08-03_1030", state="error", attempts=1)
    assert [d["meeting_id"] for d in store.unfinished()] == ["2026-08-03_1030"]
