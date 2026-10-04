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
    # потребитель своего транспорта наружу без решения (выходной круг 1 по №469, GLM I2)
    без_доктора = copy.deepcopy(layout)
    del без_доктора["mutation_not_critical"]["scripts/doctor.py"]
    assert any(p.startswith("scripts/doctor.py: импорт charoite_graph.net") for p in
               lm.zone_problems(inv, без_доктора, scanned))
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


def _в_зоне(src: str, qual: str, entries=("door::sweep",)) -> bool:
    import ast
    return lm.in_zone(entries, "src/door.py", qual, ast.parse(src))


def test_критичность_двери_идёт_по_прямому_вызову_в_модуле():
    """Вынос тела — в хелпер того же модуля, приватный или нет, с эффектом или
    только с условием, на любую глубину. Список зон хелпер не называет.
    Сосед, которого дверь не зовёт, и тот, кто зовёт дверь, критичными не становятся.
    """
    вынос = (
        "def sweep(path, limit):\n"
        "    return drop_if_big(path, limit)\n"
        "def drop_if_big(path, limit):\n"
        "    return _drop(path, limit)\n"
        "def _drop(path, limit):\n"
        "    if path.stat().st_size > limit:\n"
        "        path.unlink()\n"
        "    return path.exists()\n"
        "def _untouched(path):\n"
        "    return path.stat().st_size > 0\n"
        "def main(path):\n"
        "    return sweep(path, 1)\n")
    assert _в_зоне(вынос, "sweep")
    assert _в_зоне(вынос, "drop_if_big") and _в_зоне(вынос, "_drop")
    # потомок вызванного хелпера критичен, потомок невызванного — нет
    assert _в_зоне(вынос, "_drop.inner") and not _в_зоне(вынос, "_untouched.inner")
    assert not _в_зоне(вынос, "_untouched")
    assert not _в_зоне(вынос, "main")
    # без дерева членство лексическое: хелпер снова невидим
    assert not lm.in_zone(("door::sweep",), "src/door.py", "_drop")
    # условие без unlink — тот же класс, не каталог эффектов
    условие = (
        "def sweep(path, limit):\n"
        "    if _too_big(path, limit):\n"
        "        path.unlink()\n"
        "    return path.exists()\n"
        "def _too_big(path, limit):\n"
        "    return path.stat().st_size > limit\n")
    assert _в_зоне(условие, "_too_big")
    # общий хелпер, которого зовёт и некритичная функция, всё равно критичен
    общий = (
        "def sweep(path, limit):\n"
        "    return _drop(path, limit)\n"
        "def other(path):\n"
        "    return _drop(path, 0)\n"
        "def _drop(path, limit):\n"
        "    return path.stat().st_size > limit\n")
    assert _в_зоне(общий, "_drop")
    # вложенный вызов и вложенная функция хелпера
    вложенный = (
        "def sweep(path, limit):\n"
        "    def inner():\n"
        "        return _drop(path, limit)\n"
        "    return inner()\n"
        "def _drop(path, limit):\n"
        "    def gate():\n"
        "        return path.stat().st_size > limit\n"
        "    return gate()\n")
    assert _в_зоне(вложенный, "_drop") and _в_зоне(вложенный, "_drop.gate")
    # весь модуль по-прежнему критичен целиком, дерево этого не сужает
    import ast
    дерево = ast.parse(вынос)
    assert lm.in_zone(("door",), "src/door.py", "main", дерево)
    assert lm.in_zone(("door",), "src/door.py", "", дерево)


def test_вызов_метода_и_класса_того_же_модуля_критичен():
    """Вынос в метод своего класса, в `Класс.метод` и в `Класс().метод` — прямой
    вызов. Чужой приёмник и одноимённая функция модуля критичными не становятся.
    """
    метод = (
        "class Door:\n"
        "    def sweep(self, path, limit):\n"
        "        return self._drop(path, limit)\n"
        "    def _drop(self, path, limit):\n"
        "        if path.stat().st_size > limit:\n"
        "            path.unlink()\n"
        "        return True\n"
        "    @classmethod\n"
        "    def sweep_cls(cls, path, limit):\n"
        "        return cls._drop(path, limit)\n"
        "def drop(path):\n"
        "    return path.stat().st_size > 1\n")
    assert _в_зоне(метод, "Door._drop", ("door::Door.sweep",))
    assert _в_зоне(метод, "Door._drop", ("door::Door.sweep_cls",))
    assert not _в_зоне(метод, "drop", ("door::Door.sweep",))
    класс = (
        "class Cleaner:\n"
        "    def drop(self, path, limit):\n"
        "        return path.stat().st_size > limit\n"
        "    def other(self):\n"
        "        return 1\n"
        "def sweep(path, limit):\n"
        "    return Cleaner().drop(path, limit)\n"
        "def sweep_bare(path, limit):\n"
        "    return Cleaner.drop(path, limit)\n")
    assert _в_зоне(класс, "Cleaner.drop")
    assert _в_зоне(класс, "Cleaner.drop", ("door::sweep_bare",))
    assert not _в_зоне(класс, "Cleaner.other")
    # приёмник — не self: метод класса не подтягивается по одноимённому вызову
    чужой = (
        "class Door:\n"
        "    def sweep(self, hub, path):\n"
        "        return hub.drop(path)\n"
        "    def drop(self, path):\n"
        "        return path.stat().st_size > 1\n")
    assert not _в_зоне(чужой, "Door.drop", ("door::Door.sweep",))


def test_затенение_и_косвенный_вызов_хелпер_не_тянут():
    """Локальное имя закрывает функцию модуля. Вызов через переменную не след:
    иначе аргумент `spawn(fn)` втянул бы в зону цепочку пересборки сирот.
    Импорт чужого писца не делает критичной одноимённую функцию модуля.
    """
    тень = (
        "def sweep(path):\n"
        "    _drop = path.unlink\n"
        "    _drop()\n"
        "def _drop(path):\n"
        "    return path.stat().st_size > 1\n")
    assert not _в_зоне(тень, "_drop")
    косвенный = (
        "def sweep(path, limit):\n"
        "    op = _drop\n"
        "    return op(path, limit)\n"
        "def _drop(path, limit):\n"
        "    if path.stat().st_size > limit:\n"
        "        path.unlink()\n"
        "    return True\n")
    assert not _в_зоне(косвенный, "_drop")
    чужой_модуль = (
        "import safe_write\n"
        "def sweep(path):\n"
        "    safe_write.write_text(path, 'x')\n"
        "def write_text(path, body):\n"
        "    return len(body) > 1\n")
    assert not _в_зоне(чужой_модуль, "write_text")
    # путь скрипта, не импортируемое имя: та же запись `файл::функция`
    import ast
    скрипт = "def run(path):\n    return _drop(path)\ndef _drop(path):\n    path.unlink()\n"
    assert lm.in_zone(("scripts/door.py::run",), "scripts/door.py", "_drop", ast.parse(скрипт))


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
