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


def test_review_word_inside_the_title_does_not_hide_the_transcript(tmp_path):
    """Тема «План разбора» содержит «_разбор» подстрокой, но не хвостом:
    проверка `in` вычёркивала главный файл, статус получал несуществующий
    путь, встреча помечалась ошибкой (аудит DeepSeek 16.08)."""
    live = _transcript(tmp_path)
    live.unlink()
    renamed = live.with_name("2026-07-31_1415_План_разбора.md")
    renamed.write_text("стенограмма", encoding="utf-8")

    assert find_final_transcript(live) == renamed.resolve()


def test_note_is_found_in_project_graph(tmp_path, monkeypatch):
    for name in ("CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR"):
        monkeypatch.delenv(name, raising=False)
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
    for name in ("CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR"):
        monkeypatch.delenv(name, raising=False)
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


def test_main_transcript_whose_title_ends_with_an_aux_word_is_still_found(tmp_path):
    """Тема встречи «Общий разбор» кончается на «разбор» — по одному имени файл
    выглядит производным; главный узнаётся по содержимому (хвост аудита 20.08)."""
    live = _transcript(tmp_path)
    live.unlink()
    main = live.with_name("2026-07-31_141501_Общий_разбор.md")
    main.write_text("# Встреча 2026-07-31_141501 — Общий разбор\n\nтекст\n", encoding="utf-8")
    review = live.with_name("2026-07-31_141501_Общий_разбор_разбор.md")
    review.write_text("Граф дообогащён. Ниже — ревизия текстом.\n", encoding="utf-8")
    assert find_final_transcript(live) == main.resolve()


def test_live_copy_is_never_taken_for_the_main_transcript(tmp_path):
    """`_live.md` — дословная копия главного файла с тем же началом: спасать
    её по содержимому нельзя, даже если она моложе (DS по #455)."""
    import os
    live = _transcript(tmp_path)
    live.unlink()
    main = live.with_name("2026-07-31_141501_Тема.md")
    main.write_text("# Встреча 2026-07-31_141501 — Тема\n\nтекст\n", encoding="utf-8")
    copy = live.with_name("2026-07-31_141501_Тема_live.md")
    copy.write_text("# Встреча 2026-07-31_141501 — Тема\n\nчерновик\n", encoding="utf-8")
    os.utime(copy, (os.stat(copy).st_atime, os.stat(copy).st_mtime + 100))
    assert find_final_transcript(live) == main.resolve()


def test_main_transcript_whose_title_ends_with_live_is_still_found(tmp_path):
    """Тема «Демо live» даёт файл `…_Демо_live.md` — это главный, а не копия:
    без другого главного рядом его судят по содержимому (DS r2 по #455)."""
    live = _transcript(tmp_path)
    live.unlink()
    main = live.with_name("2026-07-31_141501_Демо_live.md")
    main.write_text("# Встреча 2026-07-31_141501 — Демо live\n\nтекст\n", encoding="utf-8")
    live.with_name("2026-07-31_141501_minutes.md").write_text("минутки", encoding="utf-8")
    assert find_final_transcript(live) == main.resolve()
    # копии — голого штампа и самого главного — с той же шапкой и моложе:
    # узнаются по имени источника, mtime не решает (DS r3)
    import os
    for name in ("2026-07-31_141501_live.md", "2026-07-31_141501_Демо_live_live.md"):
        copy = live.with_name(name)
        copy.write_text(main.read_text(encoding="utf-8"), encoding="utf-8")
        os.utime(copy, (os.stat(copy).st_atime, os.stat(main).st_mtime + 100))
    assert find_final_transcript(live) == main.resolve()
    # черновик без заголовка главного файла — по-прежнему не кандидат
    for name in ("2026-07-31_141501_live.md", "2026-07-31_141501_Демо_live_live.md"):
        live.with_name(name).unlink()
    main.write_text("черновик без шапки\n", encoding="utf-8")
    assert find_final_transcript(live) == live.resolve()


def test_retitled_final_under_minute_stamp_beats_the_live_copy(tmp_path):
    """Запись с секундами зовут по минуте: финал — «1415_Тема» при исходной
    «141501». Спасение живой копии на посекундном шаге не должно закрывать
    настоящий финал минутного шага: статус цеплялся за «_live», финальный
    гейт не видел заметку — ГОТОВАЯ встреча падала в error (первая живая
    встреча 31.08)."""
    import os
    live = _transcript(tmp_path)
    live.unlink()
    final = live.with_name("2026-07-31_1415_Тема.md")
    final.write_text("# Встреча 2026-07-31_1415 — Тема\n\nтекст\n", encoding="utf-8")
    copy = live.with_name("2026-07-31_141501_live.md")
    copy.write_text("# Встреча 2026-07-31_141501\n\nчерновик\n", encoding="utf-8")
    os.utime(copy, (os.stat(copy).st_atime, os.stat(final).st_mtime + 100))
    live.with_name("2026-07-31_1415_Тема_minutes.md").write_text("минутки", encoding="utf-8")
    assert find_final_transcript(live) == final.resolve()
    # штатный стейт после ПОЛНОГО retitle: копия тоже минутно названа и
    # свежее финала — финал всё равно выигрывает (GLM Minor-2, круг-1 #460)
    mcopy = live.with_name("2026-07-31_1415_Тема_live.md")
    mcopy.write_text(final.read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(mcopy, (os.stat(mcopy).st_atime, os.stat(final).st_mtime + 200))
    assert find_final_transcript(live) == final.resolve()


def test_seconds_named_neighbour_is_invisible_to_the_minute_glob(tmp_path):
    """Посекундная соседка («141505_Тема») в минутный глоб «1415_*.md» не
    попадает по построению — между штампом и «_» стоят её секунды; №39
    держится самим глобом, спасается своя живая копия."""
    live = _transcript(tmp_path)
    live.unlink()
    neighbour = live.with_name("2026-07-31_141505_Тема.md")
    neighbour.write_text("# Встреча 2026-07-31_141505 — Тема\n\nтекст\n", encoding="utf-8")
    copy = live.with_name("2026-07-31_141501_live.md")
    copy.write_text("# Встреча 2026-07-31_141501\n\nчерновик\n", encoding="utf-8")
    assert find_final_transcript(live) == copy.resolve()


def test_minute_named_owner_beats_the_orphaned_live_copy_by_recorded_choice(tmp_path):
    """ЗАПИСАННЫЙ ВЫБОР (круг-1 #460, DS Important-2): минутного главного
    «1415_Тема» по каталогу не отличить от чужого владельца минуты — оба
    мира выглядят одинаково. Выбран минутный финал: это полевой кейс каждой
    обычной встречи (владелец минуты — норма, 31.08), а конфликт «две
    встречи в минуту, у нашей остался только `_live`» — экзотика, в которой
    минута отдаётся владельцу; смешение статусов ограничено claimed/None-
    механикой стора (#456)."""
    live = _transcript(tmp_path)
    live.unlink()
    owner = live.with_name("2026-07-31_1415_Тема.md")
    owner.write_text("# Встреча 2026-07-31_1415 — Тема\n\nтекст\n", encoding="utf-8")
    copy = live.with_name("2026-07-31_141501_live.md")
    copy.write_text("# Встреча 2026-07-31_141501\n\nчерновик\n", encoding="utf-8")
    assert find_final_transcript(live) == owner.resolve()


def test_status_key_is_the_stamp_and_survives_retitle(tmp_path):
    """Ключ статуса — штамп исходного файла, записанный в сам статус: падение
    после переименования писало «error» под старым стемом, повтор шёл под
    новым, старый json оставался «error» навсегда — и каждая тихая итерация
    заново пересобирала встречу из записей (аудит 30.08, GLM Critical 2)."""
    live = _transcript(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    first = store.processing(live, "updating_graph")
    assert first.name == "2026-07-31_141501.json"
    titled = live.with_name("2026-07-31_1415_План_релиза.md")
    live.rename(titled)
    assert store.failed(live, "модель не дала разбор") == first     # старым путём, как rebuild
    later = MeetingStatusStore(tmp_path, now=lambda: 20.0)           # другой процесс, новым путём
    assert later.ready(titled, None, False) == first, "одна встреча — один файл статуса"
    assert json.loads(first.read_text(encoding="utf-8"))["key"] == "2026-07-31_141501"
    assert later.unfinished() == [], "после успешного повтора призрака нет"
    # файл прежней версии, названный по стему, без поля key — не перевешивает свежий «ready»
    ghost = tmp_path / "logs" / "meeting-status" / "2026-07-31_1415_План_релиза.json"
    ghost.write_text(json.dumps({"meeting_id": "2026-07-31_1415_План_релиза", "state": "error",
                                 "updated_at": 5.0, "attempts": 1,
                                 "transcript_path": str(titled.resolve())}), encoding="utf-8")
    assert later.unfinished() == []
    # не-штамп (импорт с чужим именем) — стем, как раньше
    odd = tmp_path / "transcripts" / "заметка.md"
    odd.write_text("# Встреча\n", encoding="utf-8")
    assert store.processing(odd, "x").name == "заметка.json"


def test_dead_neighbour_status_does_not_hijack_the_minute(tmp_path):
    """Вторая встреча той же минуты удалена с диска, её «error» свежее: раньше
    её мёртвый путь резолвился в файл владельца минуты и ронял ЕГО повтор
    (DS r1 по #456). Живой путь перевешивает мёртвый."""
    live = _transcript(tmp_path)
    titled = live.with_name("2026-07-31_1415_Тема.md")
    live.rename(titled)
    d = tmp_path / "logs" / "meeting-status"
    d.mkdir(parents=True)
    (d / "2026-07-31_141501.json").write_text(json.dumps({
        "meeting_id": "2026-07-31_141501", "key": "2026-07-31_141501", "state": "error",
        "updated_at": 100.0, "attempts": 1, "transcript_path": str(titled.resolve())}), encoding="utf-8")
    (d / "2026-07-31_141512.json").write_text(json.dumps({
        "meeting_id": "2026-07-31_141512", "key": "2026-07-31_141512", "state": "error",
        "updated_at": 200.0, "attempts": 1,
        "transcript_path": str((tmp_path / "transcripts" / "2026-07-31_141512.md").resolve())}), encoding="utf-8")
    pending = MeetingStatusStore(tmp_path, now=lambda: 1000.0).unfinished()
    assert [p["key"] for p in pending] == ["2026-07-31_141501"]
    assert pending[0]["transcript_path"] == str(titled.resolve())


def test_crash_after_retitle_is_still_retried_with_the_current_path(tmp_path):
    """Единственный статус — «processing» со старым (уже мёртвым) путём:
    падение между retitle и следующей записью. Повтор находит текущий файл
    и получает его путь, а не мёртвый (DS r1 по #456)."""
    live = _transcript(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    store.processing(live, "updating_graph")
    titled = live.with_name("2026-07-31_1415_Тема.md")
    live.rename(titled)
    pending = MeetingStatusStore(tmp_path, now=lambda: 10.0 + 2 * 3600).unfinished()
    assert len(pending) == 1 and pending[0]["transcript_path"] == str(titled.resolve())


def test_legacy_main_named_live_beats_its_own_newer_copy(tmp_path):
    """Прежние версии могли назвать главный «<штамп>_live.md» (тема «live»);
    его копия «<штамп>_live_live.md» новее — но копия узнаётся по живому
    источнику, а не по mtime (luna по аудиту 30.08)."""
    import os
    live = _transcript(tmp_path)
    live.unlink()
    main = live.with_name("2026-07-31_141501_live.md")
    main.write_text("# Встреча 2026-07-31_141501 — live\n\nтекст\n", encoding="utf-8")
    copy = live.with_name("2026-07-31_141501_live_live.md")
    copy.write_text(main.read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(copy, (os.stat(copy).st_atime, os.stat(main).st_mtime + 100))
    assert find_final_transcript(live) == main.resolve()


def test_suffix_lists_have_one_source():
    import meeting_processing as mp
    import meeting_stamp
    sys.path.insert(0, str(SRC.parent / "scripts"))
    import rename_meeting as rm
    assert mp._AUX_SUFFIXES is meeting_stamp.AUX_SUFFIXES and rm.SUFFIXES is meeting_stamp.AUX_SUFFIXES
    assert "_debrief" in rm.SUFFIXES


def test_meeting_id_stays_the_first_one_for_the_meeting(tmp_path):
    """Приложение сверяет итог повтора по точному meeting_id: после retitle
    новый стем ломал бы сверку (luna r1 по #456). Тема карточки — из пути."""
    live = _transcript(tmp_path)
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    store.processing(live, "updating_graph")
    titled = live.with_name("2026-07-31_1415_Тема.md")
    live.rename(titled)
    path = MeetingStatusStore(tmp_path, now=lambda: 20.0).ready(titled, None, False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["meeting_id"] == "2026-07-31_141501" and data["transcript_path"] == str(titled.resolve())


def test_corrupt_status_does_not_abort_the_retry_scan(tmp_path):
    live = _transcript(tmp_path)
    d = tmp_path / "logs" / "meeting-status"
    d.mkdir(parents=True)
    (d / "bad.json").write_text(json.dumps({"meeting_id": "x", "state": "error", "updated_at": "bad",
                                            "transcript_path": str(live.resolve())}), encoding="utf-8")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    store.failed(live, "упало")
    assert len(MeetingStatusStore(tmp_path, now=lambda: 20.0).unfinished()) == 1


def test_live_copy_of_a_titled_main_is_a_copy_only_while_its_source_lives(tmp_path):
    """Средний ярус: «X_live» при живом X — копия; «<штамп>_live» без голого
    файла — главный прежних версий (GLM r1: множество источников должно
    включать все файлы встречи, не только live-копии)."""
    live = _transcript(tmp_path)
    live.unlink()
    main = live.with_name("2026-07-31_141501_Тема.md")
    main.write_text("# Встреча 2026-07-31_141501 — Тема\n\nтекст\n", encoding="utf-8")
    copy = live.with_name("2026-07-31_141501_Тема_live.md")
    copy.write_text(main.read_text(encoding="utf-8"), encoding="utf-8")
    assert find_final_transcript(live) == main.resolve()
    main.unlink()   # источник исчез — копия становится единственным кандидатом
    assert find_final_transcript(live) == copy.resolve()


def test_two_dead_paths_do_not_let_freshness_pick_the_key(tmp_path):
    """Владелец после retitle (мёртвый голый путь) и удалённая соседка (мёртвый
    путь, свежее): свежесть — не признак владения; в неоднозначности ключ
    берётся детерминированно от текущего файла (GLM r2 по #456)."""
    live = _transcript(tmp_path)
    titled = live.with_name("2026-07-31_1415_Тема.md")
    live.rename(titled)
    d = tmp_path / "logs" / "meeting-status"
    d.mkdir(parents=True)
    (d / "2026-07-31_141501.json").write_text(json.dumps({
        "meeting_id": "2026-07-31_141501", "key": "2026-07-31_141501", "state": "processing",
        "updated_at": 10.0, "transcript_path": str(live.resolve())}), encoding="utf-8")
    (d / "2026-07-31_141512.json").write_text(json.dumps({
        "meeting_id": "2026-07-31_141512", "key": "2026-07-31_141512", "state": "error",
        "updated_at": 200.0, "attempts": 1,
        "transcript_path": str((tmp_path / "transcripts" / "2026-07-31_141512.md").resolve())}), encoding="utf-8")
    path = MeetingStatusStore(tmp_path, now=lambda: 300.0).ready(titled, None, False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["key"] == "2026-07-31_1415" and data["meeting_id"] != "2026-07-31_141512"


def test_busy_ignores_a_corrupt_record(tmp_path):
    d = tmp_path / "logs" / "meeting-status"
    d.mkdir(parents=True)
    (d / "bad.json").write_text(json.dumps({"state": "processing", "updated_at": "bad", "stage": "x"}), encoding="utf-8")
    assert MeetingStatusStore(tmp_path, now=lambda: 10.0).busy() == []


def test_infinite_numbers_in_a_status_are_treated_as_corrupt(tmp_path):
    """json.loads принимает Infinity/NaN: «processing» с updated_at=Infinity
    был бы вечно свежим, attempts=Infinity ронял int() (luna r3 по #456)."""
    live = _transcript(tmp_path)
    d = tmp_path / "logs" / "meeting-status"
    d.mkdir(parents=True)
    (d / "inf.json").write_text('{"meeting_id": "x", "state": "processing", "updated_at": Infinity, '
                                '"attempts": Infinity, "stage": "y", "transcript_path": "%s"}' % live.resolve(),
                                encoding="utf-8")
    store = MeetingStatusStore(tmp_path, now=lambda: 10.0)
    assert store.busy() == []
    assert store.unfinished() == []
