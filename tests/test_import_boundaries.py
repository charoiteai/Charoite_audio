"""Сторож границ слоёв и точек входа (№320, фаза 0 разбиения на пакеты).

Архитектурный круг 19.09 (DS и GLM): слои в `src/` есть по факту импортов, но
держались памятью автора и разовым замером; при движении файлов рвутся не
только импорты, а две другие связи — кто кого запускает по пути и где файл
лежит. Один источник истины — `docs/design/layout.json`; этот тест сверяет его
с реальностью как равенство множеств в обе стороны по каждой сущности (круг 2
по #594: одностороннее включение либо держит лишнее, либо молчит о потерянном):
слои, рёбра против стрелок, точки входа (исполняемые файлы), названные пути
(код и проза), свежесть карты. Тот же класс, что `test_cloud_call_sites`:
инвариант структурный, проверяется разбором кода, не запуском.

Круг 1 по #594: связи собирались регексом по прозе — фантомы из докстрингов и
комментариев, `nightly.sh` и CI вне инвентаря, дубль слоя легализовал ребро.
Круг 2: одно множество на две сущности — библиотеки из подсказок попали в
точки входа, `make_app.sh` вне целей, `__main__` подстрокой, скрытые каталоги
вне самопроверки, числа в ARCHITECTURE снова разошлись.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import layout_map as lm  # noqa: E402


@pytest.fixture(scope="module")
def world():
    return lm.load_layout(), lm.import_graph(), lm.scan(), lm.executables()


def test_layout_matches_the_code(world):
    """Один гейт: расхождений между раскладкой и кодом нет. Каждая строка —
    готовое действие."""
    layout, graph, scanned, execs = world
    problems = lm.check(layout, graph, scanned, execs, map_text=lm.MAP.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_layer_table_is_complete_and_the_arrows_point_down(world):
    layout, graph, _, _ = world
    assert set(layout["order"]) == set(layout["brief_layers"]) == set(layout["allowed"])
    assert not lm.unassigned(graph, layout) and not lm.stale_layers(graph, layout)
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


def test_entry_points_are_executables_not_mentions(world):
    """Точки входа — исполняемые файлы: скрипты, `app/make_app.sh` (его зовёт
    релизный CI), модули `src/` с гвардом. Библиотека, названная подсказкой,
    точкой входа не является (Critical DS и GLM круга 2)."""
    layout, _, scanned, execs = world
    assert "app/make_app.sh" in execs and "scripts/nightly.sh" in execs and "src/daemon.py" in execs
    for lib in ("src/privacy.py", "src/graph_search.py", "src/llm_health.py"):
        assert lib not in execs and lib not in layout["manual_entry_points"]
        assert lib in scanned.mentions, f"{lib} назван подсказкой — путь обязан существовать, но точкой входа не быть"
    assert set(layout["manual_entry_points"]) <= set(execs)
    assert not set(layout["manual_entry_points"]) & set(scanned.mentions)


def test_the_artifact_is_loaded_strictly(tmp_path):
    """Инварианты артефакта — при загрузке: дубль модуля в двух слоях, стрелка
    вверх или в неизвестный слой, поправка без обоснования, ребро без карточки,
    ручная точка входа без обоснования или не по шаблону."""
    base = lm.load_layout()

    def write(mutate):
        data = json.loads(json.dumps(base))
        mutate(data)
        p = tmp_path / "layout.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def dup(d): d["brief_layers"]["app"].append(d["brief_layers"]["core"][0])
    def up(d): d["allowed"]["core"] = ["app"]
    def typo(d): d["allowed"]["core"] = ["ap"]
    def no_why(d): d["layer_overrides"]["tier3"] = {"layer": "graph", "why": ""}
    def no_ticket(d): d["allowed_edges"][0]["ticket"] = ""
    def bad_manual(d): d["manual_entry_points"]["docs/x.md"] = "почему-то"
    def empty_manual(d): d["manual_entry_points"]["scripts/x.py"] = ""
    for bad in (dup, up, typo, no_why, no_ticket, bad_manual, empty_manual):
        with pytest.raises(lm.LayoutError):
            lm.load_layout(write(bad))
    assert lm.load_layout(write(lambda d: None))


def test_the_gate_sees_lazy_imports_and_new_upward_edges(tmp_path):
    """Отрицательные проверки на синтетическом дереве без git: ленивый импорт
    виден; новое ребро вверх / устаревшая запись allowlist / исполняемый без
    вызывающих и без manual / manual на библиотеку / manual при живом вызове /
    названный путь без файла / карта отстала — всё расхождения."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "core_mod.py").write_text("x = 1\n", encoding="utf-8")
    (src / "top_mod.py").write_text("import core_mod\n", encoding="utf-8")
    (src / "low_mod.py").write_text("def f():\n    import top_mod\n    return top_mod\n", encoding="utf-8")
    layout = {"order": ["low", "high"], "allowed": {"low": [], "high": ["low"]},
              "brief_layers": {"low": ["core_mod", "low_mod"], "high": ["top_mod"]}, "layer_overrides": {},
              "allowed_edges": [], "manual_entry_points": {}, "generated": "x"}
    graph = lm.import_graph(src)
    assert graph["low_mod"] == {"top_mod"}, "ленивый импорт внутри функции обязан быть виден"
    assert lm.violations(graph, layout) == [("low_mod", "top_mod")]
    empty = lm.Scan({}, {}, [])
    problems = lm.check(layout, graph, empty, {}, repo=tmp_path)
    assert any("новое ребро против стрелок: low_mod" in p for p in problems)
    layout["allowed_edges"] = [{"from": "low_mod", "to": "top_mod", "ticket": "№0"},
                               {"from": "core_mod", "to": "top_mod", "ticket": "№0"}]
    problems = lm.check(layout, graph, empty, {}, repo=tmp_path)
    assert any("core_mod → top_mod, но такого ребра" in p for p in problems)
    assert not any("новое ребро" in p for p in problems)
    layout["allowed_edges"] = layout["allowed_edges"][:1]
    # точки входа
    (src / "cli.py").write_text("if __name__ == '__main__':\n    pass\n", encoding="utf-8")
    graph = lm.import_graph(src)
    layout["brief_layers"]["low"].append("cli")
    execs = lm.executables(tmp_path)
    assert execs == {"src/cli.py": "модуль с гвардом __main__"}, "гвард в одинарных кавычках — по AST"
    problems = lm.check(layout, graph, empty, execs, repo=tmp_path)
    assert any("исполняемый файл src/cli.py никто не зовёт" in p for p in problems)
    layout["manual_entry_points"] = {"src/cli.py": "руками", "src/top_mod.py": "ошибка: библиотека"}
    problems = lm.check(layout, graph, empty, execs, repo=tmp_path)
    assert any("src/top_mod.py, но это не исполняемый файл" in p for p in problems)
    layout["manual_entry_points"] = {"src/cli.py": "руками"}
    assert lm.check(layout, graph, empty, execs, repo=tmp_path) == []
    called = lm.Scan({"src/cli.py": {"app/X.swift"}, "src/gone.py": {"app/X.swift"}}, {"src/doc_gone.py": {"README.md"}}, [])
    problems = lm.check(layout, graph, called, execs, repo=tmp_path)
    assert any("src/cli.py объявлен ручным, но его зовёт код" in p for p in problems)
    assert any("путь src/gone.py назван в коде (app/X.swift), а файла нет" in p for p in problems)
    assert any("src/doc_gone.py назван в документации" in p for p in problems)
    layout["manual_entry_points"] = {}
    ok = lm.Scan({"src/cli.py": {"app/X.swift"}}, {}, [])
    assert lm.check(layout, graph, ok, execs, repo=tmp_path) == []
    fresh = lm.render_map(layout, graph, ok, execs)
    assert lm.check(layout, graph, ok, execs, repo=tmp_path, map_text=fresh) == []
    assert any("отстал" in p for p in lm.check(layout, graph, ok, execs, repo=tmp_path, map_text=fresh + "x"))
    broken = lm.Scan({}, {}, ["src/x.py не разбирается"])
    assert "src/x.py не разбирается" in lm.check(layout, graph, broken, {}, repo=tmp_path)


def test_scanner_reads_code_not_prose(tmp_path):
    """Swift/shell/yml — без комментариев; python — литералы по AST без
    докстрингов, путь как подстрока (подсказка человеку) и как склейка через
    «/»; голое имя `.sh` резолвится по имени своего скрипта (`./make_app.sh`
    из CI с working-directory), чужие голые имена и `.py` без каталога — нет;
    проза (md, toml) — только существование; битый python — в problems."""
    (tmp_path / "app" / "Sources").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "app" / "Sources" / "S.swift").write_text(
        '/// см. src/comment.py\n// и src/comment2.py\n/* src/block.py */\n'
        'p.arguments = ["src/daemon.py"] // src/trail.py\nlet u = "https://x" + "src/after_url.py"\n', encoding="utf-8")
    (tmp_path / "app" / "make_app.sh").write_text("# scripts/hidden.sh\n$PY scripts/get_models.py\n", encoding="utf-8")
    (tmp_path / "scripts" / "n.sh").write_text('python src/dossier.py && echo "scripts/echoed.py" # scripts/x.py.bak\n'
                                                "$ROOT/scripts/nightly.sh\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "run: python scripts/check.py  # scripts/no.py\nrun: ./make_app.sh\nrun: ./foreign.sh\n", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text(
        '"""Докстринг: File "scripts/phantom.py", line 1."""\n'
        'run([sys.executable, str(CODE / "src" / "b.py")])\n'
        'subprocess.run(["scripts/memory_bench.py"])\n'
        'hint = f"запусти python3 scripts/doctor.py {flag}"\n'
        'note = "модуль audio.py не путь"\n'
        '# см. src/comment_only.py\n'
        'def f():\n    """scripts/inner_doc.py"""\n    return 1\n', encoding="utf-8")
    (tmp_path / "src" / "broken.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Запуск: `scripts/setup.sh`, см. src/lib.py\n", encoding="utf-8")
    (tmp_path / "scripts" / "get_models.py").write_text("x = 1\n", encoding="utf-8")
    scanned = lm.scan(tmp_path)
    assert set(scanned.mentions) == {"src/daemon.py", "src/after_url.py", "scripts/get_models.py", "src/dossier.py",
                                     "scripts/echoed.py", "scripts/nightly.sh", "scripts/check.py", "app/make_app.sh",
                                     "src/b.py", "scripts/memory_bench.py", "scripts/doctor.py"}, sorted(scanned.mentions)
    assert scanned.mentions["src/daemon.py"] == {"app/Sources/S.swift"}
    assert scanned.mentions["app/make_app.sh"] == {".github/workflows/ci.yml"}
    for phantom in ("src/comment.py", "src/comment2.py", "src/block.py", "src/trail.py", "scripts/hidden.sh",
                    "scripts/x.py", "scripts/no.py", "scripts/phantom.py", "src/comment_only.py",
                    "scripts/inner_doc.py", "audio.py", "foreign.sh"):
        assert phantom not in scanned.mentions, phantom
    assert scanned.prose == {"scripts/setup.sh": {"README.md"}, "src/lib.py": {"README.md"}}
    assert scanned.problems and "src/broken.py не разбирается" in scanned.problems[0]
    assert lm.executables(tmp_path) == {"app/make_app.sh": "скрипт", "scripts/n.sh": "скрипт",
                                        "scripts/get_models.py": "скрипт"}
