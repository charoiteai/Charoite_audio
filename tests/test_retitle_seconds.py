"""Конвейер сам называет посекундные стенограммы.

03.08 демон начал писать стенограммы с секундами в штампе —
«2026-08-03_113012.md». Для graph_updater секунды в стеме выглядели как
«файл уже переименовывали»: тему он не давал, и встреча жила в списке
приложения датой вместо темы, пока её не чинили руками через
rename_meeting.py. Здесь — распознавание голого штампа и переименование:
посекундный главный файл получает минутное имя со слагом темы, как назвал
бы его сам конвейер у минутной встречи.
"""

from __future__ import annotations

import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import graph_updater as gu  # noqa: E402


# --- parse_stem: что считается «уже с темой» ---------------------------------

def test_minute_stamp_is_bare():
    assert gu.parse_stem("2026-08-03_1130") == ("2026-08-03_1130", "2026-08-03_1130", False)


def test_seconds_stamp_is_bare_too():
    stamp, bare, titled = gu.parse_stem("2026-08-03_113012")
    assert (stamp, bare, titled) == ("2026-08-03_1130", "2026-08-03_113012", False)


def test_titled_stem_is_titled():
    stamp, bare, titled = gu.parse_stem("2026-08-03_1130_Инцидент_загрузки")
    assert (stamp, titled) == ("2026-08-03_1130", True)


def test_titled_seconds_stem_is_titled():
    stamp, bare, titled = gu.parse_stem("2026-08-03_113012_Инцидент_загрузки")
    assert (stamp, bare, titled) == ("2026-08-03_1130", "2026-08-03_113012", True)


def test_non_stamp_name_stays_untitled():
    # пять цифр после подчёркивания — не штамп; трогать нечего
    assert gu.parse_stem("import_загрузка") == ("import_загрузка", "import_загрузка", False)


# --- retitle: файловые операции ----------------------------------------------

def _meeting(tmp_path: Path, stem: str) -> Path:
    t = tmp_path / f"{stem}.md"
    t.write_text(f"# Встреча {stem}\nречь\n", encoding="utf-8")
    (tmp_path / f"{stem}_minutes.md").write_text("минутки", encoding="utf-8")
    (tmp_path / f"{stem}_hints.md").write_text("подсказки", encoding="utf-8")
    return t


def test_seconds_meeting_gets_minute_name_and_title(tmp_path):
    t = _meeting(tmp_path, "2026-08-03_113012")
    new = gu.retitle(t, "2026-08-03_1130", "2026-08-03_113012", "Инцидент загрузки")
    assert new.name == "2026-08-03_1130_Инцидент_загрузки.md"
    assert not t.exists()
    # производные приведены к тому же минутному виду с темой
    assert (tmp_path / "2026-08-03_1130_Инцидент_загрузки_minutes.md").exists()
    assert (tmp_path / "2026-08-03_1130_Инцидент_загрузки_hints.md").exists()
    # шапка без хвоста «12» после темы
    assert "# Встреча 2026-08-03_1130 — Инцидент загрузки\n" in new.read_text(encoding="utf-8")


def test_retitle_records_seconds_stamp_in_sidecar(tmp_path):
    """После наката темы имя файла минутное, сайдкар переехал под него —
    посекундный штамп встречи остаётся только в ключе `stamp` (№164)."""
    import json

    t = _meeting(tmp_path, "2026-08-03_113012")
    new = gu.retitle(t, "2026-08-03_1130", "2026-08-03_113012", "Инцидент загрузки")
    meta = json.loads((tmp_path / (new.name + ".live.json")).read_text(encoding="utf-8"))
    assert meta["stamp"] == "2026-08-03_113012"


def test_retitle_of_minute_meeting_records_no_stamp(tmp_path):
    """Минутной встрече уточнять нечего — сайдкар ради этого не создаётся."""
    t = _meeting(tmp_path, "2026-08-03_1130")
    new = gu.retitle(t, "2026-08-03_1130", "2026-08-03_1130", "Инцидент загрузки")
    assert not (tmp_path / (new.name + ".live.json")).exists()


def test_retitle_refuses_a_name_whose_sidecar_is_foreign(tmp_path):
    """DS-FW I1 по main 05.09: сирота соседки уже лежит под целевым именем
    сайдкара — migrate её не сдвинул бы, remember слил бы наши ключи в чужой
    JSON. Занятый сайдкар = занятое имя: файл остаётся, тема идёт в шапку."""
    import json

    t = _meeting(tmp_path, "2026-08-03_113012")
    foreign = tmp_path / "2026-08-03_1130_Инцидент_загрузки.md.live.json"
    foreign.write_text(json.dumps({"stamp": "2026-08-03_113045", "names": {"Собеседник 1": "Чужой"}}), encoding="utf-8")
    new = gu.retitle(t, "2026-08-03_1130", "2026-08-03_113012", "Инцидент загрузки")
    assert new == t and t.exists(), "файл остался под своим именем"
    assert "— Инцидент загрузки" in t.read_text(encoding="utf-8")
    assert json.loads(foreign.read_text(encoding="utf-8"))["stamp"] == "2026-08-03_113045", "чужой сайдкар не тронут"


def test_retitle_reunites_with_its_own_sidecar_under_the_target_name(tmp_path):
    """GLM r1 по #494, I2: близнец под целевым именем несёт НАШ stamp — это
    свой сайдкар после прерванного переноса, файл переименовывается."""
    import json

    t = _meeting(tmp_path, "2026-08-03_113012")
    own = tmp_path / "2026-08-03_1130_Инцидент_загрузки.md.live.json"
    own.write_text(json.dumps({"stamp": "2026-08-03_113012"}), encoding="utf-8")
    new = gu.retitle(t, "2026-08-03_1130", "2026-08-03_113012", "Инцидент загрузки")
    assert new.name == "2026-08-03_1130_Инцидент_загрузки.md" and not t.exists()
    assert json.loads(own.read_text(encoding="utf-8"))["stamp"] == "2026-08-03_113012"


def test_minute_meeting_behaviour_unchanged(tmp_path):
    t = _meeting(tmp_path, "2026-08-03_1130")
    new = gu.retitle(t, "2026-08-03_1130", "2026-08-03_1130", "Инцидент загрузки")
    assert new.name == "2026-08-03_1130_Инцидент_загрузки.md"
    assert (tmp_path / "2026-08-03_1130_Инцидент_загрузки_minutes.md").exists()
    assert "# Встреча 2026-08-03_1130 — Инцидент загрузки\n" in new.read_text(encoding="utf-8")


def test_neighbour_meeting_same_minute_is_left_alone(tmp_path):
    """Вторая встреча той же минуты: чужие файлы не подбираем и не затираем."""
    other = _meeting(tmp_path, "2026-08-03_113012")           # соседка
    t = _meeting(tmp_path, "2026-08-03_113055")               # наша
    gu.retitle(t, "2026-08-03_1130", "2026-08-03_113055", "Планёрка")
    assert other.exists()                                     # соседка цела
    assert (tmp_path / "2026-08-03_113012_minutes.md").exists()
    assert (tmp_path / "2026-08-03_1130_Планёрка_minutes.md").exists()


def test_taken_name_is_not_overwritten(tmp_path):
    """Имя занято — файл встречи не затираем, тема идёт только в шапку."""
    taken = tmp_path / "2026-08-03_1130_Планёрка.md"
    taken.write_text("чужая встреча", encoding="utf-8")
    t = _meeting(tmp_path, "2026-08-03_113055")
    new = gu.retitle(t, "2026-08-03_1130", "2026-08-03_113055", "Планёрка")
    assert new == t and t.exists()                            # остались при своём
    assert taken.read_text(encoding="utf-8") == "чужая встреча"
    assert "— Планёрка" in t.read_text(encoding="utf-8")


def test_retitle_leaves_the_neighbour_meeting_of_the_same_minute_alone(tmp_path):
    """Глоб `{bare}_*.md` ловил главный файл соседней встречи той же минуты и
    переименовывал его вместе с производными (хвост аудита 20.08, GLM)."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    import graph_updater as g
    d = tmp_path / "transcripts"
    d.mkdir()
    bare = "2026-08-29_1234"
    live = d / f"{bare}.md"
    live.write_text(f"# Встреча {bare}\n\nтекст\n", encoding="utf-8")
    (d / f"{bare}_minutes.md").write_text("минутки", encoding="utf-8")
    neighbour = d / f"{bare}_План_разбора.md"
    neighbour.write_text(f"# Встреча {bare} — План разбора\n", encoding="utf-8")
    out = g.retitle(live, bare, bare, "Новая тема")
    assert out.name == f"{bare}_Новая_тема.md"
    assert (d / f"{bare}_Новая_тема_minutes.md").exists(), "свои производные переехали"
    assert neighbour.exists(), "чужой главный файл той же минуты не тронут"


def test_retitle_leaves_the_neighbour_titled_exactly_with_an_aux_word(tmp_path):
    """Соседка той же минуты с темой ровно «Разбор»: суффикс совпадает с
    производным, главный файл узнаётся по содержимому (DS по #455)."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    import graph_updater as g
    d = tmp_path / "transcripts"
    d.mkdir()
    bare = "2026-08-29_1234"
    live = d / f"{bare}.md"
    live.write_text(f"# Встреча {bare}\n", encoding="utf-8")
    neighbour = d / f"{bare}_Разбор.md"
    neighbour.write_text(f"# Встреча {bare} — Разбор\n", encoding="utf-8")
    review = d / f"{bare}_разбор.md"
    g.retitle(live, bare, bare, "Тема")
    assert neighbour.exists() or review.exists()
    assert (d / f"{bare}_Тема.md").exists()



def test_retitle_of_a_per_second_meeting_leaves_the_minute_neighbour_alone(tmp_path):
    """Боевой случай: вторая встреча той же минуты живёт с посекундным штампом,
    её bare — «…_123456»; соседка с минутным стемом и её производные не
    трогаются, свои производные едут (GLM по #455)."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    import graph_updater as g
    d = tmp_path / "transcripts"
    d.mkdir()
    minute, bare = "2026-08-29_1234", "2026-08-29_123456"
    neighbour = d / f"{minute}_Первая.md"
    neighbour.write_text(f"# Встреча {minute} — Первая\n", encoding="utf-8")
    (d / f"{minute}_Первая_minutes.md").write_text("минутки первой", encoding="utf-8")
    live = d / f"{bare}.md"
    live.write_text(f"# Встреча {bare}\n\nтекст\n", encoding="utf-8")
    (d / f"{bare}_minutes.md").write_text("минутки второй", encoding="utf-8")
    out = g.retitle(live, bare, bare, "Вторая")
    assert out.name == f"{bare}_Вторая.md" and "— Вторая" in out.read_text(encoding="utf-8")
    assert (d / f"{bare}_Вторая_minutes.md").exists()
    assert neighbour.exists() and (d / f"{minute}_Первая_minutes.md").exists()


def test_theme_ending_with_an_aux_word_stays_a_recognisable_meeting(tmp_path):
    """«Демо live» / «Разбор» как тема давали имя, которое stamp_of считал
    производным файлом: встреча пропадала из списков и теряла замок
    пересборки (luna r3 по #455). Хвост крепится дефисом, одно слово —
    «-встреча»; производные едут вместе с главным и остаются производными."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    import graph_updater as g
    import meeting_stamp
    d = tmp_path / "transcripts"
    d.mkdir()
    for title, tail in (("Демо live", "Демо-live"), ("Разбор", "Разбор-встреча"), ("Ревизия claude", "Ревизия-claude")):
        stamp = f"2026-08-29_12{len(tail):02d}"
        live = d / f"{stamp}.md"
        live.write_text(f"# Встреча {stamp}\n\nтекст\n", encoding="utf-8")
        (d / f"{stamp}_minutes.md").write_text("минутки", encoding="utf-8")
        out = g.retitle(live, stamp, stamp, title)
        assert out.name == f"{stamp}_{tail}.md", out.name
        assert meeting_stamp.stamp_of(out.stem) == stamp, "главный файл узнаётся по имени"
        moved = d / f"{stamp}_{tail}_minutes.md"
        assert moved.exists() and meeting_stamp.stamp_of(moved.stem) is None, "производный остался производным"
    assert g.theme_slug("Отчёт по задачам, итоги") == "Отчёт_по_задачам_итоги"


def test_decompose_is_the_public_name_parser():
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    import meeting_stamp
    assert meeting_stamp.decompose("2026-08-04_1203_Отчет_minutes") == ("2026-08-04_1203", "Отчет_minutes")
    assert meeting_stamp.decompose("2026-08-04_120312") == ("2026-08-04_120312", "")
    assert meeting_stamp.decompose("заметка") is None


def test_belongs_is_the_one_stamp_boundary_rule():
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
    import meeting_stamp
    assert meeting_stamp.belongs("2026-08-03_1130_Тема.md", "2026-08-03_1130")
    assert meeting_stamp.belongs("2026-08-03_1130.json", "2026-08-03_1130")
    assert not meeting_stamp.belongs("2026-08-03_113012.md", "2026-08-03_1130")
    assert not meeting_stamp.belongs("2026-08-03_1130-1_Тема.md", "2026-08-03_1130")


# --- статус после наката темы (№699) ------------------------------------------

def test_status_is_repathed_right_after_the_retitle():
    """Сторож проводки: новый путь уходит в статус сразу после переименования,
    а не с `ready` — иначе приложение до конца разбора видит мёртвый путь."""
    text = (Path(__file__).resolve().parent.parent / "src" / "graph_updater.py").read_text(encoding="utf-8")
    call = text.index("tpath = retitle(tpath, stamp, bare, title)\n")
    assert text[call:].split("\n", 2)[1].strip() == "_repath(tpath)"
    assert text.index("    _open_progress(tpath)\n") < call, "ключ статуса — под исходным именем, до наката темы"


def test_repath_is_silent_without_a_status_and_never_fails_the_graph(monkeypatch, capsys):
    monkeypatch.setattr(gu, "_progress", None)
    gu._repath(Path("y.md"))                        # руками по файлу — статуса нет, молча

    class Broken:
        calls = 0

        def repath(self, transcript, moved_to):
            Broken.calls += 1
            raise OSError("диск")
    monkeypatch.setattr(gu, "_progress", (Broken(), Path("x.md")))
    gu._repath(Path("y.md"))
    out = capsys.readouterr().out
    assert "новый путь стенограммы в статус не записан (OSError: диск)" in out
    assert Broken.calls == 1 and out.count("не записан") == 1, "не таймаут — без повтора (выход 4, Sonnet M2)"


def test_a_failing_track_keeps_the_status_and_says_so(monkeypatch, capsys):
    """Сбой ключа не гасит статус прогона (выход 2 №699, DS M4) — поведением, а не
    порядком строк (выход 3, Sonnet M1)."""
    import meeting_processing

    def broken(self, transcript):
        raise OSError("каталог")
    monkeypatch.setattr(gu, "_progress", None)
    monkeypatch.setattr(meeting_processing.MeetingStatusStore, "track", broken)
    gu._open_progress(Path("x.md"))
    assert gu._progress is not None and gu._progress[1] == Path("x.md")
    assert "ключ статуса не запомнен (OSError: каталог)" in capsys.readouterr().out


def test_no_store_means_no_status_and_no_key_line(monkeypatch, capsys):
    """Стор не открылся — статуса нет; про ключ не говорим (выход 3, DS M3)."""
    import meeting_processing

    def broken(self, root, **kw):
        raise OSError("права")
    monkeypatch.setattr(gu, "_progress", (object(), Path("прошлая.md")))
    monkeypatch.setattr(meeting_processing.MeetingStatusStore, "__init__", broken)
    gu._open_progress(Path("x.md"))
    out = capsys.readouterr().out
    assert gu._progress is None, "прошлая стенограмма не наследуется (выход 5, Sonnet M1 = DS M3)"
    assert "статус недоступен (OSError: права)" in out and "ключ статуса" not in out


def test_repath_tries_again_while_the_status_lock_is_busy(monkeypatch, capsys):
    """У короткой встречи следующей записи до `ready` нет: занятый замок — ещё попытка,
    после последней — строка (выход 3 №699, Haiku I2)."""
    class Busy:
        def __init__(self, fails):
            self.fails, self.calls, self.args = fails, 0, set()

        def repath(self, transcript, moved_to):
            self.calls += 1
            self.args.add((transcript, moved_to))
            if self.calls <= self.fails:
                raise TimeoutError("замок занят")
    store = Busy(gu.REPATH_TRIES - 1)
    monkeypatch.setattr(gu, "_progress", (store, Path("x.md")))
    gu._repath(Path("y.md"))
    out = capsys.readouterr().out
    assert store.calls == gu.REPATH_TRIES and store.args == {(Path("x.md"), Path("y.md"))}
    assert out.count("замок статусов занят, попытка") == gu.REPATH_TRIES - 1 and "не записан" not in out
    store = Busy(gu.REPATH_TRIES)
    monkeypatch.setattr(gu, "_progress", (store, Path("x.md")))
    gu._repath(Path("y.md"))
    out = capsys.readouterr().out
    assert store.calls == gu.REPATH_TRIES and store.args == {(Path("x.md"), Path("y.md"))}
    assert "не записан (TimeoutError: замок занят)" in out
    assert out.count("замок статусов занят, попытка") == gu.REPATH_TRIES - 1
