"""Переименование встречи: новая тема везде, где жила старая.

Тему придумывает модель, и она бывает мимо. Поменять руками — пять мест
(transcripts/, архивная папка со ссылками внутри, копии в Документации,
заголовок заметки графа, статус приложения); пропущенное расходится с
остальными навсегда.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import rename_meeting as rm  # noqa: E402

STAMP = "2026-08-03_1130"


def test_short_stamp_accepts_any_spelling():
    assert rm.short_stamp("2026-08-03_1130") == STAMP
    assert rm.short_stamp("2026-08-03_113012") == STAMP, "посекундный стем — та же встреча"
    assert rm.short_stamp("2026-08-03_1130_Старая_тема") == STAMP


def test_garbage_stamp_is_refused():
    with pytest.raises(SystemExit):
        rm.short_stamp("вчера в полдень")


def test_titled_files_get_the_new_slug():
    assert rm.retitled(f"{STAMP}_Обновление_ОС.md", STAMP, "Инцидент_загрузки") \
        == f"{STAMP}_Инцидент_загрузки.md"
    assert rm.retitled(f"{STAMP}_Обновление_ОС_разбор.md", STAMP, "Инцидент_загрузки") \
        == f"{STAMP}_Инцидент_загрузки_разбор.md"


def test_files_with_seconds_carry_no_title_and_stay():
    """«2026-08-03_113012_hints.md» — штамп посекундной точности, темы в имени
    нет, а по полному стему его находит конвейер: трогать нельзя."""
    assert rm.retitled("2026-08-03_113012_hints.md", STAMP, "Тема") is None
    assert rm.retitled("2026-08-03_113012.md", STAMP, "Тема") is None


def test_sidecar_and_foreign_files_stay():
    assert rm.retitled(f"{STAMP}_Тема.md.live.json", STAMP, "Другая") is None
    assert rm.retitled("2026-08-02_1030_Тема.md", STAMP, "Другая") is None


def test_already_renamed_file_is_not_touched():
    assert rm.retitled(f"{STAMP}_Инцидент_загрузки.md", STAMP, "Инцидент_загрузки") is None


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """Встреча во всех пяти местах — как её оставляет конвейер."""
    graph = tmp_path / "graph"
    tdir = tmp_path / "transcripts"
    old_folder = "2026-08-03 11-30 — Обновление ОС"

    (tdir).mkdir()
    (tdir / f"{STAMP}_Обновление_ОС.md").write_text("стенограмма", encoding="utf-8")
    (tdir / f"{STAMP}_Обновление_ОС_разбор.md").write_text("разбор", encoding="utf-8")
    (tdir / "2026-08-03_113012_hints.md").write_text("подсказки", encoding="utf-8")

    arch = graph / rm.ARCHIVE_DIR / old_folder
    arch.mkdir(parents=True)
    (arch / "Саммари.md").write_text(
        f"# Саммари — {old_folder}\n\nПодробнее: [[Встречи-архив/{old_folder}/Минутки|Минутки]]\n",
        encoding="utf-8")

    docs = graph / "Документация" / "Стенограммы встреч"
    docs.mkdir(parents=True)
    (docs / f"{STAMP}_Обновление_ОС_разбор.md").write_text("копия", encoding="utf-8")

    (graph / "Встречи").mkdir()
    (graph / "Встречи" / f"{STAMP}.md").write_text(
        "---\nтип: встреча\naliases: [\"Планёрка 03.08\"]\n---\n"
        f"# Встреча {STAMP} — Обновление ОС\n\nтело\n", encoding="utf-8")

    status = tmp_path / "logs" / "meeting-status"
    status.mkdir(parents=True)
    (status / "2026-08-03_113012.json").write_text(json.dumps(
        {"meeting_id": "2026-08-03_113012",
         "transcript_path": str(tdir / f"{STAMP}_Обновление_ОС.md")}), encoding="utf-8")

    monkeypatch.setattr(rm, "ROOT", tmp_path)
    return graph, tdir


def test_plan_alone_touches_nothing(world):
    graph, tdir = world
    pretty, slug = rm.pretty_and_slug("Инцидент загрузки")
    rm.plan(graph, tdir, STAMP, pretty, slug)

    assert (tdir / f"{STAMP}_Обновление_ОС.md").exists(), "план — это только план"


def test_apply_renames_all_five_places(world):
    graph, tdir = world
    pretty, slug = rm.pretty_and_slug("Инцидент загрузки")
    rm.apply(rm.plan(graph, tdir, STAMP, pretty, slug), graph, STAMP, pretty)

    # transcripts: тема заменена, посекундный файл нетронут
    assert (tdir / f"{STAMP}_Инцидент_загрузки.md").exists()
    assert (tdir / f"{STAMP}_Инцидент_загрузки_разбор.md").exists()
    assert (tdir / "2026-08-03_113012_hints.md").exists()

    # архивная папка и ссылки внутри неё
    new_folder = graph / rm.ARCHIVE_DIR / "2026-08-03 11-30 — Инцидент загрузки"
    assert new_folder.is_dir()
    summary = (new_folder / "Саммари.md").read_text(encoding="utf-8")
    assert "Инцидент загрузки" in summary
    assert "Обновление ОС" not in summary, "ссылки на старое имя папки — битые"

    # копия в Документации
    assert (graph / "Документация" / "Стенограммы встреч"
            / f"{STAMP}_Инцидент_загрузки_разбор.md").exists()

    # заметка графа: заголовок новый, старая тема — в aliases
    note = (graph / "Встречи" / f"{STAMP}.md").read_text(encoding="utf-8")
    assert f"# Встреча {STAMP} — Инцидент загрузки" in note
    assert "Планёрка 03.08" in note, "старые aliases не теряются"
    assert '"Инцидент загрузки"' in note, "новая тема добавлена в aliases"

    # статус приложения смотрит на новый путь
    data = json.loads((Path(rm.ROOT) / "logs" / "meeting-status"
                       / "2026-08-03_113012.json").read_text(encoding="utf-8"))
    assert data["transcript_path"].endswith(f"{STAMP}_Инцидент_загрузки.md")


def test_meeting_without_graph_rename_still_works(world):
    """Встреча могла не доехать до графа: переименовать файлы всё равно можно."""
    graph, tdir = world
    (graph / "Встречи" / f"{STAMP}.md").unlink()
    pretty, slug = rm.pretty_and_slug("Инцидент загрузки")

    rm.apply(rm.plan(graph, tdir, STAMP, pretty, slug), graph, STAMP, pretty)

    assert (tdir / f"{STAMP}_Инцидент_загрузки.md").exists()


def test_title_is_sanitized_for_the_filesystem():
    # тему вводят как угодно — в имена файлов и папок она обязана влезть
    pretty, slug = rm.pretty_and_slug('Инцидент: загрузки/ODS?')
    assert "/" not in pretty and ":" not in pretty
    assert ":" not in slug and " " not in slug
