"""Признак «имена не определены» считается по тексту стенограммы (№501).

Плашка в шапке несёт список безымянных меток. Читатель — чистая функция:
потеря есть, только пока эти метки ещё стоят в заголовках речи. Статус
пишет то же самое под замком, без флага, пронесённого через конвейер.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import pathlib
import sys
import threading
import time

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import live_sidecar  # noqa: E402
import meeting_processing  # noqa: E402
import name_fixes as nf  # noqa: E402
import rebuild_transcript as rt  # noqa: E402
import transcript  # noqa: E402
from meeting_processing import MeetingStatusStore  # noqa: E402

CFG = {"audio": {"samplerate": 16000}, "log": {}}


def _stamp_file(folder: pathlib.Path, name: str, text: str) -> pathlib.Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def _owned(folder: pathlib.Path, name: str, text: str) -> pathlib.Path:
    live = _stamp_file(folder, name, text)
    live.with_name(live.name + ".live.json").write_text(
        json.dumps({"transcript_sha256": live_sidecar.sha(text)}), encoding="utf-8")
    return live


def _speech(banner: str, headers: list[tuple[str, str]]) -> str:
    parts = ["# Встреча 2026-09-11_1533", "", banner, ""]
    for label, line in headers:
        parts += [f"**{label}** [15:33]:", line, ""]
    return "\n".join(parts)


def test_hand_edited_names_clear_the_pending_flag_and_keep_the_file(tmp_path, monkeypatch):
    """Имена вписаны в финал, «Пересобрать» идёт по ручной ветке: файл не
    переписывается (хеш сдвигать нельзя), а в статусе ключей уже нет."""
    monkeypatch.setattr(rt, "wait_recording", lambda *a, **k: None)
    monkeypatch.setattr(rt, "canonize_file", lambda *a, **k: None)
    monkeypatch.delenv("CHAROITE_FORCE_STT", raising=False)
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1", "Собеседник 2"])
    text = (f"# Встреча 2026-09-03_1200\n\n{banner}\n\n"
            "**Анна** [12:00]:\nпривет\n\n**Борис** [12:01]:\nда\n")
    live = _stamp_file(tmp_path / "transcripts", "2026-09-03_1200.md", text)
    (tmp_path / "logs").mkdir()
    live.with_name(live.name + ".live.json").write_text(
        json.dumps({"transcript_sha256": "a" * 64}), encoding="utf-8")
    before = live.read_bytes()

    assert rt.rebuild(live, CFG) == live

    assert live.read_bytes() == before
    assert rt.NAMES_PENDING_PREFIX in live.read_text(encoding="utf-8"), "устаревшая строка плашки остаётся"
    assert transcript.read_names_pending(live.read_text(encoding="utf-8")).pending is False
    data = json.loads(MeetingStatusStore(tmp_path).ready(live, None).read_text(encoding="utf-8"))
    assert "names_pending" not in data and "names_reason" not in data


def test_owned_restamp_drops_the_banner_and_matches_the_sidecar_hash(tmp_path):
    """Ревизия назвала все метки машинного файла: плашки нет, хеш сайдкара
    равен sha256 байтов, которые лежат на диске."""
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1", "Собеседник 2"])
    text = _speech(banner, [("Собеседник 1", "а"), ("Собеседник 2", "б")])
    live = _owned(tmp_path, "2026-09-11_1533.md", text)

    assert nf.restamp_transcript(live, {"Собеседник 1": "Анна", "Собеседник 2": "Борис"}) == 2

    body = live.read_text(encoding="utf-8")
    assert transcript.NAMES_PENDING_PREFIX not in body
    assert "**Анна** [15:33]:" in body and "**Борис** [15:33]:" in body
    digest = hashlib.sha256(live.read_bytes()).hexdigest()
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == digest == live_sidecar.sha(body)


def test_ready_reads_the_renamed_transcript(tmp_path):
    """Шаг графа переименовал файл: ready находит его по минутному штампу
    и пишет признак, хотя исходного пути уже нет."""
    live = tmp_path / "transcripts" / "2026-07-31_141501.md"
    live.parent.mkdir(parents=True)
    titled = live.with_name("2026-07-31_1415_Тема.md")
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1"])
    titled.write_text(
        f"# Встреча 2026-07-31_1415 — Тема\n\n{banner}\n\n**Собеседник 1** [14:15]:\nда\n",
        encoding="utf-8")

    data = json.loads(MeetingStatusStore(tmp_path).ready(live, None).read_text(encoding="utf-8"))

    assert data["transcript_path"] == str(titled.resolve())
    assert data["names_pending"] is True and data["names_reason"] == "silent"
    assert data["state"] == "ready"


def test_partial_rename_keeps_the_banner_with_the_two_remaining_labels(tmp_path):
    """Названа одна метка из трёх: плашка остаётся, в списке две."""
    banner = transcript.names_pending_line(
        rt.NAMES_PENDING_NOTE, ["Собеседник 1", "Собеседник 2", "Собеседник 3"])
    text = _speech(banner, [("Собеседник 1", "а"), ("Собеседник 2", "б"), ("Собеседник 3", "в")])
    live = _owned(tmp_path, "2026-09-11_1533.md", text)

    assert nf.restamp_transcript(live, {"Собеседник 1": "Анна"}) == 1

    body = live.read_text(encoding="utf-8")
    assert "безымянные: Собеседник 2, Собеседник 3" in body
    assert "Собеседник 1" not in body and "**Анна** [15:33]:" in body
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == hashlib.sha256(live.read_bytes()).hexdigest()


def test_hand_edited_restamp_keeps_the_stale_banner_and_the_old_hash(tmp_path):
    """Ручной файл перештамповывает заголовки, но не плашку и не хеш:
    сдвиг хеша сделал бы правку машинным текстом."""
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1", "Собеседник 2"])
    base = _speech(banner, [("Собеседник 1", "а"), ("Собеседник 2", "б")])
    live = _owned(tmp_path, "2026-09-11_1533.md", base)
    live.write_text(base + "правка руками\n", encoding="utf-8")

    assert nf.restamp_transcript(live, {"Собеседник 1": "Анна"}) == 1

    body = live.read_text(encoding="utf-8")
    assert "безымянные: Собеседник 1, Собеседник 2" in body
    assert "**Анна** [15:33]:" in body and "правка руками" in body
    meta = json.loads(live.with_name(live.name + ".live.json").read_text(encoding="utf-8"))
    assert meta["transcript_sha256"] == live_sidecar.sha(base)


def test_reader_ignores_a_label_quoted_in_co_thinking():
    """Заголовок в хвосте ко-мышления — цитата, не говорящий."""
    text = (
        f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n**Анна** [12:00]:\nда\n"
        f"{transcript.NOTES_HEAD}{transcript.NOTES_SUFFIX}\n"
        "**Собеседник 1** [12:05]:\nцитата из черновика\n"
    )
    info = transcript.read_names_pending(text)
    assert info.pending is False and info.labels == () and info.reason is None


def test_reader_treats_an_old_banner_without_a_list_as_every_numbered_label():
    text = (
        f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n"
        "**Собеседник 2** [12:00]:\nа\n\n**Анна** [12:01]:\nб\n\n**Собеседник 1** [12:02]:\nв\n"
    )
    info = transcript.read_names_pending(text)
    assert info.pending is True and info.reason == "silent"
    assert info.labels == ("Собеседник 2", "Собеседник 1")
    rejected = (
        f"# Встреча\n\n{rt.NAMES_REJECTED_NOTE.format(proposed=2)}\n\n"
        "**Собеседник 1** [12:00]:\nда\n"
    )
    info = transcript.read_names_pending(rejected)
    assert info.pending is True and info.reason == "rejected" and info.labels == ("Собеседник 1",)


def test_reader_keeps_a_listless_banner_beside_the_collapsed_mic_note():
    collapsed = rt.MIC_COLLAPSED_NOTE.format(live=4)
    text = f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n{collapsed}\n\n**Анна** [12:00]:\nда\n"
    info = transcript.read_names_pending(text)
    assert info.pending is True and info.labels == () and info.reason == "silent"
    assert transcript.names_banner_for(text) == text
    bare = f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n**Анна** [12:00]:\nда\n"
    assert transcript.read_names_pending(bare).pending is False
    assert transcript.NAMES_PENDING_PREFIX not in transcript.names_banner_for(bare)


def test_reader_does_not_count_the_bare_neutral_label():
    """Плашка без списка: голый «Собеседник» не считается — им же бывает
    слитая метка микрофона, а слитых меток текст не знает. Плашка со
    списком считает его, если он в списке."""
    both = (
        f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n"
        "**Собеседник** [12:00]:\nда\n\n**Собеседник 3** [12:01]:\nнет\n"
    )
    assert transcript.read_names_pending(both).labels == ("Собеседник 3",)
    only = f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n**Собеседник** [12:00]:\nда\n"
    assert transcript.read_names_pending(only).pending is False
    listed = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник"])
    text = f"# Встреча\n\n{listed}\n\n**Собеседник** [12:00]:\nда\n"
    assert transcript.read_names_pending(text).labels == ("Собеседник",)


def test_listed_banner_drops_when_nothing_remains_even_beside_the_collapsed_note():
    """Список уже был: свёрнутый микрофон в него не входил, пустое пересечение
    снимает плашку. Заметка о микрофоне остаётся."""
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1"])
    collapsed = rt.MIC_COLLAPSED_NOTE.format(live=3)
    text = f"# Встреча\n\n{banner}\n\n{collapsed}\n\n**Анна** [12:00]:\nда\n"
    assert transcript.read_names_pending(text).pending is False
    out = transcript.names_banner_for(text)
    assert transcript.NAMES_PENDING_PREFIX not in out
    assert "Разметка микрофона" in out


def test_status_update_lock_applies_the_later_write_on_top_of_the_earlier(tmp_path):
    """Поток A держит _update между чтением и записью, поток B зовёт processing.
    Запись B ложится поверх A и видит её attempts: ничего не потеряно."""
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store_a = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    store_b = MeetingStatusStore(tmp_path, now=lambda: 20.0)
    path = store_a.processing(live, "seed")
    entered = threading.Event()
    release = threading.Event()
    b_done = threading.Event()

    def mutate(current):
        entered.set()
        assert release.wait(5)
        updated = dict(current)
        updated["attempts"] = 7
        updated["state"] = "ready"
        updated["stage"] = "complete"
        return updated

    def run_a():
        store_a._update(live, mutate)

    def run_b():
        store_b.processing(live, "rebuilding_transcript")
        b_done.set()

    thread_a = threading.Thread(target=run_a)
    thread_a.start()
    assert entered.wait(3)
    thread_b = threading.Thread(target=run_b)
    thread_b.start()
    assert not b_done.wait(0.4), "B записал статус, пока A держал замок"
    release.set()
    thread_a.join(3)
    assert b_done.wait(3)
    thread_b.join(3)
    assert not thread_a.is_alive() and not thread_b.is_alive()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "processing" and data["stage"] == "rebuilding_transcript"
    assert data["attempts"] == 7 and data["started_at"] == 10.0 and data["updated_at"] == 20.0


def test_ready_called_from_inside_update_returns(tmp_path):
    """ready() из _update не берёт второй flock: тот же поток иначе ждёт себя."""
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 5.0)
    done = threading.Event()

    def mutate(_current):
        store.ready(live, None)
        done.set()
        return None

    thread = threading.Thread(target=lambda: store._update(live, mutate), daemon=True)
    thread.start()
    assert done.wait(3), "ready() из _update не вернулся"
    thread.join(3)
    assert not thread.is_alive()
    data = json.loads(next(store.directory.glob("*.json")).read_text(encoding="utf-8"))
    assert data["state"] == "ready"


def test_status_lock_file_is_not_a_json_document(tmp_path):
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\nтекст\n")
    store = MeetingStatusStore(tmp_path)
    store.processing(live, "updating_graph")
    jsons = list(store.directory.glob("*.json"))
    others = [path for path in store.directory.iterdir() if path not in jsons]
    assert len(jsons) == 1 and jsons[0].suffix == ".json"
    assert others and all(not path.name.endswith(".json") for path in others)
    assert store.unfinished() == []


def test_refresh_names_creates_nothing_without_a_status(tmp_path):
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path)
    assert store.refresh_names(live) is None
    assert not (tmp_path / "logs" / "meeting-status").exists()


def test_refresh_names_does_not_touch_processing(tmp_path):
    """Плашка есть, но встреча ещё обрабатывается: ключи имён не появляются."""
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1"])
    live = _stamp_file(
        tmp_path / "transcripts", "2026-08-12_153219.md",
        f"# Встреча 2026-08-12_153219\n\n{banner}\n\n**Собеседник 1** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.processing(live, "rebuilding_transcript")
    before = path.read_bytes()
    assert store.refresh_names(live) is None
    assert path.read_bytes() == before
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "processing"


def test_refresh_names_drops_the_keys_when_the_names_are_found(tmp_path):
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1"])
    live = _stamp_file(
        tmp_path / "transcripts", "2026-08-12_153219.md",
        f"# Встреча 2026-08-12_153219\n\n{banner}\n\n**Собеседник 1** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.ready(live, None)
    assert json.loads(path.read_text(encoding="utf-8"))["names_pending"] is True
    live.write_text(
        f"# Встреча 2026-08-12_153219\n\n{banner}\n\n**Анна** [15:32]:\nда\n",
        encoding="utf-8")
    assert store.refresh_names(live) == path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "names_pending" not in data and "names_reason" not in data
    assert data["state"] == "ready" and data["updated_at"] == 10.0


def test_refresh_names_keeps_the_keys_when_the_transcript_cannot_be_read(tmp_path):
    """Сбой чтения не снимает уже записанный признак готовой встречи."""
    banner = transcript.names_pending_line(rt.NAMES_PENDING_NOTE, ["Собеседник 1"])
    live = _stamp_file(
        tmp_path / "transcripts", "2026-08-12_153219.md",
        f"# Встреча 2026-08-12_153219\n\n{banner}\n\n**Собеседник 1** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.ready(live, None)
    live.unlink()
    live.mkdir()
    assert store.refresh_names(live) is None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["names_pending"] is True and data["names_reason"] == "silent"
    assert data["updated_at"] == 10.0


def test_refresh_names_sets_both_fields_on_a_ready_meeting(tmp_path):
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.ready(live, None)
    assert "names_pending" not in json.loads(path.read_text(encoding="utf-8"))
    banner = transcript.names_pending_line(rt.NAMES_REJECTED_NOTE.format(proposed=1), ["Собеседник 2"])
    live.write_text(
        f"# Встреча 2026-08-12_153219\n\n{banner}\n\n**Собеседник 2** [15:32]:\nда\n",
        encoding="utf-8")
    assert store.refresh_names(live) == path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["names_pending"] is True and data["names_reason"] == "rejected"
    assert data["state"] == "ready" and data["updated_at"] == 10.0


def test_names_pending_line_keeps_the_note_and_sorts_the_tail():
    """Пустой набор — note без изменений. Повтор и пробелы схлопываются в
    отсортированный хвост. Пробел в конце note не остаётся перед списком."""
    note = transcript.NAMES_PENDING_NOTE
    assert transcript.names_pending_line(note, []) == note
    assert transcript.names_pending_line(
        note, [" Собеседник 2 ", "Собеседник 1", "Собеседник 2", " "],
    ) == note + " | безымянные: Собеседник 1, Собеседник 2"
    assert transcript.names_pending_line(note + " ", ["Собеседник 1"]) == (
        note + " | безымянные: Собеседник 1")


def test_names_banner_for_rewrites_by_the_line_and_keeps_what_follows():
    """Вход → весь текст. Плашка, пустая строка и заголовок; вплотную;
    последняя строка без перевода; укороченный хвост; те же окончания \\r\\n;
    список совпал с заголовками — байт в байт."""
    note = transcript.NAMES_PENDING_NOTE
    full = f"{note} | безымянные: Собеседник 1, Собеседник 2"
    tail = f"{transcript.NOTES_HEAD}{transcript.NOTES_SUFFIX}\n> цитата\n"
    both_named = (
        f"# Встреча\n\n{full}\n\n"
        "**Анна** [12:00]:\nда\n\n**Борис** [12:01]:\nнет\n"
        + tail
    )
    assert transcript.names_banner_for(both_named) == (
        "# Встреча\n\n"
        "**Анна** [12:00]:\nда\n\n**Борис** [12:01]:\nнет\n"
        + tail
    )
    two_blanks = (
        f"# Встреча\n\n{full}\n\n\n"
        "**Анна** [12:00]:\nда\n"
    )
    assert transcript.names_banner_for(two_blanks) == (
        "# Встреча\n\n\n**Анна** [12:00]:\nда\n"
    )
    adjacent = f"# Встреча\n\n{full}\n**Анна** [12:00]:\nда\n"
    assert transcript.names_banner_for(adjacent) == (
        "# Встреча\n\n**Анна** [12:00]:\nда\n"
    )
    last = f"# Встреча\n\n{note} | безымянные: Собеседник 1"
    assert transcript.names_banner_for(last) == "# Встреча\n\n"
    partial = (
        f"# Встреча\n\n{note} | безымянные: Собеседник 2, Собеседник 3, Собеседник 1\n\n"
        "**Собеседник 2** [12:00]:\nа\n\n**Анна** [12:01]:\nб\n\n"
        "**Собеседник 1** [12:02]:\nв\n"
    )
    assert transcript.names_banner_for(partial) == (
        f"# Встреча\n\n{note} | безымянные: Собеседник 2, Собеседник 1\n\n"
        "**Собеседник 2** [12:00]:\nа\n\n**Анна** [12:01]:\nб\n\n"
        "**Собеседник 1** [12:02]:\nв\n"
    )
    crlf = partial.replace("\n", "\r\n")
    assert transcript.names_banner_for(crlf) == (
        f"# Встреча\r\n\r\n{note} | безымянные: Собеседник 2, Собеседник 1\r\n\r\n"
        "**Собеседник 2** [12:00]:\r\nа\r\n\r\n**Анна** [12:01]:\r\nб\r\n\r\n"
        "**Собеседник 1** [12:02]:\r\nв\r\n"
    )
    same = (
        f"# Встреча\n\n{note} | безымянные: Собеседник 1,Собеседник 2\n\n"
        "**Собеседник 1** [12:00]:\nа\n\n**Собеседник 2** [12:01]:\nб\n"
    )
    assert transcript.names_banner_for(same) == same
    plain = "# Встреча\n\n**Анна** [12:00]:\nда\n"
    assert transcript.names_banner_for(plain) == plain


def _hold_status_lock(directory: pathlib.Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / meeting_processing._STATUS_LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _json_bytes(directory: pathlib.Path) -> dict[str, bytes]:
    if not directory.is_dir():
        return {}
    return {path.name: path.read_bytes() for path in directory.glob("*.json")}


def test_status_lock_times_out_and_then_the_write_lands(tmp_path, monkeypatch):
    """Чужой описатель держит замок, срок 0,2 с: TimeoutError не раньше срока,
    документа нет. Описатель отпущен — запись проходит."""
    monkeypatch.setattr(meeting_processing, "STATUS_LOCK_WAIT_S", 0.2)
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    before = _json_bytes(store.directory)
    fd = _hold_status_lock(store.directory)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError) as caught:
            store.processing(live, "rebuilding_transcript")
        elapsed = time.monotonic() - started
        assert elapsed >= 0.2
        message = str(caught.value)
        assert meeting_processing._STATUS_LOCK_NAME in message
        assert "0.2" in message
        assert _json_bytes(store.directory) == before
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    written = store.processing(live, "rebuilding_transcript")
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8"))["stage"] == "rebuilding_transcript"


def test_the_first_lock_poll_releases_the_holder_and_the_write_lands(tmp_path, monkeypatch):
    """Первая пауза опроса отпускает замок теста: TimeoutError нет, запись есть."""
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    fd = _hold_status_lock(store.directory)
    pauses = {"n": 0}

    def pause(_seconds):
        pauses["n"] += 1
        if pauses["n"] == 1:
            fcntl.flock(fd, fcntl.LOCK_UN)

    monkeypatch.setattr(meeting_processing.time, "sleep", pause)
    try:
        path = store.processing(live, "updating_graph")
    finally:
        os.close(fd)
    assert pauses["n"] >= 1
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["stage"] == "updating_graph"


def test_review_without_a_document_creates_nothing(tmp_path):
    """Без каталога статусов его нет и после вызова. Каталог есть, документа
    нет — файла замка нет."""
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path)
    assert store.review(live, "running", "рано") is None
    assert not store.directory.exists()
    store.directory.mkdir(parents=True)
    assert store.review(live, "running", "ещё рано") is None
    assert list(store.directory.iterdir()) == []


def _stale_running(path: pathlib.Path, transcript_path: str) -> None:
    path.write_text(json.dumps({
        "review": {"note": "висит", "state": "running", "updated_at": 0},
        "state": "ready",
        "transcript_path": transcript_path,
    }, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def test_expire_reviews_fails_the_file_it_read_and_leaves_the_canonical(tmp_path):
    """Сирота running → failed, канонический ok не тронут. Пустой
    transcript_path и два мёртвых пути тоже становятся failed."""
    transcripts = tmp_path / "transcripts"
    live = _stamp_file(transcripts, "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: meeting_processing.REVIEW_STALE + 50)
    canonical = store.ready(live, None)
    assert store.review(live, "ok", "доставлено") == canonical
    canonical_bytes = canonical.read_bytes()
    orphan = store.directory / "orphan-status.json"
    _stale_running(orphan, str(live))
    empty = store.directory / "empty-path.json"
    _stale_running(empty, "")
    missing = str(transcripts / "2026-01-01_000000.md")
    dead_a = store.directory / "dead-a.json"
    dead_b = store.directory / "dead-b.json"
    _stale_running(dead_a, missing)
    _stale_running(dead_b, missing)

    written = store.expire_reviews()

    assert canonical.read_bytes() == canonical_bytes
    assert json.loads(canonical.read_text(encoding="utf-8"))["review"]["state"] == "ok"
    assert set(written) == {orphan, empty, dead_a, dead_b}
    for path in (orphan, empty, dead_a, dead_b):
        review = json.loads(path.read_text(encoding="utf-8"))["review"]
        assert review["state"] == "failed" and "не завершил" in review["note"]


def test_ready_records_the_rejected_reason(tmp_path):
    """ready пишет причину читателя: отказ гвардов остаётся rejected."""
    banner = transcript.names_pending_line(
        rt.NAMES_REJECTED_NOTE.format(proposed=1), ["Собеседник 2"])
    live = _stamp_file(
        tmp_path / "transcripts", "2026-08-12_153219.md",
        f"# Встреча 2026-08-12_153219\n\n{banner}\n\n**Собеседник 2** [15:32]:\nда\n")
    data = json.loads(
        MeetingStatusStore(tmp_path, now=lambda: 10.0).ready(live, None).read_text(encoding="utf-8"))
    assert data["names_pending"] is True and data["names_reason"] == "rejected"


def test_expire_reviews_reads_inside_the_lock(tmp_path, monkeypatch):
    """Документ уже просрочен. Подменённый замок на входе пишет ok:
    проход видит уже ok и возвращает [], состояние ok."""
    transcripts = tmp_path / "transcripts"
    live = _stamp_file(transcripts, "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: meeting_processing.REVIEW_STALE + 50)
    path = store.ready(live, None)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["review"] = {"note": "висит", "state": "running", "updated_at": 0}
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    @contextlib.contextmanager
    def locked_writes_ok(self):
        current = json.loads(path.read_text(encoding="utf-8"))
        current["review"]["state"] = "ok"
        path.write_text(
            json.dumps(current, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        yield

    monkeypatch.setattr(MeetingStatusStore, "_locked", locked_writes_ok)
    assert store.expire_reviews() == []
    assert json.loads(path.read_text(encoding="utf-8"))["review"]["state"] == "ok"


def test_expire_reviews_returns_nothing_when_the_lock_is_held(tmp_path, monkeypatch):
    """Замок держит тест, срок подменён: [], байты документов те же."""
    monkeypatch.setattr(meeting_processing, "STATUS_LOCK_WAIT_S", 0.2)
    transcripts = tmp_path / "transcripts"
    live = _stamp_file(transcripts, "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: meeting_processing.REVIEW_STALE + 50)
    path = store.ready(live, None)
    assert store.review(live, "running", "идёт") == path
    before = _json_bytes(store.directory)
    fd = _hold_status_lock(store.directory)
    started = time.monotonic()
    try:
        assert store.expire_reviews() == []
        assert time.monotonic() - started >= 0.2
        assert _json_bytes(store.directory) == before
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@pytest.mark.parametrize(("write", "state"), [
    (lambda store, live: store.ready(live, None), "ready"),
    (lambda store, live: store.failed(live, "упало"), "error"),
    (lambda store, live: store.no_speech(live), "empty"),
])
def test_meeting_outcome_lands_when_the_lock_is_held(tmp_path, monkeypatch, capsys, write, state):
    """Замок держит тест дольше срока: этап (`processing`) пропускается
    TimeoutError, а исход встречи всё равно ложится поверх `processing`,
    со строкой в stderr. Замок после этого по-прежнему у теста."""
    monkeypatch.setattr(meeting_processing, "STATUS_LOCK_WAIT_S", 0.2)
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.processing(live, "updating_graph")
    fd = _hold_status_lock(store.directory)
    try:
        with pytest.raises(TimeoutError):
            store.processing(live, "rebuilding_transcript")
        assert json.loads(path.read_text(encoding="utf-8"))["stage"] == "updating_graph"
        started = time.monotonic()
        assert write(store, live) == path
        assert time.monotonic() - started >= 0.2
        assert json.loads(path.read_text(encoding="utf-8"))["state"] == state
        assert "без замка" in capsys.readouterr().err
        probe = os.open(store.directory / meeting_processing._STATUS_LOCK_NAME, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_expire_reviews_returns_nothing_when_the_lock_file_fails(tmp_path, monkeypatch):
    """Не только занятый замок: сбой файла замка (права, диск) — тоже [],
    unfinished() идёт дальше."""
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: meeting_processing.REVIEW_STALE + 50)
    path = store.ready(live, None)
    assert store.review(live, "running", "идёт") == path

    @contextlib.contextmanager
    def denied(self):
        raise PermissionError("status.lock: нет прав")
        yield

    monkeypatch.setattr(MeetingStatusStore, "_locked", denied)
    assert store.expire_reviews() == []
    assert store.unfinished() == []
    assert json.loads(path.read_text(encoding="utf-8"))["review"]["state"] == "running"


def test_status_writes_leave_no_open_descriptors(tmp_path):
    """Каждая запись открывает описатель замка и обязана его закрыть: у
    долгоживущего демона утечка кончается EMFILE."""
    live = _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    store.processing(live, "updating_graph")
    before = len(os.listdir("/dev/fd"))
    for _ in range(20):
        store.processing(live, "updating_graph")
        store.ready(live, None)
        store.refresh_names(live)
        store.expire_reviews()
    assert len(os.listdir("/dev/fd")) == before


def _live(tmp_path: pathlib.Path) -> pathlib.Path:
    return _stamp_file(tmp_path / "transcripts", "2026-08-12_153219.md",
                       "# Встреча 2026-08-12_153219\n\n**Анна** [15:32]:\nда\n")


def test_delivered_review_lands_when_the_lock_is_held(tmp_path, monkeypatch, capsys):
    """«ok» ревизии — доставка: ложится и при занятом замке. «running»
    при том же замке пропускается TimeoutError."""
    monkeypatch.setattr(meeting_processing, "STATUS_LOCK_WAIT_S", 0.2)
    live = _live(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.ready(live, None)
    assert store.review(live, "running", "идёт") == path
    fd = _hold_status_lock(store.directory)
    try:
        with pytest.raises(TimeoutError):
            store.review(live, "retrying", "повтор")
        assert store.review(live, "ok", "доставлено") == path
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["state"] == "ready" and data["review"]["state"] == "ok"
        assert "без замка" in capsys.readouterr().err
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_meeting_outcome_lands_when_flock_fails_not_busy(tmp_path, monkeypatch):
    """flock отказал не занятостью (ENOLCK на томе без замков): исход
    ложится без замка, обычная запись получает тот же OSError."""
    live = _live(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.processing(live, "updating_graph")

    def no_locks(_fd, _op):
        raise OSError(77, "No locks available")

    monkeypatch.setattr(meeting_processing.fcntl, "flock", no_locks)
    with pytest.raises(OSError) as caught:
        store.processing(live, "rebuilding_transcript")
    assert caught.value.errno == 77
    assert json.loads(path.read_text(encoding="utf-8"))["stage"] == "updating_graph"
    assert store.failed(live, "упало") == path
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "error"


def test_meeting_outcome_lands_when_the_lock_file_does_not_open(tmp_path, monkeypatch):
    """Файл замка не открывается (права, чужой владелец): исход ложится,
    обычная запись получает PermissionError."""
    live = _live(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.processing(live, "updating_graph")
    real_open = os.open

    def deny_lock(name, *args, **kwargs):
        if pathlib.Path(name).name == meeting_processing._STATUS_LOCK_NAME:
            raise PermissionError(13, "Permission denied", str(name))
        return real_open(name, *args, **kwargs)

    monkeypatch.setattr(meeting_processing.os, "open", deny_lock)
    with pytest.raises(PermissionError):
        store.processing(live, "rebuilding_transcript")
    assert store.no_speech(live) == path
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "empty"


def _race_against_the_outcome(tmp_path, monkeypatch, holder):
    """Поток A держит замок в `_update` (mutate ждёт), исход `ready` ложится
    без замка по сроку, затем A дописывает. Возвращает документ после A."""
    monkeypatch.setattr(meeting_processing, "STATUS_LOCK_WAIT_S", 0.2)
    live = _live(tmp_path)
    store_a = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    store_b = MeetingStatusStore(tmp_path, now=lambda: 20.0)
    path = store_a.processing(live, "updating_graph")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def mutate(current):
        calls.append(current.get("state"))
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return holder(current)

    thread = threading.Thread(target=lambda: store_a._update(live, mutate))
    thread.start()
    try:
        assert entered.wait(3)
        assert store_b.ready(live, None) == path
        assert json.loads(path.read_text(encoding="utf-8"))["state"] == "ready"
    finally:
        release.set()
        thread.join(5)
    assert calls == ["processing", "ready"]
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_stale_stage_under_the_lock_does_not_undo_the_outcome(tmp_path, monkeypatch):
    def stage(current):
        return {**current, "state": "processing", "stage": "rebuilding_transcript"}

    data = _race_against_the_outcome(tmp_path, monkeypatch, stage)
    assert data["state"] == "ready" and data["stage"] == "complete"


def test_a_review_under_the_lock_lands_on_top_of_the_outcome(tmp_path, monkeypatch):
    def review(current):
        return {**current, "review": {"state": "running", "note": "", "updated_at": 1.0}}

    data = _race_against_the_outcome(tmp_path, monkeypatch, review)
    assert data["state"] == "ready" and data["review"]["state"] == "running"


def test_status_update_after_the_outcome_still_reopens_the_meeting(tmp_path):
    """Без гонки гейта нет: пересборка готовой встречи пишет processing."""
    live = _live(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    path = store.ready(live, None)
    assert store.processing(live, "rebuilding_transcript") == path
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "processing"


def test_no_lockless_line_when_the_outcome_did_not_land(tmp_path, monkeypatch, capsys):
    """Строка «записан без замка» — только после записи."""
    monkeypatch.setattr(meeting_processing, "STATUS_LOCK_WAIT_S", 0.2)
    live = _live(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    store.processing(live, "updating_graph")
    capsys.readouterr()

    def broken(_current):
        raise ValueError("mutate упал")

    fd = _hold_status_lock(store.directory)
    try:
        with pytest.raises(ValueError):
            store._update(live, broken, terminal=True)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert "без замка" not in capsys.readouterr().err
