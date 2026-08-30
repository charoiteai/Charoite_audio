"""Одна встреча — одна папка в архиве.

Тема встречи уточняется при повторных разборах, а папка называется по теме.
Пока архивация не умела переименовывать, каждое уточнение заводило вторую
папку на ту же встречу: «2026-07-15 09-00 — Бюджет MVP» и рядом
«2026-07-15 09-00 — Бюджет и ресурсы MVP». К 03.08 таких пар
накопилось на 21 встречу из 62 — треть архива.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import dedup_archive as dd  # noqa: E402
from meeting_archive import ARCHIVE_DIR, _folders_for, archive_meeting  # noqa: E402


@pytest.fixture()
def graph(tmp_path: Path) -> Path:
    (tmp_path / ARCHIVE_DIR).mkdir()
    (tmp_path / "Встречи").mkdir()
    return tmp_path


def _folder(graph: Path, name: str, files: dict[str, str] | None = None) -> Path:
    d = graph / ARCHIVE_DIR / name
    d.mkdir()
    for fname, text in (files or {"Стенограмма.md": "текст"}).items():
        (d / fname).write_text(text, encoding="utf-8")
    return d


def _note(graph: Path, stem: str) -> Path:
    p = graph / "Встречи" / f"{stem}.md"
    p.write_text("# встреча", encoding="utf-8")
    return p


def test_folders_for_finds_every_spelling_of_one_meeting(graph):
    _folder(graph, "2026-07-15 09-00 — Бюджет MVP")
    _folder(graph, "2026-07-15 09-00 — Бюджет и ресурсы MVP")
    _folder(graph, "2026-07-15 10-00 — Другая встреча")

    found = _folders_for(graph, "2026-07-15_0900")

    assert len(found) == 2, "обе папки той же встречи"
    assert all("09-00" in f.name for f in found)


def test_time_is_what_identifies_a_meeting_not_the_topic(graph):
    # у пяти встреч в один день совпадает дата — различает их время
    _folder(graph, "2026-07-15 09-00 — Тема")
    assert _folders_for(graph, "2026-07-15_1000") == []


def test_duplicates_are_grouped(graph):
    _folder(graph, "2026-07-15 09-00 — Старое имя")
    _folder(graph, "2026-07-15 09-00 — Новое длинное имя")
    _folder(graph, "2026-07-16 11-00 — Одинокая встреча")
    (graph / ARCHIVE_DIR / "_дубли").mkdir()

    dups = dd.groups(graph / ARCHIVE_DIR)

    assert list(dups) == ["2026-07-15_0900"], "служебные папки и одиночки не трогаем"


def test_folder_matching_the_graph_title_wins(graph):
    old = _folder(graph, "2026-07-15 09-00 — Бюджет MVP")
    new = _folder(graph, "2026-07-15 09-00 — Бюджет и ресурсы MVP")
    _note(graph, "2026-07-15_0900_Бюджет_и_ресурсы_MVP")

    keep = dd.pick([old, new], dd.current_titles(graph), "2026-07-15_0900")

    assert keep == new, "остаётся та папка, которую человек видит в графе"


def test_without_a_graph_title_the_freshest_wins(graph):
    old = _folder(graph, "2026-07-15 09-00 — Первое имя")
    new = _folder(graph, "2026-07-15 09-00 — Второе имя")
    import os
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    keep = dd.pick([old, new], {}, "2026-07-15_0900")

    assert keep == new


def test_unique_files_are_rescued_before_the_folder_goes_away(graph):
    keep = _folder(graph, "2026-07-15 09-00 — Новое", {"Стенограмма.md": "новая"})
    extra = _folder(graph, "2026-07-15 09-00 — Старое",
                    {"Стенограмма.md": "старая", "Голоса и спикеры.md": "уникальный"})

    moved = dd.merge(keep, extra, apply=True)

    assert moved == ["Голоса и спикеры.md"]
    assert (keep / "Голоса и спикеры.md").exists()
    assert (keep / "Стенограмма.md").read_text(encoding="utf-8") == "новая", \
        "одноимённый файл не затирается: в остающейся папке версия свежее"


def test_dry_run_touches_nothing(graph):
    keep = _folder(graph, "2026-07-15 09-00 — Новое")
    extra = _folder(graph, "2026-07-15 09-00 — Старое", {"Разбор.md": "текст"})

    moved = dd.merge(keep, extra, apply=False)

    assert moved == ["Разбор.md"], "показать — да"
    assert not (keep / "Разбор.md").exists(), "сделать — нет"


def test_archive_takes_only_files_of_this_meeting(graph, tmp_path):
    """Минутный штамп — префикс секундного: `archive_meeting` собирал в папку
    файлы обеих встреч одной минуты и перезаписывал «Стенограмма.md» чужой
    (аудит DeepSeek 16.08)."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "2026-08-03_1130_Планёрка.md").write_text("моя стенограмма", encoding="utf-8")
    (tdir / "2026-08-03_1130_minutes.md").write_text("мои минутки", encoding="utf-8")
    (tdir / "2026-08-03_113012.md").write_text("СОСЕДНЯЯ", encoding="utf-8")
    (tdir / "2026-08-03_113012_minutes.md").write_text("СОСЕДНИЕ МИНУТКИ", encoding="utf-8")

    folder = archive_meeting(graph, tdir, "2026-08-03_1130", "Планёрка")

    assert folder is not None
    assert (folder / "Стенограмма.md").read_text(encoding="utf-8") == "моя стенограмма"
    assert (folder / "Минутки.md").read_text(encoding="utf-8") == "мои минутки"
    for f in folder.iterdir():
        assert "СОСЕДН" not in f.read_text(encoding="utf-8", errors="ignore"), f


def test_untitled_meeting_with_seconds_archives_its_own_files(graph, tmp_path):
    """Посекундная встреча без темы («…113012»): ключ файлов — стем
    стенограммы, иначе минутный глоб брал бы файлы соседки, а граница
    штампа — не брала бы свои."""
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "2026-08-03_113012.md").write_text("моя стенограмма", encoding="utf-8")
    (tdir / "2026-08-03_113012_minutes.md").write_text("мои минутки", encoding="utf-8")
    (tdir / "2026-08-03_113045.md").write_text("СОСЕДНЯЯ", encoding="utf-8")
    (tdir / "2026-08-03_1130_Планёрка.md").write_text("ДРУГАЯ", encoding="utf-8")

    folder = archive_meeting(graph, tdir, "2026-08-03_1130", "", files_key="2026-08-03_113012")

    assert folder is not None
    assert (folder / "Стенограмма.md").read_text(encoding="utf-8") == "моя стенограмма"
    assert (folder / "Минутки.md").read_text(encoding="utf-8") == "мои минутки"
    for f in folder.iterdir():
        text = f.read_text(encoding="utf-8", errors="ignore")
        assert "СОСЕДНЯЯ" not in text and "ДРУГАЯ" not in text, f


def test_exclusions_keep_the_full_stamp(tmp_path):
    """Строка про посекундную соседку исключала владельца минуты: регексп
    резал штамп до 15 знаков (аудит 30.08, GLM)."""
    import meeting_archive as ma
    adir = tmp_path / ma.ARCHIVE_DIR
    adir.mkdir()
    (adir / "_исключено.md").write_text("2026-08-03_113045 — тест звука\n2026-08-04_1200 — демо\n", encoding="utf-8")
    ex = ma._excluded(tmp_path)
    assert "2026-08-03_113045" in ex and "2026-08-04_1200" in ex
    assert "2026-08-03_1130" not in ex, "владелец минуты не исключён строкой про соседку"


def test_empty_summary_is_regenerated(tmp_path, monkeypatch):
    """Пустое Саммари.md — след оборванной записи, не готовое саммари (аудит 30.08)."""
    import meeting_archive as ma
    folder = tmp_path / "2026-08-03 11-30 — Тема"
    folder.mkdir()
    (folder / "Минутки.md").write_text("минутки " * 50, encoding="utf-8")
    calls = []
    monkeypatch.setattr(ma, "_history_context", lambda f: calls.append(f) or "")
    monkeypatch.setattr(ma, "decisions_of", lambda f: [])
    (folder / "Саммари.md").write_text("готовое саммари", encoding="utf-8")
    ma._gen_summary(folder)
    assert not calls, "непустое саммари не пересобирается"
    (folder / "Саммари.md").write_text("", encoding="utf-8")
    try:
        ma._gen_summary(folder)
    except Exception:   # noqa: BLE001 — дальше модель, нам важен только вход в генерацию
        pass
    assert calls, "пустое саммари должно уйти на пересборку"
