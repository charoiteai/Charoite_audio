"""№362 (часть А): снятие UF_HIDDEN со всего графа — один раз на массовый обход.

`_unhide` обходит весь граф (`rglob("*")` со `stat` на каждый файл): замер
25.09 на копии рабочего графа — медиана 477 мс, и обход бэклога платил её на
каждой встрече. Массовые обходы (`migrate_all`, цикл `retro_fill.main`) зовут
архив с `unhide=False` и снимают флаг сами, один раз, в `finally`. Оглавление
при этом пересобирается на каждой встрече: после `kill -9` посреди обхода оно
должно оставаться верным. Тесты считают вызовы, а не секунды.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import charoite_paths  # noqa: E402
import meeting_archive as ma  # noqa: E402
import rebuild_transcript  # noqa: E402
import retro_fill  # noqa: E402

N = 4
K = 3   # встреча, на которой обход падает: не первая и не последняя
SPEECH = "# Встреча\n\n" + "[10:01:00] Участник А: обсуждаем Ядра/Тема\n" * 30


@pytest.fixture(autouse=True)
def _no_live_model(модель_не_отвечает):
    """Саммари архива без модели: тест про вызовы, не про саммари."""
    return модель_не_отвечает


@pytest.fixture
def calls(monkeypatch):
    """Журнал вызовов `_unhide` и `_rebuild_index` с их аргументом-графом.

    `_rebuild_index` падает на встрече номер `fail_at` (если задан) — так
    исключение рождается внутри `archive_meeting` k-й встречи, после того как
    предыдущие встречи уже разложены."""
    log = {"unhide": [], "index": [], "fail_at": None}
    real_index = ma._rebuild_index

    def unhide(graph):
        log["unhide"].append(graph)

    def index(graph):
        log["index"].append(graph)
        if len(log["index"]) == log["fail_at"]:
            raise RuntimeError("сбой на k-й встрече")
        real_index(graph)

    monkeypatch.setattr(ma, "_unhide", unhide)
    monkeypatch.setattr(retro_fill, "_unhide", unhide)   # имя привязано при импорте
    monkeypatch.setattr(ma, "_rebuild_index", index)
    return log


def _world(tmp_path):
    graph = tmp_path / "граф"
    (graph / ma.ARCHIVE_DIR).mkdir(parents=True)
    (graph / "Встречи").mkdir()
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    for i in range(N):
        (tdir / f"2026-09-2{i}_1000_Тема.md").write_text(SPEECH, encoding="utf-8")
    return graph, tdir


# --- migrate_all ---------------------------------------------------------------

def test_migrate_all_unhides_the_graph_once_and_rebuilds_the_index_per_meeting(tmp_path, calls):
    graph, tdir = _world(tmp_path)
    assert ma.migrate_all(graph, tdir) == N
    assert calls["unhide"] == [graph]
    assert calls["index"] == [graph] * N
    assert (graph / ma.ARCHIVE_DIR / "_ОГЛАВЛЕНИЕ.md").is_file()


def test_migrate_all_unhides_once_even_when_a_meeting_fails(tmp_path, calls):
    graph, tdir = _world(tmp_path)
    calls["fail_at"] = K
    with pytest.raises(RuntimeError, match="k-й встрече"):
        ma.migrate_all(graph, tdir)
    assert calls["unhide"] == [graph]
    assert len(calls["index"]) == K, "обход остановился на упавшей встрече"


def test_migrate_all_without_meetings_still_unhides_once(tmp_path, calls):
    graph, tdir = _world(tmp_path)
    for f in tdir.iterdir():
        f.unlink()
    assert ma.migrate_all(graph, tdir) == 0
    assert (calls["unhide"], calls["index"]) == ([graph], [])


# --- одиночный вызов -----------------------------------------------------------

def test_single_archive_still_unhides_the_whole_graph(tmp_path, calls):
    graph, tdir = _world(tmp_path)
    a = ma.archive_meeting(graph, tdir, "2026-09-20_1000", "Тема", files_key="2026-09-20_1000_Тема")
    assert a is not None
    assert (calls["unhide"], calls["index"]) == ([graph], [graph])


def test_archive_with_unhide_off_rebuilds_the_index_but_leaves_the_flag(tmp_path, calls):
    graph, tdir = _world(tmp_path)
    ma.archive_meeting(graph, tdir, "2026-09-20_1000", "Тема",
                       files_key="2026-09-20_1000_Тема", unhide=False)
    assert (calls["unhide"], calls["index"]) == ([], [graph])


# --- retro_fill.main ------------------------------------------------------------

@pytest.fixture
def retro(tmp_path, monkeypatch):
    """`retro_fill.main` над каталогом из N встреч с настоящими `process` и
    архивом; модель не зовётся: минутки «свежие», генерация отвечает пусто."""
    graph, tdir = _world(tmp_path)
    charoite_paths.use_data_root(tmp_path, replace=True)
    monkeypatch.setattr(retro_fill, "load_user_or_example",
                        lambda root: {"log": {"transcripts_dir": "transcripts"}})
    monkeypatch.setattr(retro_fill.graphs, "graph_dir", lambda cfg: graph)
    monkeypatch.setattr(retro_fill, "harden_umask", lambda: None)
    monkeypatch.setattr(retro_fill, "gen", lambda *a, **k: "")
    monkeypatch.setattr(rebuild_transcript, "finalize_minutes", lambda *a, **k: "fresh")
    monkeypatch.setattr(rebuild_transcript, "record_minutes_passport", lambda *a, **k: None)
    return graph, tdir


def test_retro_fill_walk_unhides_the_graph_once(retro, calls, capsys):
    graph, _tdir = retro
    retro_fill.main([])
    assert f"встреч обработано {N}" in capsys.readouterr().out
    assert calls["unhide"] == [graph]
    assert calls["index"] == [graph] * N


def test_retro_fill_walk_unhides_once_even_when_a_meeting_fails(retro, calls):
    graph, _tdir = retro
    calls["fail_at"] = K
    with pytest.raises(RuntimeError, match="k-й встрече"):
        retro_fill.main([])
    assert calls["unhide"] == [graph]
    assert len(calls["index"]) == K


def test_retro_fill_process_alone_keeps_unhiding(retro, calls):
    """`process` — публичная точка входа: без аргумента флаг снимает архив, как прежде."""
    graph, tdir = retro
    live = sorted(tdir.glob("*.md"))[0]
    retro_fill.process(live, {"log": {"transcripts_dir": "transcripts"}}, graph, tdir)
    assert (calls["unhide"], calls["index"]) == ([graph], [graph])


# --- отбор встреч обхода -----------------------------------------------------
# Строки отбора ушли под `try` вместе с циклом; держим их поведение: пустышка
# короче 600 байт — не встреча, имя без штампа — не встреча, тема — из стема.

def _sized(path: pathlib.Path, size: int) -> pathlib.Path:
    body = "# Встреча\n\n[10:01:00] Участник А: обсуждаем\n".encode("utf-8")
    path.write_bytes(body + b"x" * (size - len(body)))
    assert path.stat().st_size == size
    return path


def test_migrate_all_skips_stubs_and_names_folders_by_the_topic(tmp_path, calls):
    graph, tdir = _world(tmp_path)
    for f in tdir.iterdir():
        f.unlink()
    _sized(tdir / "2026-09-20_1000_Тема.md", 600)
    _sized(tdir / "2026-09-21_1100.md", 600)
    _sized(tdir / "2026-09-22_1200_Пустышка.md", 599)
    assert ma.migrate_all(graph, tdir) == 2
    folders = sorted(p.name for p in (graph / ma.ARCHIVE_DIR).iterdir() if p.is_dir())
    assert folders == ["2026-09-20 10-00 — Тема", "2026-09-21 11-00 — встреча"]


def test_retro_fill_walk_skips_stubs_and_names_without_a_stamp(retro, calls, capsys):
    graph, tdir = retro
    for f in tdir.iterdir():
        f.unlink()
    _sized(tdir / "2026-09-20_1000_Тема.md", 600)
    _sized(tdir / "2026-09-22_1200_Пустышка.md", 599)
    _sized(tdir / "заметки.md", 5000)
    retro_fill.main([])
    assert "встреч обработано 1" in capsys.readouterr().out
    assert calls["index"] == [graph]
