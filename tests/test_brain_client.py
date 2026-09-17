"""Память демона (src/brain.py): граф из конфига, формат выдачи, сбой — исключением.

До №250 здесь жил HTTP-клиент сервера памяти (:8100) и тест держал его контракт
живым сервером. Теперь память — индекс графа в процессе демона
(src/graph_search.py); контракт для трёх контуров демона — значение: состояние
полем `status` (Verdict), фрагменты для модели, текст для человека (круг 3 по
#577: маркеры в строке разбирались префиксом и ломались), а сбой — исключение,
деградацию каждый контур выбирает сам.
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
    # без кэша векторов семантики нет — выдача честно помечена полем, но не пуста
    assert out.status is brain.Verdict.UNVERIFIED and "• Системы/Релиз.md" in out.fragments and "⚠" not in out.fragments
    assert out.text.startswith("⚠ Совпадения не проверены семантикой") and "• Системы/Релиз.md" in out.text
    assert "МОЙ_МАРКЕР" in out.fragments and "ЧУЖОЙ_МАРКЕР" not in out.text, "соседние графы в ответы не попадают"
    none = brain.vault_search(cfg, "qqqzzz", limit=3, snippet_chars=700, timeout=5)
    assert none.empty and none.status is brain.Verdict.UNVERIFIED, "пусто без семантики — не доказанное отсутствие (GLM C1 r3)"


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


def _res(status, blocks=(), dossiers=()):
    return graph_search.Result(list(blocks), len(blocks), status, dossiers=list(dossiers), query="q")


@pytest.mark.parametrize("status", list(brain.Verdict))
@pytest.mark.parametrize("shape", ["blocks", "dossiers", "empty"])
def test_memory_block_policy_is_one_table_for_every_status_and_shape(status, shape):
    """Политика «что честно сказать» — таблица фасада, а не if/elif контуров:
    шапка по статусу, EMPTY — без фрагментов, пусто и без узлов — '' (DS r4, критика 2)."""
    r = _res(status, blocks=["• a.md\n  факт"] if shape == "blocks" else (),
             dossiers=["📁 Досье «т»\n  сводка"] if shape == "dossiers" else ())
    block = brain.memory_block(r, budget=500)
    if shape == "empty" or status is brain.Verdict.EMPTY:
        assert block == "" and brain.memory_block(r, nodes="узел: статус", budget=500).startswith("Из узлов графа проекта:")
    else:
        assert block.startswith(brain.LEAD[status]) and r.fragments in block and "⚠" not in block
        if status is brain.Verdict.CONFIDENT:
            assert "СЛАБЫЕ" not in block and "НЕ ПРОВЕРЕНО" not in block
    assert brain.caveat(r) == brain.CAVEAT[status] and brain.absence_note(r) == brain.ABSENCE[status]
    assert (status is brain.Verdict.EMPTY) == (brain.absence_note(r) == "пусто"), "«пусто» — только по проверенной выдаче (DS C1 r4)"
    assert set(brain.LEAD) == set(brain.CAVEAT) == set(brain.ABSENCE) == set(brain.Verdict)


def test_memory_block_splits_the_budget_between_nodes_and_fragments():
    """Узлы первыми под общий кап съедали архив целиком (DS I1 r4): бюджет делится
    по долям, остаток одного уходит другому."""
    r = _res(brain.Verdict.UNVERIFIED, blocks=["• a.md\n  " + "ф" * 3000])
    nodes = "у" * 3000
    block = brain.memory_block(r, nodes=nodes, budget=2600)
    assert len(block) <= 2600 + 2 and block.startswith("Из узлов графа проекта:")
    assert brain.LEAD[brain.Verdict.UNVERIFIED] in block and block.count("ф") >= 1000, "фрагменты не съедены узлами"
    assert block.count("у") <= int(2600 * brain.NODES_SHARE)
    short_nodes = brain.memory_block(r, nodes="у" * 100, budget=2600)
    assert short_nodes.count("ф") > 2000, "остаток бюджета узлов уходит фрагментам"
    only_nodes = brain.memory_block(r, nodes=nodes, budget=1000) if False else brain.memory_block(None, nodes=nodes, budget=1000)
    assert only_nodes.startswith("Из узлов графа проекта:") and len(only_nodes) == 1000 and "vault" not in only_nodes
    assert brain.memory_block(None, budget=1000) == ""
