"""Сторож границ слоёв и точек входа (№320, фаза 0 разбиения на пакеты).

Архитектурный круг 19.09 (DS и GLM): слои в `src/` есть по факту импортов, но
держались памятью автора и разовым замером; при этом рвутся не только импорты,
а две другие связи — кто кого запускает по пути и где лежит файл (Swift зовёт
`src/daemon.py` в 22 местах, `code_root()` выводит корень из `__file__`).
Один источник истины — `docs/design/layout.json`; этот тест сверяет его с
реальностью: каждый модуль отнесён к слою, каждое ребро против стрелок — в
allowlist с карточкой, каждая запись allowlist ещё существует, каждая точка
входа объявлена и файл на месте. Тот же класс, что `test_cloud_call_sites`:
инвариант структурный, проверяется разбором кода, не запуском.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import layout_map as lm  # noqa: E402


@pytest.fixture(scope="module")
def world():
    layout = lm.load_layout()
    graph = lm.import_graph()
    entries = lm.entry_points_found()
    return layout, graph, entries


def test_layout_matches_the_code(world):
    """Один гейт: расхождений между раскладкой и кодом нет. Каждая строка —
    готовое действие (отнести к слою, развязать или внести в allowlist с
    карточкой, снять устаревшее, поправить вызывающих)."""
    layout, graph, entries = world
    problems = lm.check(layout, graph, entries)
    assert not problems, "\n".join(problems)


def test_layer_table_is_complete_and_the_arrows_point_down(world):
    layout, graph, _ = world
    assert set(layout["order"]) == set(layout["layers"]) == set(layout["allowed"])
    order = layout["order"]
    for layer, deps in layout["allowed"].items():
        assert all(order.index(d) < order.index(layer) for d in deps), f"{layer} зависит вверх: {deps}"
    assert not lm.unassigned(graph, layout) and not lm.stale_layers(graph, layout)
    # контракты фазы 0 по факту: core и cloud не импортируют ничего вне своего слоя
    # (внутри core `live_gate → file_locks` — законно), graph — ничего из meeting
    lay = lm.layer_of(layout)
    for m in layout["layers"]["core"] + layout["layers"]["cloud"]:
        outside = {d for d in graph[m] if lay[d] != lay[m]}
        assert not outside, f"{m} ({lay[m]}) импортирует {sorted(outside)}"
    for m in layout["layers"]["graph"]:
        assert not {d for d in graph[m] if lay[d] == "meeting"}, f"{m} тянет meeting: {sorted(graph[m])}"


def test_every_allowed_edge_carries_a_ticket(world):
    layout, _, _ = world
    missing = [f"{e['from']} → {e['to']}" for e in layout["allowed_edges"] if not e.get("ticket")]
    assert not missing, "рёбра без карточки на снятие: " + ", ".join(missing)


def test_the_gate_sees_lazy_imports_and_new_upward_edges(tmp_path):
    """Отрицательные проверки на синтетическом дереве: импорт внутри функции
    виден; новое ребро вверх без allowlist — расхождение; запись allowlist без
    ребра — расхождение; точка входа без файла — расхождение."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "core_mod.py").write_text("x = 1\n", encoding="utf-8")
    (src / "top_mod.py").write_text("import core_mod\n", encoding="utf-8")
    (src / "low_mod.py").write_text("def f():\n    import top_mod\n    return top_mod\n", encoding="utf-8")
    layout = {"order": ["low", "high"], "allowed": {"low": [], "high": ["low"]},
              "layers": {"low": ["core_mod", "low_mod"], "high": ["top_mod"]},
              "allowed_edges": [], "entry_points": []}
    graph = lm.import_graph(src)
    assert graph["low_mod"] == {"top_mod"}, "ленивый импорт внутри функции обязан быть виден"
    assert lm.violations(graph, layout) == [("low_mod", "top_mod")]
    problems = lm.check(layout, graph, {}, repo=tmp_path)
    assert any("новое ребро против стрелок: low_mod" in p for p in problems)
    layout["allowed_edges"] = [{"from": "low_mod", "to": "top_mod", "ticket": "№0"},
                               {"from": "core_mod", "to": "top_mod", "ticket": "№0"}]
    problems = lm.check(layout, graph, {}, repo=tmp_path)
    assert any("core_mod → top_mod, но такого ребра" in p for p in problems)
    assert not any("новое ребро" in p for p in problems)
    # точки входа: незаявленная, заявленная без вызывающих, несуществующий файл
    layout["allowed_edges"] = layout["allowed_edges"][:1]
    layout["entry_points"] = ["src/gone.py"]
    problems = lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}}, repo=tmp_path)
    assert any("src/top_mod.py зовётся из app/X.swift, но не объявлена" in p for p in problems)
    assert any("src/gone.py, но по этому пути никто не зовёт" in p for p in problems)
    assert any("src/gone.py не существует" in p for p in problems)
    layout["entry_points"] = ["src/top_mod.py"]
    assert lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}}, repo=tmp_path) == []


def test_entry_point_scanner_reads_swift_shell_and_python_forms(tmp_path):
    (tmp_path / "app" / "Sources").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "app" / "Sources" / "S.swift").write_text('p.arguments = ["src/daemon.py"]\n', encoding="utf-8")
    (tmp_path / "app" / "make_app.sh").write_text("$PY scripts/get_models.py\n", encoding="utf-8")
    (tmp_path / "scripts" / "n.sh").write_text("python src/dossier.py && echo scripts/x.py.bak\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text('run([sys.executable, str(CODE / "src" / "b.py")])\n'
                                           'subprocess.run(["scripts/memory_bench.py"])\n'
                                           '# см. src/comment_only.py\n', encoding="utf-8")
    found = lm.entry_points_found(tmp_path)
    assert set(found) == {"src/daemon.py", "scripts/get_models.py", "src/dossier.py", "src/b.py", "scripts/memory_bench.py"}
    assert found["src/daemon.py"] == {"app/Sources/S.swift"}
    assert "src/comment_only.py" not in found, "комментарий в python — не точка входа"
    assert "scripts/x.py" not in found, "имя с хвостом .bak — не путь к скрипту"
