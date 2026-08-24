"""Переименование встречи: новая тема везде, где жила старая.

Тему придумывает модель, и она бывает мимо. Поменять руками — пять мест
(transcripts/, архивная папка со ссылками внутри, копии в Документации,
заголовок заметки графа, статус приложения); пропущенное расходится с
остальными навсегда.
"""

from __future__ import annotations

import json
import pathlib
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
    # Секунды сохраняются: ими выбирается вторая встреча той же минуты
    # (карточка №39); ключ графа дальше решает resolve_key.
    assert rm.short_stamp("2026-08-03_113012") == "2026-08-03_113012"
    assert rm.short_stamp("2026-08-03_1130_Старая_тема") == STAMP


def test_resolve_key_owner_and_second_meeting(tmp_path):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / "2026-08-03_113010.md").write_text("# a", encoding="utf-8")
    (tdir / "2026-08-03_113012.md").write_text("# b", encoding="utf-8")
    assert rm.resolve_key(tdir, "2026-08-03_113010") == STAMP          # владелец минуты
    assert rm.resolve_key(tdir, "2026-08-03_113012") == "2026-08-03_113012"
    assert rm.resolve_key(tdir, STAMP) == STAMP                        # минута = владелец
    # Соседку забыли: по каталогу вторая встреча стала бы владельцем минуты,
    # но заметка под посекундным ключом уже есть — граф весит больше.
    (tdir / "2026-08-03_113010.md").unlink()
    assert rm.resolve_key(tdir, "2026-08-03_113012") == STAMP
    graph = tmp_path / "graph"
    (graph / "Встречи").mkdir(parents=True)
    (graph / "Встречи" / "2026-08-03_113012.md").write_text("# b", encoding="utf-8")
    assert rm.resolve_key(tdir, "2026-08-03_113012", graph) == "2026-08-03_113012"


def test_retitled_handles_seconds_key_derivatives():
    key = "2026-08-03_113012"
    assert rm.retitled("2026-08-03_113012.md", key, "Тема") == f"{key}_Тема.md"
    assert rm.retitled("2026-08-03_113012_hints.md", key, "Тема") == f"{key}_Тема_hints.md"
    assert rm.retitled("2026-08-03_113012_Старая_minutes.md", key, "Тема") == f"{key}_Тема_minutes.md"


def test_garbage_stamp_is_refused():
    with pytest.raises(SystemExit):
        rm.short_stamp("вчера в полдень")


def test_titled_files_get_the_new_slug():
    assert rm.retitled(f"{STAMP}_Обновление_ОС.md", STAMP, "Инцидент_загрузки") \
        == f"{STAMP}_Инцидент_загрузки.md"
    assert rm.retitled(f"{STAMP}_Обновление_ОС_разбор.md", STAMP, "Инцидент_загрузки") \
        == f"{STAMP}_Инцидент_загрузки_разбор.md"


def test_derived_files_with_seconds_follow_the_title():
    """«…113012_hints.md» — производный файл посекундной встречи. После
    наката темы главный файл — «{штамп}_Тема», и всё, что ищет файлы встречи
    по его стему (архив, облачный контекст, повторные прогоны), посекундные
    производные больше не видит: переименовываем их так же, как сам
    конвейер (`graph_updater.retitle`). Незнакомый хвост не трогаем."""
    assert rm.retitled("2026-08-03_113012_hints.md", STAMP, "Тема") == f"{STAMP}_Тема_hints.md"
    assert rm.retitled("2026-08-03_113012_minutes.md", STAMP, "Тема") == f"{STAMP}_Тема_minutes.md"
    assert rm.retitled("2026-08-03_113012_что-то.md", STAMP, "Тема") is None


def test_bare_main_transcript_finally_gets_its_title():
    """Главный файл без темы — короткий или посекундный.

    Конвейер посекундный стем так и не переименовывал: секунды в стеме
    выглядели для него как «файл уже с темой», и в списке встреч такая
    встреча показывалась датой вместо темы.
    """
    assert rm.retitled(f"{STAMP}.md", STAMP, "Тема") == f"{STAMP}_Тема.md"
    assert rm.retitled("2026-08-03_113012.md", STAMP, "Тема") == f"{STAMP}_Тема.md"


def test_two_mains_do_not_collide_on_one_name(tmp_path):
    # короткий и посекундный главные разом: второй не двигается, файл встречи
    # затирать переименованием нельзя ни при каком раскладе
    (tmp_path / f"{STAMP}.md").write_text("а", encoding="utf-8")
    (tmp_path / "2026-08-03_113012.md").write_text("б", encoding="utf-8")
    p = rm.plan(tmp_path / "нет-графа", tmp_path, STAMP, "Тема", "Тема")
    assert len(p["moves"]) == 1


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

    # transcripts: тема заменена, посекундный производный файл получил её же
    assert (tdir / f"{STAMP}_Инцидент_загрузки.md").exists()
    assert (tdir / f"{STAMP}_Инцидент_загрузки_разбор.md").exists()
    assert (tdir / f"{STAMP}_Инцидент_загрузки_hints.md").exists()
    assert not (tdir / "2026-08-03_113012_hints.md").exists()

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


def test_apply_rebuilds_portable_manifest(world):
    """Телефоны читают тему из meeting.meta.json: после переименования
    манифест обязан говорить новую тему, а не прошлогоднюю."""
    graph, tdir = world
    old = graph / rm.ARCHIVE_DIR / "2026-08-03 11-30 — Обновление ОС"
    (old / "meeting.meta.json").write_text(json.dumps(
        {"schema_version": 1, "meeting_id": STAMP, "title": "Обновление ОС"},
        ensure_ascii=False), encoding="utf-8")
    pretty, slug = rm.pretty_and_slug("Инцидент загрузки")

    rm.apply(rm.plan(graph, tdir, STAMP, pretty, slug), graph, STAMP, pretty)

    new_folder = graph / rm.ARCHIVE_DIR / "2026-08-03 11-30 — Инцидент загрузки"
    saved = json.loads(
        (new_folder / "meeting.meta.json").read_text(encoding="utf-8"))
    assert saved["title"] == "Инцидент загрузки"
    assert saved["meeting_id"] == STAMP


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


def test_resolve_graph_honours_sufler_graph_dir(tmp_path, monkeypatch):
    """Тестовое окружение не должно дотягиваться до рабочего графа.

    Аудит 04.08: скрипт читал граф только из config.yaml, и прогон с
    SUFLER_GRAPH_DIR переименовывал файлы transcripts/, а архивную папку и
    заметку молча искал в рабочем графе — «готово» при сделанной половине.
    """
    cfg = {"sufler": {"graph_dir": "/tmp/рабочий-граф"}}
    for name in ("CHAROITE_GRAPH_DIR", "SUFLER_GRAPH_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUFLER_GRAPH_DIR", str(tmp_path))
    assert rm.resolve_graph(cfg) == tmp_path
    monkeypatch.delenv("SUFLER_GRAPH_DIR")
    assert rm.resolve_graph(cfg) == pathlib.Path("/tmp/рабочий-граф")
