"""Память демона (src/brain.py): граф из конфига, формат выдачи, сбой — исключением.

До №250 здесь жил HTTP-клиент сервера памяти (:8100) и тест держал его контракт
живым сервером. Теперь память — индекс графа в процессе демона
(src/graph_search.py); контракт для трёх контуров демона тот же: текст с
маркерами «Найдено…» / «⚠» / «не найдено», а сбой — исключение, деградацию
каждый контур выбирает сам.
"""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import brain  # noqa: E402
import graph_search  # noqa: E402


def _graph(root: pathlib.Path, name: str, marker: str) -> pathlib.Path:
    g = root / "Графы" / name
    (g / "Системы").mkdir(parents=True)
    (g / "Системы" / "Релиз.md").write_text(f"# Релиз\nРешили по релизу: {marker}\n\n## Встречи\n", encoding="utf-8")
    return g


def test_vault_search_reads_the_configured_graph_only(tmp_path, monkeypatch):
    # graphs.graph_dir читает env ДО конфига — машинные переменные не
    # должны решать исход теста (круг-1 по #405, DS)
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    monkeypatch.setattr(graph_search, "_shared", {})
    mine = _graph(tmp_path, "проект", "МОЙ_МАРКЕР")
    _graph(tmp_path, "соседний", "ЧУЖОЙ_МАРКЕР")
    cfg = {"sufler": {"graph_dir": str(mine)}}
    with pytest.raises(brain.MemoryNotReady):
        brain.vault_search(cfg, "что решили по релизу", limit=3, snippet_chars=700, timeout=5)   # не прогрет
    mem = brain.warm(cfg)
    assert mem is not None and mem.size == 1
    out = brain.vault_search(cfg, "что решили по релизу", limit=3, snippet_chars=700, timeout=5)
    # без кэша векторов семантики нет — выдача честно помечена, но не пуста
    assert out.startswith("⚠ Совпадения не проверены семантикой") and "• Системы/Релиз.md" in out
    assert "МОЙ_МАРКЕР" in out and "ЧУЖОЙ_МАРКЕР" not in out, "соседние графы в ответы не попадают"
    assert brain.vault_search(cfg, "qqqzzz", limit=3, snippet_chars=700, timeout=5).startswith("Ничего не найдено по")


def test_unconfigured_graph_raises(monkeypatch):
    # Сбой — исключением: деградация у каждого контура своя (узлы графа,
    # молчание, пустая память) — память её не выбирает за вызывающего.
    monkeypatch.delenv("CHAROITE_GRAPH_DIR", raising=False)
    monkeypatch.delenv("SUFLER_GRAPH_DIR", raising=False)
    monkeypatch.setattr(graph_search, "_shared", {})
    with pytest.raises(brain.MemoryUnavailable):
        brain.vault_search({"sufler": {}}, "вопрос", limit=1, snippet_chars=100, timeout=0.3)
    assert issubclass(brain.MemoryUnavailable, RuntimeError) and issubclass(brain.MemoryNotReady, RuntimeError)
    assert brain.warm({"sufler": {}}) is None
