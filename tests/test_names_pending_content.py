"""Признак «имена не определены» считается по тексту стенограммы (№501).

Плашка в шапке несёт список безымянных меток. Читатель — чистая функция:
потеря есть, только пока эти метки ещё стоят в заголовках речи. Статус
пишет то же самое под замком, без флага, пронесённого через конвейер.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import threading

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

import live_sidecar  # noqa: E402
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
    assert rt.names_pending(live) is False
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
    both = (
        f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n"
        "**Собеседник** [12:00]:\nда\n\n**Собеседник 3** [12:01]:\nнет\n"
    )
    assert transcript.read_names_pending(both).labels == ("Собеседник 3",)
    only = f"# Встреча\n\n{rt.NAMES_PENDING_NOTE}\n\n**Собеседник** [12:00]:\nда\n"
    assert transcript.read_names_pending(only).pending is False


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
