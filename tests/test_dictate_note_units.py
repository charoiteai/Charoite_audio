"""Диктовка: ссылка на встречу — по ключу графа, момент заметки — из --moment,
имя заметки не затирает соседку (аудит 13.09, зона 5)."""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import dictate_note as dn  # noqa: E402


def test_last_meeting_today_links_the_graph_key_not_the_file_stem(tmp_path, monkeypatch):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    today = f"{dt.datetime.now():%Y-%m-%d}"
    (tdir / f"{today}_1000_Итоги_квартала.md").write_text(
        f"# Встреча {today}_1000 — Итоги квартала\nтело\n", encoding="utf-8")
    monkeypatch.setenv("SUFLER_TRANSCRIPTS_DIR", str(tdir))
    stamp, topic = dn.last_meeting_today()
    assert stamp == f"{today}_1000", "ссылка [[Встречи/<стем с темой>]] висела в пустоте"
    assert topic == "Итоги квартала"
    # день записи из --moment: вчерашняя заметка ищет вчерашнюю встречу, не сегодняшнюю
    (tdir / "2026-01-05_0900_Ретро.md").write_text("# Встреча 2026-01-05_0900 — Ретро\nтело\n", encoding="utf-8")
    assert dn.last_meeting_today("2026-01-05") == ("2026-01-05_0900", "Ретро")
    assert dn.last_meeting_today("2026-01-06") is None


def test_moment_comes_from_the_flag_and_falls_back_to_now(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dictate_note.py", "--text", "--moment", "2026-09-12 18:00"])
    assert dn._moment() == dt.datetime(2026, 9, 12, 18, 0)
    monkeypatch.setattr(sys, "argv", ["dictate_note.py", "--text", "--moment", "вчера"])
    assert abs((dn._moment() - dt.datetime.now()).total_seconds()) < 5
    monkeypatch.setattr(sys, "argv", ["dictate_note.py", "--text"])
    assert abs((dn._moment() - dt.datetime.now()).total_seconds()) < 5


def test_граф_спрашивается_на_вызове_а_не_на_импорте(tmp_path, monkeypatch):
    """Заметка и дневник идут в граф, названный СЕЙЧАС, а не на импорте.

    Снимок на импорте считался раньше фикстур: в шелле владельца (переменная
    графа экспортирована — этим включают демо-граф) процесс держал его путь
    до конца прогона, и `--diary` писал в живой граф, а изоляция окружения
    этого уже не догоняла (круг 11 по коду №327, DS C1).
    """
    import dictate_note
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(tmp_path / "поздний-граф"))
    monkeypatch.delenv("SUFLER_DIARY_DIR", raising=False)
    # `cfg` снят на импорте — на машине с настоящим config.yaml ключ
    # `diary_dir` перекрыл бы fallback, и тест зеленел бы только там, где
    # конфига нет (круг 12 по коду №327, DS C2).
    monkeypatch.setitem(dictate_note.cfg["sufler"], "diary_dir", "")
    assert dictate_note._graph() == tmp_path / "поздний-граф"
    assert dictate_note.diary_dir() == tmp_path / "Дневник"


def test_заметка_ложится_в_папку_заметок_названного_графа(tmp_path, monkeypatch):
    """Путь заметки — `<граф>/Заметки`, и он тоже считается на вызове.

    Мутация `_graph() / "Заметки"` → `_graph() * "Заметки"` пережила прогон
    (штатный мутатор, дельта круга 12): место записи заметки не проверял
    никто. Сама модель здесь не нужна — обработка вспомогательна, и при её
    отказе заметка всё равно обязана лечь в граф.
    """
    import dictate_note
    # `main()` ставит боевую маску (0o077) на весь процесс — вернём как было,
    # иначе следующий тест прав судит чужой umask и падает по нашей причине.
    прежняя = os.umask(0o022)
    os.umask(прежняя)
    monkeypatch.setattr(dictate_note, "harden_umask", lambda: прежняя)
    graph = tmp_path / "граф"
    monkeypatch.setenv("CHAROITE_GRAPH_DIR", str(graph))
    monkeypatch.setattr(dictate_note, "_llm", types.SimpleNamespace(
        complete=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("модели нет"))))
    monkeypatch.setattr(dictate_note, "_record_and_transcribe", lambda *a, **k: "проверить счётчики")
    monkeypatch.setattr(dictate_note.sys, "argv", ["dictate_note.py"])
    dictate_note.main()
    заметки = list((graph / "Заметки").glob("*.md"))
    assert заметки, "заметка не легла в Заметки названного графа"
    assert "проверить счётчики" in заметки[0].read_text(encoding="utf-8")
