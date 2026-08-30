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
