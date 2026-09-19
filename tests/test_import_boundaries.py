"""Сторож границ слоёв и точек входа (№320, фаза 0 разбиения на пакеты).

Архитектурный круг 19.09 (DS и GLM): слои в `src/` есть по факту импортов, но
держались памятью автора и разовым замером; при этом рвутся не только импорты,
а две другие связи — кто кого запускает по пути и где лежит файл. Один
источник истины — `docs/design/layout.json`; этот тест сверяет его с
реальностью: каждый модуль отнесён к слою, каждое ребро против стрелок — в
allowlist с карточкой, каждая запись allowlist ещё существует, каждая точка
входа объявлена и файл на месте, каждый исполняемый файл в инвентаре, карта
свежа, область скана полна. Тот же класс, что `test_cloud_call_sites`:
инвариант структурный, проверяется разбором кода, не запуском.

Выходной круг по #594 (DS 4 Critical, GLM 2): связи собирались регексом по
прозе — докстринги и комментарии держали фантомные точки входа и вызывающих,
`nightly.sh` и CI-workflow были вне инвентаря, дубль модуля в двух слоях
легализовал бы ребро молча, карта в git не проверялась. Теперь python — по
AST без докстрингов, Swift/shell/yml — без комментариев, цель — любой
исполняемый файл, загрузка артефакта строгая, карта под гейтом.
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
    карточкой, снять устаревшее, объявить точку входа, поправить упоминающих,
    перегенерировать карту, расширить область скана)."""
    layout, graph, entries = world
    problems = lm.check(layout, graph, entries, map_text=lm.MAP.read_text(encoding="utf-8"),
                        outside=lm.out_of_scope_mentions())
    assert not problems, "\n".join(problems)


def test_layer_table_is_complete_and_the_arrows_point_down(world):
    layout, graph, _ = world
    assert set(layout["order"]) == set(layout["brief_layers"]) == set(layout["allowed"])
    assert not lm.unassigned(graph, layout) and not lm.stale_layers(graph, layout)
    # контракты фазы 0 по факту: core и cloud не импортируют ничего вне своего слоя
    # (внутри core `live_gate → file_locks` — законно), graph — ничего из meeting
    lay = lm.layer_of(layout)
    for m, layer in lay.items():
        if layer in ("core", "cloud"):
            outside = {d for d in graph[m] if lay[d] != layer}
            assert not outside, f"{m} ({layer}) импортирует {sorted(outside)}"
        if layer == "graph":
            assert not {d for d in graph[m] if lay[d] == "meeting"}, f"{m} тянет meeting: {sorted(graph[m])}"
    # долг фазы 3 виден на доске: graph → llm не разрешён стрелками, а живёт в allowlist с карточкой
    assert "llm" not in layout["allowed"]["graph"]
    assert ("graph_search", "llm") in lm.allowlist_edges(layout)


def test_the_artifact_is_loaded_strictly(tmp_path):
    """Инварианты артефакта — при загрузке, не в каждой проверке (Critical GLM
    по #594: дубль модуля в двух слоях менял слой молча; Important DS: перенос
    слоя без обоснования)."""
    import json
    base = lm.load_layout()

    def write(mutate):
        data = json.loads(json.dumps(base))
        mutate(data)
        p = tmp_path / "layout.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def dup(d): d["brief_layers"]["app"].append(d["brief_layers"]["core"][0])
    def up(d): d["allowed"]["core"] = ["app"]
    def no_why(d): d["layer_overrides"]["tier3"] = {"layer": "graph", "why": ""}
    def no_ticket(d): d["allowed_edges"][0]["ticket"] = ""
    def bad_entry(d): d["entry_points"]["docs/x.md"] = {"manual": True}
    for bad in (dup, up, no_why, no_ticket, bad_entry):
        with pytest.raises(lm.LayoutError):
            lm.load_layout(write(bad))
    assert lm.load_layout(write(lambda d: None))


def test_the_gate_sees_lazy_imports_and_new_upward_edges(tmp_path):
    """Отрицательные проверки на синтетическом дереве: импорт внутри функции
    виден; новое ребро вверх без allowlist — расхождение; запись allowlist без
    ребра — расхождение; точка входа без файла / незаявленная / без упоминаний
    / исполняемый файл не в инвентаре / карта отстала — расхождение."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "core_mod.py").write_text("x = 1\n", encoding="utf-8")
    (src / "top_mod.py").write_text("import core_mod\n", encoding="utf-8")
    (src / "low_mod.py").write_text("def f():\n    import top_mod\n    return top_mod\n", encoding="utf-8")
    layout = {"order": ["low", "high"], "allowed": {"low": [], "high": ["low"]},
              "brief_layers": {"low": ["core_mod", "low_mod"], "high": ["top_mod"]}, "layer_overrides": {},
              "allowed_edges": [], "entry_points": {}, "generated": "x"}
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
    # точки входа: незаявленная, заявленная без упоминаний, несуществующий файл, ручная
    layout["allowed_edges"] = layout["allowed_edges"][:1]
    layout["entry_points"] = {"src/gone.py": {"manual": False}}
    problems = lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}}, repo=tmp_path)
    assert any("src/top_mod.py упоминается в app/X.swift, но не объявлена" in p for p in problems)
    assert any("src/gone.py, но в коде его никто не упоминает" in p for p in problems)
    assert any("src/gone.py не существует" in p for p in problems)
    layout["entry_points"] = {"src/top_mod.py": {"manual": False}, "src/hand.py": {"manual": True}}
    (src / "hand.py").write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    graph = lm.import_graph(src)
    layout["brief_layers"]["low"].append("hand")
    assert lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}}, repo=tmp_path) == []
    # исполняемый файл не в инвентаре
    (src / "tool.py").write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    graph = lm.import_graph(src)
    layout["brief_layers"]["low"].append("tool")
    problems = lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}}, repo=tmp_path)
    assert any("исполняемый файл src/tool.py не в инвентаре" in p for p in problems)
    layout["entry_points"]["src/tool.py"] = {"manual": True}
    # карта отстала — расхождение; вне области — расхождение
    fresh = lm.render_map(layout, graph, {"src/top_mod.py": {"app/X.swift"}})
    assert lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}}, repo=tmp_path, map_text=fresh) == []
    assert any("отстал" in p for p in lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}}, repo=tmp_path,
                                               map_text=fresh + "x"))
    assert any("вне области скана" in p for p in lm.check(layout, graph, {"src/top_mod.py": {"app/X.swift"}},
                                                          repo=tmp_path, outside={"src/top_mod.py": {"Makefile"}}))


def test_entry_point_scanner_reads_code_not_prose(tmp_path):
    """Swift/shell/yml — без комментариев; python — литералы по AST без
    докстрингов, путь может быть подстрокой литерала (подсказка человеку) или
    склейкой через «/» (Critical DS и GLM по #594: фантомы из прозы)."""
    (tmp_path / "app" / "Sources").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "app" / "Sources" / "S.swift").write_text(
        '/// см. src/comment.py\n// и src/comment2.py\n/* src/block.py */\n'
        'p.arguments = ["src/daemon.py"] // src/trail.py\n', encoding="utf-8")
    (tmp_path / "app" / "make_app.sh").write_text("# scripts/hidden.sh\n$PY scripts/get_models.py\n", encoding="utf-8")
    (tmp_path / "scripts" / "n.sh").write_text('python src/dossier.py && echo "scripts/echoed.py" # scripts/x.py.bak\n'
                                                "$ROOT/scripts/nightly.sh\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("run: python scripts/check.py  # scripts/no.py\n",
                                                                encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text(
        '"""Докстринг: File "scripts/phantom.py", line 1."""\n'
        'run([sys.executable, str(CODE / "src" / "b.py")])\n'
        'subprocess.run(["scripts/memory_bench.py"])\n'
        'hint = f"запусти python3 scripts/doctor.py {flag}"\n'
        '# см. src/comment_only.py\n'
        'def f():\n    """scripts/inner_doc.py"""\n    return 1\n', encoding="utf-8")
    found = lm.entry_points_found(tmp_path)
    assert set(found) == {"src/daemon.py", "scripts/get_models.py", "src/dossier.py", "scripts/echoed.py",
                          "scripts/nightly.sh", "scripts/check.py", "src/b.py", "scripts/memory_bench.py",
                          "scripts/doctor.py"}, sorted(found)
    assert found["src/daemon.py"] == {"app/Sources/S.swift"}
    for phantom in ("src/comment.py", "src/comment2.py", "src/block.py", "src/trail.py", "scripts/hidden.sh",
                    "scripts/x.py", "scripts/no.py", "scripts/phantom.py", "src/comment_only.py", "scripts/inner_doc.py"):
        assert phantom not in found, phantom
    # исполняемые файлы: скрипты все, модули src только с __main__
    (tmp_path / "scripts" / "tool.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "lib.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "cli.py").write_text('if __name__ == "__main__":\n    pass\n', encoding="utf-8")
    assert lm.executables(tmp_path) == {"scripts/n.sh", "scripts/tool.py", "src/cli.py"}
    # самопроверка области: файл кода вне SOURCE_ROOTS с упоминанием пути
    (tmp_path / "Makefile.sh").write_text("python scripts/tool.py\n", encoding="utf-8")
    assert lm.out_of_scope_mentions(tmp_path) == {"scripts/tool.py": {"Makefile.sh"}}
