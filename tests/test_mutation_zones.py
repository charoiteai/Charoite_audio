"""Область и зоны мутатора (№469): что мутируется и чьи выжившие держат мерж.

Решает раскладка, а не мутатор: область — `src/` и скрипты, которые запускает
продукт (`layout_map.mutation_area`), зоны — `mutation_critical` в `layout.json`.
Сторож зон двусторонний, как у `manual_entry_points`: запись называет живой файл и
живую функцию, а модуль с сетью или дверью записи обязан быть решён.
"""
import copy
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import layout_map as lm  # noqa: E402

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _мир():
    inv = lm.inventory(lm.REPO)
    return inv, lm.scan(inv), lm.load_layout()


def test_область_это_src_и_скрипты_продукта_а_не_служебное():
    inv, scanned, _ = _мир()
    area = lm.mutation_area(inv, scanned)
    assert "src/privacy.py" in area and "src/charoite_graph/net.py" in area
    # «Забыть» и облачная ревизия живут в scripts/, но запускаются продуктом
    assert {"scripts/forget_meeting.py", "scripts/cloud_review.py"} <= area
    # CI и pre-commit не корни: мутатор, сторож маркеров, замеры тени — вне области
    for служебное in ("scripts/mutate_check.py", "scripts/check_private_markers.py",
                      "scripts/nemotron_shadow_replay.py", "scripts/diar_bench.py"):
        assert служебное not in area, служебное
    assert not any(rel.startswith("tests/") for rel in area)


def test_зоны_живого_дерева_решены():
    inv, scanned, layout = _мир()
    assert lm.zone_problems(inv, layout, scanned) == []


def test_сторож_зон_краснеет_на_каждом_нарушении():
    inv, scanned, layout = _мир()
    # модуль с сетью без решения
    без_llm = copy.deepcopy(layout)
    del без_llm["mutation_not_critical"]["llm"]
    problems = lm.zone_problems(inv, без_llm, scanned)
    assert any(p.startswith("src/llm.py: импорт requests — дверь мутатора не решена") for p in problems), problems
    # имя, которого нет в области
    чужое = copy.deepcopy(layout)
    чужое["mutation_critical"]["scripts/mutate_check.py"] = "служебное"
    assert any("нет такого модуля или скрипта в области" in p for p in lm.zone_problems(inv, чужое, scanned))
    # функция, которой нет
    нет_функции = copy.deepcopy(layout)
    нет_функции["mutation_critical"]["daemon::_нет_такой"] = "опечатка"
    assert any("нет функции или класса _нет_такой" in p for p in lm.zone_problems(inv, нет_функции, scanned))
    # одна запись в обоих списках
    оба = copy.deepcopy(layout)
    оба["mutation_not_critical"]["audio"] = "противоречие"
    assert any("стоит и в mutation_critical, и в mutation_not_critical" in p
               for p in lm.zone_problems(inv, оба, scanned))


def test_in_zone_модуль_путь_и_функция():
    записи = {"audio", "scripts/forget_meeting.py", "daemon::_prune_graph_logs"}
    assert lm.in_zone(записи, "src/audio.py", "Hub.append")
    assert lm.in_zone(записи, "scripts/forget_meeting.py", "")
    assert lm.in_zone(записи, "src/daemon.py", "_prune_graph_logs")
    assert lm.in_zone(записи, "src/daemon.py", "_prune_graph_logs.inner")
    assert not lm.in_zone(записи, "src/daemon.py", "_prune_graph_logs_other")
    assert not lm.in_zone(записи, "src/daemon.py", "main")


def test_qualnames_и_области_узлов():
    import ast
    tree = ast.parse("class C:\n    def m(self):\n        x = 1\n\ndef f():\n    def g():\n        pass\n")
    assert lm.qualnames(tree) == {"C", "C.m", "f", "f.g"}
    scope = {type(n).__name__ + (f":{n.id}" if isinstance(n, ast.Name) else ""): s
             for n, s in lm.scoped_nodes(tree)}
    assert scope["Name:x"] == "C.m"


def test_инвентарь_ревизии_читает_коммит_а_не_диск(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "mod.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run([*_GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*_GIT, "commit", "-qm", "база"], cwd=repo, check=True)
    (repo / "src" / "mod.py").write_text("import requests\n", encoding="utf-8")
    (repo / "src" / "new.py").write_text("Y = 2\n", encoding="utf-8")
    inv = lm.inventory(repo, rev="HEAD")
    assert set(inv.files) == {"src/mod.py"}
    assert lm.zone_signal("src/mod.py", inv.files["src/mod.py"].tree) is None
    диск = lm.inventory(repo)
    assert lm.zone_signal("src/mod.py", диск.files["src/mod.py"].tree) == "импорт requests"
