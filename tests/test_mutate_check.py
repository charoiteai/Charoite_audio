"""Мутатор сам обязан быть проверен — он инструмент доверия.

Инструмент, который молча говорит «всё хорошо», хуже отсутствия
инструмента: он выглядит как гарантия. Первая версия ровно это и делала —
объявляла мутанта выжившим там, где та же мутация руками роняла девять
тестов. Причина: рабочее дерево поднималось от текущего HEAD, а номера
строк брались из другого диапазона, и мутации ложились мимо — в
комментарии и пустые места.
"""
import ast
import collections
import dataclasses
import json
import re
import os
import pathlib
import shutil
import subprocess
import sys

import pytest


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import mutate_check as mc  # noqa: E402


def _mutate(tmp_path: pathlib.Path, code: str, lines: set[int]):
    f = tmp_path / "sample.py"
    f.write_text(code, encoding="utf-8")
    return mc.scan(f, lines).mutations


def test_конец_диапазона_а_не_текущая_ветка():
    """Ломать надо ту ревизию, чьи строки в диффе. Иначе мутации ложатся
    мимо — это и был баг первой версии."""
    assert mc.head_of("main...feature") == "feature"
    assert mc.head_of("main..feature") == "feature"
    assert mc.head_of("abc123") == "abc123"
    assert mc.head_of("main...") == "HEAD"
    assert mc.head_of("") == "HEAD"


def test_сравнение_ломается(tmp_path):
    muts = _mutate(tmp_path, "def f(x):\n    return x > 5\n", {2})
    assert any("Gt" in m.what for m in muts)


def test_логическая_связка_ломается(tmp_path):
    muts = _mutate(tmp_path, "def f(a, b):\n    return a and b\n", {2})
    assert any("And" in m.what for m in muts)


def test_возврат_обнуляется(tmp_path):
    muts = _mutate(tmp_path, "def f():\n    return 42\n", {2})
    assert any("return" in m.what for m in muts)


def test_строки_не_мутируются(tmp_path):
    """Переделка сообщения почти всегда «выживает» и тонет в отчёте шумом."""
    muts = _mutate(tmp_path, 'def f():\n    return "привет"\n', {2})
    assert not any("привет" in m.what for m in muts)


def test_чужие_строки_не_трогаем(tmp_path):
    """Мутируем только то, что изменено в диапазоне: полный проход по файлу —
    это тысячи мутантов и часы вместо минут."""
    code = "def f(x):\n    return x > 5\n\n\ndef g(y):\n    return y < 3\n"
    muts = _mutate(tmp_path, code, {2})
    assert muts and all(m.line == 2 for m in muts)


def test_мутация_реально_меняет_код(tmp_path):
    """Ключевая проверка: применение обязано изменить дерево, иначе прогон
    сравнивает код сам с собой и объявляет мутанта выжившим."""
    code = "def f(x):\n    return x > 5\n"
    f = tmp_path / "sample.py"
    f.write_text(code, encoding="utf-8")
    mut = next(m for m in mc.scan(f, {2}).mutations if "Gt" in m.what)

    tree = ast.parse(code)
    assert mut.apply(tree), "мутация не нашла свой узел"
    changed = ast.unparse(ast.fix_missing_locations(tree))
    assert changed != code.strip()
    assert ">=" in changed


def test_битый_файл_не_роняет_разбор(tmp_path):
    assert _mutate(tmp_path, "def f(:\n", {1}) == []


def test_тесты_ищутся_по_имени_модуля():
    """Гонять весь набор на каждого мутанта — часы; берём те, что вообще
    могут заметить поломку."""
    found = mc.tests_for(REPO, REPO / "src" / "owner_voice.py")
    assert any("owner_voice" in t for t in found)


def test_неизвестный_модуль_даёт_весь_набор(tmp_path):
    """«Не нашли тестов» не значит «его никто не проверяет» — берём всё.

    Дерево здесь своё, пустое: если звать по настоящему репозиторию, поиск
    находит сам этот файл — имя модуля написано в нём же. На эту ловушку
    инструмент уже попадался, теперь она закрыта тестом.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_stub.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")

    assert mc.tests_for(tmp_path, tmp_path / "src" / "lonely.py") == ["tests"]


def test_модуль_запускаемый_подпроцессом_находится(tmp_path):
    """CLI-вход живёт без импорта: тест гоняет его как отдельный процесс.
    Без этого на такой модуль шёл бы весь набор — часы вместо минут."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text(
        'import subprocess, sys\n'
        'def test_runs():\n'
        '    subprocess.run([sys.executable, "src/dictate_note.py"])\n'
        '    assert True\n', encoding="utf-8")

    found = mc.tests_for(tmp_path, tmp_path / "src" / "dictate_note.py")
    assert found == ["tests/test_cli.py"], found


@pytest.mark.parametrize("load", [
    'importlib.util.spec_from_file_location(\n    "lonely", ROOT / "scripts" / "lonely.py")',
    '_load("lonely")',
], ids=["spec_from_file_location", "хелпер _load"])
def test_модуль_загружаемый_по_пути_находится(tmp_path, load):
    """Свежая копия модуля на каждый тест — загрузкой по пути, без import:
    так тесты изолируют изменяемое состояние модуля. Мутатор этого не видел и
    судил модуль всем набором — локально база не укладывалась в лимит (№356)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_loaded.py").write_text(
        f"def test_ok():\n    mod = {load}\n    assert mod\n", encoding="utf-8")

    assert mc.tests_for(tmp_path, tmp_path / "scripts" / "lonely.py") == ["tests/test_loaded.py"]


def test_имя_модуля_без_загрузки_не_тянет_файл(tmp_path):
    """Имя в строке, в комментарии и чужой load — не загрузка модуля: такой файл
    в подмножество не идёт, иначе поиск опять стал бы подстрокой."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mentions.py").write_text(
        "import json\n"
        "# _load lonely — упоминание в комментарии\n"
        "# mod = _load(\"lonely\")  — закомментированная загрузка\n"
        "def test_ok():\n"
        "    assert 'lonely' != json.load\n", encoding="utf-8")

    assert mc.tests_for(tmp_path, tmp_path / "scripts" / "lonely.py") == ["tests"]


@pytest.mark.parametrize("written", [
    "import graphs\n",
    "import charoite_graph.graphs\n",
    "from graphs import load\n",
    "from charoite_graph.graphs import load\n",
    "from charoite_graph import graphs\n",
], ids=["плоский", "точечный", "из плоского", "из подмодуля", "из пакета"])
def test_пакетные_формы_импорта_находят_тесты(tmp_path, written):
    """Модуль пакета приходит точечным хвостом или импортом из пакета — мутатор
    обязан найти тест по любой из этих форм, а не судить модуль всем набором
    (№424)."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_g.py").write_text(
        written + "def test_ok():\n    assert True\n", encoding="utf-8")
    assert mc.tests_for(tmp_path, tmp_path / "src" / "graphs.py") == ["tests/test_g.py"]


def test_имя_модуля_в_другом_импорте_не_цепляет(tmp_path):
    """Соседнее имя (`graphsx`, упоминание в строке) — не импорт модуля: иначе
    новое правило снова стало бы поиском подстроки."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_other.py").write_text(
        "import graphsx\nfrom charoite_graph import graphsx\n"
        "def test_ok():\n    assert 'graphs' == 'graphs'\n", encoding="utf-8")
    assert mc.tests_for(tmp_path, tmp_path / "src" / "graphs.py") == ["tests"]


@pytest.mark.parametrize("module,test", [
    ("merge_graphs", "tests/test_merge_graphs.py"),
    ("nightly_dossier", "tests/test_dossier.py"),
    ("nightly_claude_cores", "tests/test_nightly_cloud_reports.py"),
    ("nightly_dossier_review", "tests/test_nightly_cloud_reports.py"),
])
def test_скрипты_с_загрузкой_по_пути_судятся_своими_тестами(module, test):
    """Боевые случаи №356: эти тесты грузят скрипт по пути. Без них в
    подмножестве мутант судился бы всем набором или мимо изолирующего теста."""
    assert test in mc.tests_for(REPO, REPO / "scripts" / f"{module}.py")


def test_зависший_прогон_считается_убитым(tmp_path, monkeypatch):
    """Мутант, подвесивший тесты, изменил поведение — это kill, а не выживший;
    иначе один такой съедает весь ночной бюджет и попадает в отчёт как
    «тесты не заметили»."""
    import subprocess

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    monkeypatch.setattr(subprocess, "run", hang)
    assert mc.run_tests(tmp_path, ["tests"], timeout=1) is False


def test_прогон_мутанта_без_xdist(tmp_path, monkeypatch):
    """Прогон мутанта — один процесс: плагин xdist выключен в argv, и `-n` из
    конфига или `PYTEST_ADDOPTS` его не распараллелит (№453)."""
    import subprocess

    seen: list = []

    def run(cmd, **kw):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert mc.run_tests(tmp_path, ["tests"], timeout=3) is True
    argv = seen[0]
    assert any(argv[i:i + 2] == ["-p", "no:xdist"] for i in range(len(argv))), argv


def test_глобальный_n_роняет_прогон_мутанта_громко(tmp_path, monkeypatch):
    """Живой прогон: при `-n 2` в `PYTEST_ADDOPTS` и установленном xdist набор,
    зелёный сам по себе, красный — разбор аргументов отказывает, а не идёт в
    воркерах молча. Без xdist опыт ничего не различает — пропуск."""
    pytest.importorskip("xdist")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    assert mc.run_tests(tmp_path, ["tests"], timeout=30) is True
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2")
    assert mc.run_tests(tmp_path, ["tests"], timeout=30) is False


def test_потолок_прогона_набора_выше_потолка_теста_в_худший_раз(tmp_path, monkeypatch):
    """Жёсткий потолок прогона набора — `WORST_RUN_FACTOR` × `--timeout`: потолок
    pytest действует на один тест, а тестов в наборе много. Ниже него живой, но
    долгий набор обрывался бы и записывался в убитые — выживший мутант прятался бы
    за таймаутом. На этом множителе стоит и бюджет шага мутатора в CI (№395).
    Реальный прогон ниже (`timeout=60`) порядок не различает: набор идёт секунды,
    а 60 // 4 — ещё 15 с (выживший мутатора по #637)."""
    import subprocess

    seen: dict = {}

    def run(cmd, **kw):
        seen["timeout"] = kw["timeout"]
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert mc.run_tests(tmp_path, ["tests"], timeout=3) is True
    assert seen["timeout"] == 3 * mc.WORST_RUN_FACTOR


def test_арифметика_ломается(tmp_path):
    """Ошибки на единицу и на множитель живут в размерах чанков, окнах,
    индексах — без этих мутаций целый класс кода не проверяется."""
    muts = _mutate(tmp_path, "def f(sr, s):\n    return int(sr * s)\n", {2})
    assert any("Mult" in m.what for m in muts)


def test_цепочка_сравнений_не_пропускается(tmp_path):
    """`a < b < c` раньше не мутировалась вовсе — фильтр требовал ровно один
    оператор."""
    muts = _mutate(tmp_path, "def f(a, b, c):\n    return a < b < c\n", {2})
    assert any("Lt" in m.what for m in muts)


def test_константы_уровня_модуля_не_мутируются(tmp_path):
    """Тест читает ту же константу, что и код, — мутация эквивалентна и в
    отчёте неотличима от настоящей дыры."""
    code = "POROG = 15.0\n\n\ndef f(x):\n    return x < POROG\n"
    muts = _mutate(tmp_path, code, {1, 5})
    assert not any("15.0" in m.what for m in muts), [m.what for m in muts]
    assert any("Lt" in m.what for m in muts), "сравнение мутировать надо"


def test_меняется_только_мутированный_узел(tmp_path):
    """Ключевая проверка достоверности.

    Раньше файл переписывался целиком через `ast.unparse`: комментарии
    исчезали, кавычки менялись на свои. Тест, который проверяет ИСХОДНИК по
    тексту (такой у нас есть), падал на мутантном файле из-за
    переформатирования — и все мутанты модуля отчитывались «убит»
    независимо от мутации (ревью 20.08, DeepSeek).
    """
    code = ('# важный комментарий\n'
            'PATH = "models" / "diar" / "embedding.onnx"\n'
            '\n'
            '\n'
            'def f(x):\n'
            '    return x > 5  # хвостовой комментарий\n')
    f = tmp_path / "sample.py"
    f.write_text(code, encoding="utf-8")
    mut = next(m for m in mc.scan(f, {6}).mutations if "Gt" in m.what)

    node = mut.apply(ast.parse(code))
    changed = mc.patch_source(code, node)

    assert changed is not None
    assert "x >= 5" in changed, changed
    assert "# важный комментарий" in changed, "комментарий потерян"
    assert "# хвостовой комментарий" in changed, "хвостовой комментарий потерян"
    assert '"models" / "diar"' in changed, "кавычки переписаны — текстовые тесты упадут"


def test_тождества_не_мутируются(tmp_path):
    """`x + 0`, `x - 0`, `x / 1` — подмена оператора тождественна для любых
    чисел, такие мутанты выживают всегда и засоряют отчёт."""
    muts = _mutate(tmp_path, "def f(x):\n    return x + 0\n", {2})
    assert not any(m.what.startswith("Add") for m in muts), [m.what for m in muts]

    muts = _mutate(tmp_path, "def f(x):\n    return x / 1\n", {2})
    assert not any(m.what.startswith("Div") for m in muts), [m.what for m in muts]


def test_имя_модуля_в_комментарии_не_тянет_тест(tmp_path):
    """Раньше упоминание пути в комментарии добавляло файл в набор — лишние
    минуты на каждого мутанта."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mention.py").write_text(
        '# когда-нибудь проверим src/dictate_note.py\n'
        'def test_stub():\n    assert True\n', encoding="utf-8")

    assert mc.tests_for(tmp_path, tmp_path / "src" / "dictate_note.py") == ["tests"]


def test_кириллица_не_ломает_замену(tmp_path):
    """Проект русскоязычный, и `ast` отдаёт смещения в БАЙТАХ.

    Первая версия резала строку по символам: на любой строке с кириллицей
    счёт расходился, хвост уезжал за конец узла, файл становился
    синтаксически битым — и мутант засчитывался убитым из-за поломки, а не
    из-за мутации. Ровно та ложь, которую предыдущая правка закрывала
    (ревью 20.08, круг 3, DeepSeek). Все прежние примеры были на латинице,
    поэтому дефект и не ловился.
    """
    code = ('def f(text):\n'
            '    if "## Ко-мышление" not in text:\n'
            '        return "нет раздела"\n'
            '    return "есть"\n')
    f = tmp_path / "sample.py"
    f.write_text(code, encoding="utf-8")
    mut = next(m for m in mc.scan(f, {2}).mutations if "NotIn" in m.what)

    changed = mc.patch_source(code, mut.apply(ast.parse(code)))

    assert changed is not None
    ast.parse(changed)                       # главное: файл остался валидным
    assert "Ко-мышление" in changed and " in text" in changed, changed
    assert "нет раздела" in changed, "хвост строки потерян"


def test_кириллица_перед_узлом_тоже_учтена(tmp_path):
    """Не-ASCII ДО мутируемого узла сдвигает ГОЛОВНОЙ срез.

    Первая версия теста называлась так же, но кириллица в ней стояла ВНУТРИ
    узла: до него шёл чистый ASCII, и посимвольный срез совпадал с байтовым —
    тест проходил и на сломанном коде (ревью 20.08, круг 4, DeepSeek).
    Теперь не-ASCII действительно предшествует узлу, и хвост достаточно
    длинный, чтобы сдвиг было видно.
    """
    code = ('def f(res):\n'
            '    порог = 0.9; return res["x"] > порог  # хвост нужен длинный\n')
    f = tmp_path / "sample.py"
    f.write_text(code, encoding="utf-8")
    mut = next(m for m in mc.scan(f, {2}).mutations if "Gt" in m.what)

    changed = mc.patch_source(code, mut.apply(ast.parse(code)))

    ast.parse(changed)
    assert ">= порог" in changed, changed
    assert "# хвост нужен длинный" in changed, "хвост уехал — срез посимвольный"


def test_ошибка_на_единицу_не_считается_шумом(tmp_path):
    """`x - 1` → `x + 1` меняет поведение всегда — это самый ценный класс
    мутаций, ради которого арифметику и добавляли. Первый фильтр душил его
    вместе с настоящим шумом."""
    muts = _mutate(tmp_path, "def f(n):\n    return n - 1\n", {2})
    assert any(m.what.startswith("Sub") for m in muts), [m.what for m in muts]


def test_умножение_на_единицу_мутируется(tmp_path):
    """`x * 1` → `x // 1` тождеством НЕ является: для 2.5 выйдет 2.0 вместо
    2.5. Прежний фильтр молча выбрасывал эту мутацию везде, где через
    выражение течёт нецелое число (ревью 20.08, круг 4, DeepSeek)."""
    muts = _mutate(tmp_path, "def f(n):\n    return n * 1\n", {2})
    assert any(m.what.startswith("Mult") for m in muts), [m.what for m in muts]


def test_константа_слева_не_считается_шумом(tmp_path):
    """`0 + n` → `0 - n` переворачивает знак: при левой константе порядок
    операндов сохраняется, а смысл — нет."""
    muts = _mutate(tmp_path, "def f(n):\n    return 0 + n\n", {2})
    assert any(m.what.startswith("Add") for m in muts), [m.what for m in muts]


def test_тождества_с_плавающей_точкой_тоже_шум(tmp_path):
    """`x + 0.0` — то же тождество, что и `x + 0`. Проверка только на целые
    пропускала их мимо фильтра (ревью 20.08, круг 4: обе головы независимо)."""
    muts = _mutate(tmp_path, "def f(x):\n    return x + 0.0\n", {2})
    assert not any(m.what.startswith("Add") for m in muts), [m.what for m in muts]


def test_устаревший_байткод_не_судит_мутанта_по_чужому_коду(tmp_path, monkeypatch):
    """Python сверяет .pyc с исходником по mtime в секундах и размеру. Два
    мутанта одной длины, записанные в одну секунду, для него один файл:
    второй исполнялся байткодом первого и получал ЕГО вердикт. Так 21.08
    «выжил» мутант 6.0→0 в stt_runtime, под который тест написан и который
    руками падает (DeepSeek независимо: «отчёт пережил этот тест»).

    Мутанта здесь исполняет ПОДПРОЦЕСС теста, как демона в живом наборе:
    `-B` у pytest до него не доходит, работает только переменная окружения —
    и первая версия теста страховала не её, а избыточный флаг (DeepSeek
    по #368). Переменную из внешнего окружения снимаем: с ней и старый код
    не писал бы байткод, и регрессия прошла бы незамеченной."""
    import os

    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "import pathlib, subprocess, sys\n"
        "SRC = pathlib.Path(__file__).resolve().parents[1] / 'src'\n"
        "def test_value():\n"
        "    out = subprocess.run([sys.executable, '-c',\n"
        "        'import mod; print(mod.value())'], cwd=SRC,\n"
        "        capture_output=True, text=True, check=True).stdout\n"
        "    assert out.strip() == '1'\n", encoding="utf-8")
    mod = src / "mod.py"
    mod.write_text("def value():\n    return 1\n", encoding="utf-8")
    assert mc.run_tests(tmp_path, ["tests/test_mod.py"], timeout=60) is True
    stamp = int(mod.stat().st_mtime) + 5
    # Первый мутант зелёный (`+1` — та же единица), второй — красный (`-1`);
    # длина файла одинаковая, mtime подгоняем в одну секунду.
    mod.write_text("def value():\n    return +1\n", encoding="utf-8")
    os.utime(mod, (stamp, stamp))
    assert mc.run_tests(tmp_path, ["tests/test_mod.py"], timeout=60) is True
    mod.write_text("def value():\n    return -1\n", encoding="utf-8")
    os.utime(mod, (stamp, stamp))
    assert mc.run_tests(tmp_path, ["tests/test_mod.py"], timeout=60) is False
    assert not list(tmp_path.rglob("__pycache__")), "мутатор оставил байткод в дереве"


def test_прогон_мутанта_не_получает_корень_данных_мутатора(tmp_path, monkeypatch):
    """Дверь канона публикует корень данных мутатора в `CHAROITE_ROOT` (№440), а
    набор тестов мутанта называет свой: живой корень владельца ему не передаётся
    даже тогда, когда обвязка (conftest) сама сломана мутантом (круг 1, M2)."""
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path / "данные-владельца"))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_env.py").write_text(
        "import os\n\ndef test_env():\n    assert 'CHAROITE_ROOT' not in os.environ\n", encoding="utf-8")
    assert mc.run_tests(tmp_path, ["tests/test_env.py"], timeout=60) is True


def test_явный_return_none_не_мутируется(tmp_path):
    """`return None` → `return None` — тождество; в отчёте оно читалось как
    выживший мутант и тонуло среди настоящих (партия D, 22.08)."""
    muts = _mutate(tmp_path, "def f(x):\n    if x is None:\n        return None\n    return x\n", {3, 4})
    assert [m.line for m in muts if m.what.startswith("return")] == [4], [str(m) for m in muts]


def test_гвард_занятости_снимает_только_force():
    """Без флага гвард действует; `--force` снимает. Мутант `or → and` в прежнем
    предикате на два флага пережил CI по #605; флага для CI больше нет — на
    раннере без данных владельца гвард молчит сам (`machine_busy` пуст)."""
    import argparse
    assert mc.busy_guard(argparse.Namespace(force=False)) is True
    assert mc.busy_guard(argparse.Namespace(force=True)) is False


def test_занятая_машина_останавливает_мутатор_а_force_нет(monkeypatch, capsys):
    """Поведение через `main`: при живой записи без флага — код 3 и ни одного
    прогона; `--force` проходит гвард и упирается в пустой диапазон, не в занятость."""
    import busy_signals
    вызовы = []
    monkeypatch.setattr(busy_signals, "machine_busy",
                        lambda *a, **kw: вызовы.append(kw) or ["живая запись"])
    assert mc.main(["mutate_check.py", "--range", "HEAD...HEAD"]) == 3
    assert "машина занята" in capsys.readouterr().out
    # другой мутатор гварду старта не помеха: замок разделяемый (№444 B)
    assert вызовы == [{"count_mutation": False}], вызовы
    # пустой диапазон — «проверять нечего» ИМЕННО этим кодом, а не любым не-3:
    # прежний `!= 3` проходил и при 0, то есть весь смысл круга 2 не держался
    import exit_codes
    assert mc.main(["mutate_check.py", "--range", "HEAD...HEAD", "--force"]) == exit_codes.EXIT_NOTHING_TO_CHECK
    assert "машина занята" not in capsys.readouterr().out
    assert exit_codes.outcome(exit_codes.EXIT_NOTHING_TO_CHECK) == "nothing"


def _крит(critical: bool = True) -> dict:
    """Выживший для фактов: критичная зона или нет."""
    return {"key": "k", "path": "src/m.py", "line": 1, "what": "Eq → NotEq", "critical": critical}


def test_таблица_исхода_прогона():
    """Факты прогона → код возврата, все случаи в одной таблице.

    Прежняя лестница `if` в конце `main` спрашивала `tested == 0` раньше
    полноты, и прогон, прерванный на первом мутанте при плане из сорока,
    отвечал «проверять было нечего»; ни один тест туда не доставал, потому что
    покрыт был только пустой диапазон (круг 4 по №339, обе головы). С №469 судья
    один на прогон, шард и свод шардов, выборка — это план: «срезано выборкой»
    неполнотой не считается, а выживший вне критичных зон исход не меняет.
    """
    import exit_codes
    N, P, U = exit_codes.EXIT_NOTHING_TO_CHECK, exit_codes.EXIT_PARTIAL, exit_codes.EXIT_UNJUDGED
    F = mc.Facts
    таблица = [
        (F(),                                          N),   # плана не было вовсе
        (F(M=40, P=40),                                U),   # прервано на первом мутанте
        (F(M=40, P=40, tested=3),                      P),   # прервано посередине
        (F(M=40, P=40, tested=39, skipped=1),          P),   # один не применился
        (F(M=40, P=40, tested=40),                     0),   # судилась вся выборка, чисто
        (F(M=40, P=40, full=260, tested=40),           0),   # выборка из большого плана — не неполнота
        (F(M=40, P=40, tested=40, survivors=[_крит(False)]), 0),  # некритичный выживший — список
        (F(M=40, P=40, tested=3, survivors=[_крит(False)]), P),   # но неполноту он не прячет (r2 Sonnet C1)
        (F(M=40, P=40, tested=1, survivors=[_крит()]),  1),  # критичный выживший важнее неполноты
        (F(M=40, P=40, tested=40, survivors=[_крит()]), 1),
        (F(M=40, P=40, tested=40, broken="база красная", broken_rc=2), 2),
        (F(M=40, P=40, broken="копия", broken_rc=1),    1),
        (F(M=0, P=6),                                  N),   # пустая доля непустой выборки
        (F(M=0, P=0, nodes=3, lines_in=5),             exit_codes.EXIT_UNMUTABLE),
        (F(M=0, P=0, lines_in=5),                      N),   # строки есть, кода в них нет
        (F(M=0, P=0, unread=1),                        P),   # непрочитанный файл — неполнота
        (F(M=40, P=40, tested=40, unread=1),           P),
        (F(M=40, P=40, tested=40, unread=1, survivors=[_крит()]), 1),
    ]
    for факты, ждём in таблица:
        got = mc.verdict_code(факты)
        assert got == ждём, f"{факты}: {got}, ждали {ждём}"
    assert exit_codes.outcome(mc.verdict_code(F(M=40, P=40))) == "unjudged"
    assert exit_codes.outcome(mc.verdict_code(F(M=40, P=40, tested=3))) == "partial"
    assert exit_codes.outcome(mc.verdict_code(F(M=40, P=40, tested=40))) == "ok"
    assert exit_codes.outcome(mc.verdict_code(F(nodes=1, lines_in=1))) == "unmutable"


def _факты_плана(plan, totals, tested=None):
    """Факты прогона, в котором судилась вся доля плана (или `tested` мутантов)."""
    return mc.Facts(M=len(plan), P=totals.planned, full=totals.full,
                    tested=len(plan) if tested is None else tested,
                    unread=totals.files_unreadable, nodes=totals.nodes, lines_in=totals.lines_in)


def _строка(отчёт: pathlib.Path) -> dict:
    """Ядро машинной строки прогона: доля, выборка и слово."""
    data = json.loads(mc.shard_line_path(отчёт).read_text(encoding="utf-8"))
    assert data["v"] == mc.FACTS_VERSION
    return {k: data[k] for k in ("K", "N", "M", "P", "word")}


def _факт(k: int, n: int, m: int, p: int, word: str, **kw) -> dict:
    """Машинная строка шарда в формате фактов (№469) по старой записи «K, N, M, P,
    слово»: слово раскладывается в факты, из которых судья выведет его заново."""
    f = {"ok": dict(tested=m), "nothing": dict(tested=0),
         "partial": dict(tested=max(m - 1, 0), unread=0 if m else 1),
         "unjudged": dict(tested=0), "unmutable": dict(tested=0, nodes=1),
         "fail": dict(tested=m, survivors=[{"key": "k", "path": "src/m.py", "line": 1,
                                           "what": "Eq → NotEq", "critical": True}])}[word]
    return {"v": mc.FACTS_VERSION,
            **dataclasses.asdict(mc.Facts(K=k, N=n, M=m, P=p, full=p, **{**f, **kw})),
            "word": word}


def _шард(directory: pathlib.Path, имя: str, k: int, n: int, m: int, p: int, word: str):
    (directory / имя).write_text(json.dumps(_факт(k, n, m, p, word)), encoding="utf-8")


def _quiet_machine(monkeypatch):
    import busy_signals
    monkeypatch.setattr(busy_signals, "machine_busy", lambda *a, **kw: [])


def _range_as_given(monkeypatch):
    """Диапазон — как написан: тесты плана подают выдуманные имена ревизий и
    подменяют план, а настоящее разрешение (`resolve_range`) судят свои тесты."""
    monkeypatch.setattr(mc, "resolve_range", lambda root, rng: rng)


def test_есть_изменённые_строки_но_ломать_нечего(monkeypatch, capsys):
    """Ветка «строки есть, мутировать нечего» отвечает своим кодом и печатает
    счётчики плана: под общим «нечего» 25.09 ноль мутантов от правки условия
    читался как пустой диапазон (№386). Прежний тест подменял `mutations_for`,
    а план пустел раньше — на `git show` несуществующей ревизии."""
    import exit_codes
    _quiet_machine(monkeypatch)
    _range_as_given(monkeypatch)
    totals = mc.ScanTotals(files_in=2, lines_in=7, lines_constant=3, nodes=11)
    monkeypatch.setattr(mc, "plan_for", lambda root, rng, shard=None, max_n=None, only_critical=False: ([], totals))
    assert mc.main(["mutate_check.py", "--range", "A...B"]) == exit_codes.EXIT_UNMUTABLE
    out = capsys.readouterr().out
    assert "ничего мутируемого" in out
    for число in ("файлов 2", "строк 7", "констант модуля 3", "узлов AST 11", "не прочитано файлов 0"):
        assert число in out, out


def test_строки_без_кода_это_nothing_со_счётчиками(monkeypatch, capsys):
    """Правка одного комментария: строки в диапазоне есть, узлов кода нет —
    исход «нечего», и сообщение называет, из чего собран пустой план, а не
    врёт «нет изменённых строк» (критика DS круга 1 по #630)."""
    import exit_codes
    _quiet_machine(monkeypatch)
    _range_as_given(monkeypatch)
    totals = mc.ScanTotals(files_in=1, lines_in=2, lines_constant=1, nodes=0)
    monkeypatch.setattr(mc, "plan_for", lambda root, rng, shard=None, max_n=None, only_critical=False: ([], totals))
    assert mc.main(["mutate_check.py", "--range", "A...B"]) == exit_codes.EXIT_NOTHING_TO_CHECK
    out = capsys.readouterr().out
    assert "нет кода" in out and "нет изменённых строк" not in out, out
    for число in ("файлов 1", "строк 2", "констант модуля 1", "узлов AST 0"):
        assert число in out, out


def test_нет_изменённых_строк_это_nothing(monkeypatch, capsys):
    import exit_codes
    _quiet_machine(monkeypatch)
    _range_as_given(monkeypatch)
    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {})
    assert mc.main(["mutate_check.py", "--range", "A...B"]) == exit_codes.EXIT_NOTHING_TO_CHECK
    assert "нет изменённых строк" in capsys.readouterr().out


def test_ни_один_файл_не_прочитался_это_неполнота(monkeypatch, capsys):
    """Файл, чей `git show` не прочитался, раньше молча выпадал из плана, и
    пустой план отвечал «нечего» (№386). Файла нет в ревизии — диапазон при этом
    настоящий: разрешается он до плана и отказал бы раньше (№460)."""
    import exit_codes
    _quiet_machine(monkeypatch)
    monkeypatch.setattr(mc, "changed_lines",
                        lambda root, rng: {REPO / "scripts" / "призрак_нет_в_ревизии.py": {1, 2}})
    assert mc.main(["mutate_check.py", "--range", "HEAD...HEAD"]) == exit_codes.EXIT_PARTIAL
    assert "не прочитано файлов 1 из 1" in capsys.readouterr().out


_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _git_repo(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    subprocess.run([*_GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*_GIT, "commit", "-qm", "проба"], cwd=repo, check=True)
    return repo


def test_план_считает_строки_константы_узлы_и_непрочитанное(tmp_path, monkeypatch):
    """`plan_for` на настоящем git: числа `ScanTotals` и файл, которого нет в
    ревизии, — в `files_unreadable`, а не молча вне плана."""
    code = ("ПОРОГ = 5\n"                       # 1: константа модуля
            "# комментарий\n"                   # 2: ни одного узла
            "def f(x):\n"                       # 3
            "    return not x\n"                # 4
            "ГРАНИЦА = 7\n")                    # 5: константа вне диапазона не в счёт
    repo = _git_repo(tmp_path, {"src/mod.py": code})
    призрак = repo / "src" / "ghost.py"          # в рабочем дереве, но не в HEAD
    призрак.write_text("def g(y):\n    return not y\n", encoding="utf-8")
    # рабочее дерево разошлось с ревизией: разбирать надо ревизию, а не диск
    (repo / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {
        repo / "src" / "mod.py": {1, 2, 4}, призрак: {2}})

    plan, totals = mc.plan_for(repo, "HEAD")

    assert (totals.files_in, totals.lines_in, totals.lines_constant, totals.nodes,
            totals.files_unreadable, totals.planned, totals.full, totals.unreadable) == \
        (2, 4, 1, 3, 1, 2, 2, ["src/ghost.py — нет в ревизии HEAD"]), totals
    assert collections.Counter(m.bare() for m in plan) == {"not X → X": 1, "return X → return None": 1}
    assert {m.path for m in plan} == {repo / "src" / "mod.py"}


def test_файл_не_в_utf8_это_неполнота_а_не_трассировка(tmp_path, monkeypatch):
    """`git show` прочитал байты, а декодировать их нельзя: файл идёт в
    `files_unreadable`, план остального диапазона собирается (DS M1 круга 1
    по #630: прежде `UnicodeDecodeError` ронял прогон трассировкой)."""
    repo = _git_repo(tmp_path, {"src/mod.py": "def f(x):\n    return not x\n"})
    (repo / "src" / "latin.py").write_bytes(b"# \xe9t\xe9\ndef g(y):\n    return not y\n")
    subprocess.run([*_GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*_GIT, "commit", "-qm", "latin-1"], cwd=repo, check=True)
    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {
        repo / "src" / "mod.py": {2}, repo / "src" / "latin.py": {3}})

    plan, totals = mc.plan_for(repo, "HEAD")

    assert totals.files_in == 2 and totals.files_unreadable == 1, totals
    assert {m.path for m in plan} == {repo / "src" / "mod.py"}
    assert mc.verdict_code(_факты_плана(plan, totals)) == mc.EXIT_PARTIAL


def test_файл_не_разбирается_это_неполнота_а_не_нечего(tmp_path, monkeypatch):
    """Третья нога той же неполноты: `git show` прочитал, utf-8 декодировался, а
    `ast.parse` отказал. Файл идёт в `files_unreadable`, и пустой план из него —
    `partial`, а не «мутировать нечего» (выходной круг 2 по №441, DS C1: при
    P = 0 вердикт шардов красил такой диапазон зелёным)."""
    repo = _git_repo(tmp_path, {"src/mod.py": "def f(x):\n    return not x\n",
                                "src/bad.py": "def g(:\n    return 1\n"})
    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {
        repo / "src" / "mod.py": {2}, repo / "src" / "bad.py": {1, 2}})

    plan, totals = mc.plan_for(repo, "HEAD")

    assert totals.files_in == 2 and totals.files_unreadable == 1, totals
    assert {m.path for m in plan} == {repo / "src" / "mod.py"}
    assert mc.verdict_code(_факты_плана(plan, totals)) == mc.EXIT_PARTIAL

    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {repo / "src" / "bad.py": {1, 2}})
    plan, totals = mc.plan_for(repo, "HEAD")
    assert plan == [] and totals.files_unreadable == 1
    assert mc.verdict_code(_факты_плана(plan, totals)) == mc.EXIT_PARTIAL
    # файл назван с причиной — и в отчёте, а не только числом (выходной круг 3 по №441, DS M1)
    assert len(totals.unreadable) == 1 and totals.unreadable[0].startswith("src/bad.py — не разбирается (SyntaxError")
    отчёт = mc.render_report(_факты_плана(plan, totals), unreadable=totals.unreadable)
    assert "НЕ ПРОЧИТАН src/bad.py — не разбирается" in отчёт, отчёт


@pytest.mark.parametrize("text", ["x = 1\0\n", "x = " + "(" * 400 + "1" + ")" * 400 + "\n"])
def test_любой_отказ_разбора_это_неполнота_а_не_трассировка(tmp_path, monkeypatch, text):
    """NUL-байт и патологическая вложенность: тип отказа `ast.parse` зависит от
    версии (NUL — `ValueError` до 3.12, `SyntaxError` с 3.12; вложенность —
    `SyntaxError` или `RecursionError`). Любой такой отказ — файл «не разбирается»
    в списке несудимого, а не трассировка до первой записи отчёта шарда
    (выходной круг 3 по №441, DS I1)."""
    repo = _git_repo(tmp_path, {"src/mod.py": "def f(x):\n    return not x\n"})
    (repo / "src" / "bad.py").write_bytes(text.encode("utf-8"))
    subprocess.run([*_GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*_GIT, "commit", "-qm", "не разбирается"], cwd=repo, check=True)
    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {repo / "src" / "bad.py": {1}})
    plan, totals = mc.plan_for(repo, "HEAD")
    assert plan == [] and totals.files_unreadable == 1, totals
    assert totals.unreadable[0].startswith("src/bad.py — не разбирается ("), totals.unreadable
    assert mc.verdict_code(_факты_плана(plan, totals)) == mc.EXIT_PARTIAL


@pytest.mark.parametrize("error", [ValueError("source code string cannot contain null bytes"),
                                   RecursionError("maximum recursion depth exceeded")])
def test_разбор_ловит_отказы_всех_версий(monkeypatch, error):
    """`parse_source` называет причиной и те отказы, которых наш интерпретатор
    сегодня не бросает: `ValueError` на NUL-байте у 3.11, `RecursionError` на
    вложенности. Отказ — значение, а не исключение наружу."""
    def отказ(text):
        raise error
    monkeypatch.setattr(mc.ast, "parse", отказ)
    tree, why = mc.parse_source("x = 1\n")
    assert tree is None and why.startswith(type(error).__name__ + ":"), why
    assert str(error) in why, "причина называет текст отказа, а не только его тип"


def test_план_берёт_изменённые_строки_из_git(tmp_path):
    """Сквозь `changed_lines`: две ревизии, изменена одна строка."""
    repo = _git_repo(tmp_path, {"src/mod.py": "def f(x):\n    return x\n"})
    (repo / "src" / "mod.py").write_text("def f(x):\n    return not x\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)

    plan, totals = mc.plan_for(repo, "HEAD~1...HEAD")

    assert (totals.files_in, totals.lines_in, totals.nodes, totals.planned, totals.full) == \
        (1, 1, 3, 2, 2), totals
    assert sorted(m.bare() for m in plan) == ["not X → X", "return X → return None"]


# Таблица конструкций (№386): фрагмент внутри `def f(a, b, x, flag, ok):`, строка 2.
# Ожидание — мультимножество: число скрыло бы подмену одного оператора другим,
# множество — потерю второго `not` в строке.
КОНСТРУКЦИИ = [
    ("if not x:\n        pass", {"not X → X": 1}),
    ("if not a or not b:\n        pass", {"not X → X": 2, "Or → And": 1}),
    ("return not flag", {"not X → X": 1, "return X → return None": 1}),
    ("g(not x, b)", {"not X → X": 1}),
    ("return a == (not b)", {"not X → X": 1, "Eq → NotEq": 1, "return X → return None": 1}),
    ('return f"{not ok}"', {"not X → X": 1, "return X → return None": 1}),
    ("assert x", {}),
    ("y = -x", {}),          # UnaryOp без проверки на Not сюда не пройдёт
    ("yield x", {}),
]


def _fragment(code: str) -> str:
    return f"def f(a, b, x, flag, ok):\n    {code}\n"


@pytest.mark.parametrize("code,ждём", КОНСТРУКЦИИ, ids=[c for c, _ in КОНСТРУКЦИИ])
def test_таблица_конструкций(tmp_path, code, ждём):
    muts = _mutate(tmp_path, _fragment(code), {2})
    assert collections.Counter(m.bare() for m in muts) == collections.Counter(ждём)


def _dump_after_apply(m, src: str) -> str:
    tree = ast.parse(src)
    m.apply(tree)
    return ast.dump(tree)


@pytest.mark.parametrize("code,оператор,строка", [
    ("if not x:\n        pass", "not X → X", "    if x:"),
    ("if not a or not b:\n        pass", "Or → And", "    if not a and (not b):"),
    ("return a == (not b)", "Eq → NotEq", "    return a != (not b)"),
    ("return a == (not b)", "return X → return None", "    return"),
    ("return a + b", "Add → Sub", "    return a - b"),
    ("return 5", "5 → 0", "    return 0"),
    ("return True", "True → False", "    return False"),
])
def test_каждый_оператор_применяется(tmp_path, code, оператор, строка):
    """Ожидание — текстом мутанта. Сверка дампа с `_dump_after_apply` была
    верна по построению: `applied` проверяет ровно её же, и тест держал только
    «не отказал» (DS M2 круга 1 по #630)."""
    src = _fragment(code)
    muts = [m for m in _mutate(tmp_path, src, {2}) if m.bare() == оператор]
    assert len(muts) == 1, [m.what for m in muts]
    text, why = mc.applied(muts[0], src)
    assert text is not None, why
    assert text.splitlines()[1] == строка, text


def test_снятие_not_в_скобках_когда_приоритет_ниже(tmp_path):
    """`x and not (a or b)` → без скобок `x and a or b` — другое дерево."""
    src = _fragment("return x and not (a or b)")
    m = next(m for m in _mutate(tmp_path, src, {2}) if m.bare() == "not X → X")
    text, why = mc.applied(m, src)
    assert text is not None, why
    assert "x and (a or b)" in text, text
    assert ast.dump(ast.parse(text)) == _dump_after_apply(m, src)


def test_близнецы_в_одной_позиции_различимы(tmp_path):
    """`(a == b) == c`: два `Eq → NotEq`, внешний и внутренний. Локатор по паре
    «строка, колонка» путал бы их, а описание без отрезка — в отчёте."""
    src = _fragment("if (a == b) == x:\n        pass")
    twins = [m for m in _mutate(tmp_path, src, {2}) if m.bare() == "Eq → NotEq"]
    assert len(twins) == 2 and len({m.what for m in twins}) == 2, [m.what for m in twins]
    texts = {mc.applied(m, src)[0] for m in twins}
    assert texts == {src.replace("(a == b) == x", "(a == b) != x"),
                     src.replace("(a == b) == x", "(a != b) == x")}, texts


def test_близнецы_с_общим_началом(tmp_path):
    """`a + b + x`: внешний и внутренний BinOp начинаются в одной точке —
    локатор по началу отдавал внешний обоим мутантам."""
    src = _fragment("return a + b + x")
    adds = [m for m in _mutate(tmp_path, src, {2}) if m.bare() == "Add → Sub"]
    texts = {mc.applied(m, src)[0] for m in adds}
    assert texts == {src.replace("a + b + x", "a + b - x"),
                     src.replace("a + b + x", "a - b + x")}, texts


def test_f_строка_никогда_не_даёт_несошедшийся_текст(tmp_path):
    """Позиции внутри f-строк точны только с 3.12: ниже `applied` вправе
    отказать, но не вправе отдать текст, чьё дерево не то."""
    src = _fragment('return f"{x} и {not ok} при {a + b}"')
    muts = _mutate(tmp_path, src, {2})
    assert muts
    for m in muts:
        text, why = mc.applied(m, src)
        if text is None:
            assert why, m
            continue
        assert ast.dump(ast.parse(text)) == _dump_after_apply(m, src), (m.what, text)


def test_битый_текст_мутанта_не_применяется(tmp_path, monkeypatch):
    src = _fragment("if not x:\n        pass")
    m = _mutate(tmp_path, src, {2})[0]
    monkeypatch.setattr(mc, "patch_source", lambda s, n: s + "\n!")
    text, why = mc.applied(m, src)
    assert text is None and why == "текст мутанта не разбирается"


@pytest.mark.parametrize("patched,причина", [
    (lambda s, n: None, "замена не собралась"),
    (lambda s, n: s, "замена не изменила текст"),
], ids=["None", "тот же текст"])
def test_замена_без_результата_не_применяется(tmp_path, monkeypatch, patched, причина):
    src = _fragment("if not x:\n        pass")
    m = _mutate(tmp_path, src, {2})[0]
    monkeypatch.setattr(mc, "patch_source", patched)
    assert mc.applied(m, src) == (None, причина)


def test_тождественная_мутация_не_применяется(tmp_path):
    """Мутация, не меняющая дерево, не «применяется» ни в каком виде: попытка в
    скобках иначе выдавала `(True)` за годного мутанта."""
    src = _fragment("return True")
    m = next(x for x in _mutate(tmp_path, src, {2}) if x.bare() == "True → False")
    assert "return False" in mc.applied(m, src)[0]
    m.change = mc._swap_const(True)
    assert mc.applied(m, src) == (None, "мутация не меняет дерево")


def test_битый_исходник_и_пропавший_узел(tmp_path):
    src = _fragment("return a == b")
    m = next(x for x in _mutate(tmp_path, src, {2}) if x.bare() == "Eq → NotEq")
    text, why = mc.applied(m, "def f(:\n")
    assert text is None and why.startswith("исходник не разбирается"), why
    assert mc.applied(m, _fragment("pass")) == (None, "узел не нашёлся на своём отрезке")


def test_на_месте_цели_другой_узел(tmp_path):
    """Файл поменялся между планом и прогоном: на тех же координатах стоит
    сравнение с другим операндом. Ломать его — судить не ту мутацию."""
    muts = _mutate(tmp_path, _fragment("return a == b"), {2})
    m = next(x for x in muts if x.bare() == "Eq → NotEq")
    text, why = mc.applied(m, _fragment("return a == x"))
    assert text is None and why == "на месте узла другой"


def test_main_не_засчитывает_битого_мутанта_убитым(tmp_path, monkeypatch, capsys):
    """Непарсящийся текст мутанта раньше уходил в дерево, прогон падал на
    `SyntaxError`, и мутант считался «убит» (№386). Прогон тестов здесь честно
    имитирует это: красный, если файл в дереве не разбирается."""
    import exit_codes
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    rel = pathlib.Path("scripts") / "mutate_check.py"
    head = subprocess.run(["git", "show", f"HEAD:{rel.as_posix()}"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout
    mut = next(m for m in mc.scan(REPO / rel, set(range(1, 400)), head).mutations
               if m.bare() == "not X → X")
    monkeypatch.setattr(mc, "plan_for",
                        lambda root, rng, shard=None, max_n=None, only_critical=False: ([mut], mc.ScanTotals(files_in=1, lines_in=1,
                                                                            planned=1)))
    monkeypatch.setattr(mc, "tests_for", lambda root, module: ["tests"])

    def run_tests(cwd, targets, timeout):
        try:
            ast.parse((cwd / rel).read_text(encoding="utf-8"))
        except SyntaxError:
            return False
        return True
    monkeypatch.setattr(mc, "run_tests", run_tests)
    monkeypatch.setattr(mc, "patch_source", lambda s, n: s + "\n!")

    # единственный мутант плана не применился — не судился ни один: `unjudged`, а
    # не «часть» (DeepSeek по PR #637)
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force"]) == exit_codes.EXIT_UNJUDGED
    out = capsys.readouterr().out
    assert "убит" not in out, out
    assert f"НЕ ПРИМЕНИЛОСЬ {mut} — текст мутанта не разбирается" in out, out


def test_исход_main_отдаёт_verdict_code():
    """Последний возврат `main` — вызов `verdict_code`, а не константа и не
    пустой `return`: исход прогона считается в одном месте. Мутант
    «return X → return None» на этой строке пережил CI по №339 — теперь он
    меняет узел AST и краснеет здесь."""
    tree = ast.parse((REPO / "scripts" / "mutate_check.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    # по номеру строки, а не по порядку обхода: ast.walk идёт в ширину и
    # «последний» в нём — не последний в исходнике
    last = max((n for n in ast.walk(main) if isinstance(n, ast.Return)), key=lambda n: n.lineno)
    assert isinstance(last.value, ast.Call), "последний return main должен быть вызовом"
    fn = last.value.func
    assert getattr(fn, "id", getattr(fn, "attr", None)) == "verdict_code", (
        "исход прогона считает verdict_code — одна точка, а не константа по месту")


def _чужой_клон(tmp_path) -> pathlib.Path:
    """Git-клон с одним `src/` — каталог, из которого запускают мутатор другого
    дерева. Клон целиком, а не один канон: сигналы занятости сами кладут свой
    каталог первым в sys.path, и с одним каноном в чужом `src/` дефект круга 1
    по коду №331 не воспроизводился."""
    чужой = tmp_path / "другой-клон"
    shutil.copytree(REPO / "src", чужой / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (чужой / "scripts").mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q"], cwd=чужой, check=True)
    subprocess.run([*git, "add", "-A"], cwd=чужой, check=True)
    subprocess.run([*git, "commit", "-qm", "проба"], cwd=чужой, check=True)
    return чужой


def _мутатор(cwd: pathlib.Path, корень: str | None, *флаги: str) -> subprocess.CompletedProcess:
    """Мутатор ЭТОГО дерева отдельным процессом; `корень=None` — переменной нет
    вовсе, иначе `CHAROITE_ROOT` ровно такой, как дан (пустая строка — тоже)."""
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    if корень is not None:
        env["CHAROITE_ROOT"] = корень
    return subprocess.run([sys.executable, str(REPO / "scripts" / "mutate_check.py"),
                           "--range", "HEAD...HEAD", "--force", *флаги],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=120, check=False)


@pytest.mark.parametrize("корень", [None, "", "   "], ids=["нет переменной", "пустая", "пробелы"])
def test_мутатор_без_названного_корня_отказывает_дверью_канона(tmp_path, корень):
    """Корень данных мутатор НАЗЫВАЕТ (№440): без `CHAROITE_ROOT` — отказ двери
    канона её кодом и рецептом, до диапазона, плана и лока. Угадывание корня
    (`resolve_root`) из рабочего дерева отвечало самим деревом: гвард «идёт
    встреча» смотрел мимо лока демона владельца, а лок мутатора ложился не туда,
    где его ждёт ночь. Запуск из чужого клона — ровно тот путь, где догадка
    отвечала бы корнем кода."""
    import exit_codes
    чужой = _чужой_клон(tmp_path)
    out = _мутатор(чужой, корень)
    assert "Traceback" not in out.stderr, out.stderr[-800:]
    assert out.returncode == exit_codes.EXIT_ROOT_UNNAMED, (out.returncode, out.stdout[-400:],
                                                           out.stderr[-400:])
    assert "CHAROITE_ROOT=" in out.stderr, out.stderr[-400:]
    # ни диапазона, ни плана: отказ — первым делом после разбора аргументов
    assert "диапазон:" not in out.stdout and "Мутантов" not in out.stdout, out.stdout[-400:]


def test_мутатор_из_чужого_клона_с_корнем_строит_план(tmp_path):
    """Названный корень — и мутатор одного клона, запущенный из каталога
    другого, собирает процесс из своего дерева: канон и сигналы занятости
    берутся рядом со скриптом. Вторая вставка `src/` git-корня текущего
    каталога брала канон чужого клона, и сверка корня кода роняла мутатор
    трассировкой (круг 1 по коду №331, Opus M2). Шапка прогона печатает
    названный корень."""
    import exit_codes
    чужой = _чужой_клон(tmp_path)
    данные = tmp_path / "данные"
    данные.mkdir()
    out = _мутатор(чужой, str(данные))
    assert "Traceback" not in out.stderr, out.stderr[-800:]
    assert out.returncode == exit_codes.EXIT_NOTHING_TO_CHECK, (out.returncode, out.stdout[-400:])
    assert f"корень данных: {данные.resolve()}" in out.stdout, out.stdout[-400:]


def test_лок_демона_в_названном_корне_останавливает_мутатор(tmp_path):
    """Сигналы занятости читаются по НАЗВАННОМУ корню: лок демона встречи в нём
    — отказ кодом «занято», а тот же лок в каталоге запуска мутатор не судит
    (корень не угадывается из дерева)."""
    import fcntl
    import live_gate
    чужой = _чужой_клон(tmp_path)
    данные = tmp_path / "данные"
    for корень in (данные, чужой):
        live_gate.lock_path(корень).parent.mkdir(parents=True, exist_ok=True)
    with live_gate.lock_path(данные).open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
        env["CHAROITE_ROOT"] = str(данные)
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "mutate_check.py"),
                              "--range", "HEAD...HEAD"],
                             cwd=чужой, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 3, (out.returncode, out.stdout[-400:], out.stderr[-400:])
    assert "машина занята (живая запись)" in out.stdout, out.stdout[-400:]
    # тот же лок демона в каталоге запуска, а корень назван другой — не помеха
    with live_gate.lock_path(чужой).open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        env["CHAROITE_ROOT"] = str(tmp_path / "другие-данные")
        out = subprocess.run([sys.executable, str(REPO / "scripts" / "mutate_check.py"),
                              "--range", "HEAD...HEAD"],
                             cwd=чужой, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert "машина занята" not in out.stdout, out.stdout[-400:]


@pytest.mark.корень_называет_тест
def test_лок_мутатора_и_гвард_берут_названный_корень(tmp_path, monkeypatch, capsys):
    """Лок мутации и гвард старта получают корень, названный `CHAROITE_ROOT`, —
    не дерево запуска и не корень кода: ночь ждёт `logs/mutation.lock` там."""
    import busy_signals
    данные = tmp_path / "данные"
    monkeypatch.setenv("CHAROITE_ROOT", str(данные))
    гвард, лок = [], []
    monkeypatch.setattr(busy_signals, "machine_busy",
                        lambda root, **kw: гвард.append(pathlib.Path(root)) or [])

    class Лок:
        def __init__(self, root):
            лок.append(pathlib.Path(root))

        def acquire(self):
            return False                   # отказ замка — прогон кончается сразу, копии нет
    monkeypatch.setattr(busy_signals, "MutationLock", Лок)
    mut = _М("src/mod.py", "k", False)      # до мутанта дело не доходит: замок отказал раньше
    monkeypatch.setattr(mc, "plan_for",
                        lambda root, rng, shard=None, max_n=None, only_critical=False: ([mut], mc.ScanTotals(files_in=1, lines_in=1,
                                                                            planned=1)))
    _range_as_given(monkeypatch)
    assert mc.main(["mutate_check.py", "--range", "HEAD"]) == 3
    assert гвард == [данные.resolve()] and лок == [данные.resolve()], (гвард, лок)
    out = capsys.readouterr().out
    assert f"корень данных: {данные.resolve()}" in out, out


# --- Бюджет прогона `--budget-s` (№395) --------------------------------------
# Время — подменённые часы: каждый прогон набора двигает их на заданные секунды.
# Отсчёт с 1000, а не с нуля: с нулём `now - start` и `now + start` неотличимы.


class _Часы:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _мутанты(rel: str, n: int) -> list:
    """Первые `n` мутантов HEAD-версии файла, которые применяются к ней же."""
    head = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=REPO,
                          capture_output=True, text=True, check=True).stdout
    found = mc.scan(REPO / rel, set(range(1, head.count("\n") + 1)), head).mutations
    return [m for m in found if mc.applied(m, head)[0] is not None][:n]


def _прогон(tmp_path, monkeypatch, plan, *, секунды, падать_на=None):
    """Подменить план, наборы, часы и `run_tests`. Набор модуля — свой файл
    тестов, новым списком на каждый вызов: ключ длительностей обязан от этого
    не зависеть. Возвращает журнал вызовов `run_tests`: (набор, таймаут).
    `plan=None` — план настоящий: `plan_for` по репозиторию, из которого запущен
    прогон (сквозные тесты копии, №460)."""
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    if plan is not None:
        def plan_for(root, rng, shard=None, max_n=None, only_critical=False):
            взяли = mc.select(list(plan), max_n)
            return взяли, mc.ScanTotals(files_in=1, lines_in=1, planned=len(взяли), full=len(plan))
        monkeypatch.setattr(mc, "plan_for", plan_for)
    monkeypatch.setattr(mc, "tests_for", lambda root, module: [f"tests/test_{module.stem}.py"])
    часы = _Часы()
    monkeypatch.setattr(mc, "clock", часы)
    журнал = []

    def run_tests(cwd, targets, timeout):
        журнал.append((tuple(targets), timeout))
        if падать_на is not None and len(журнал) == падать_на:
            raise RuntimeError("раннер оборвал job")
        часы.t += секунды[targets[0]]
        # база зелёная (первый прогон каждого набора), мутанты убиты
        return [t for t, _ in журнал].count(tuple(targets)) == 1
    monkeypatch.setattr(mc, "run_tests", run_tests)
    return журнал


# ---- Диапазон и копия мутанта (№460) ----------------------------------------
# Копия — локальный клон на SHA головы, диапазон разрешается в SHA один раз до
# плана. `git worktree` писал реестр в общий `.git`, и параллельные `add`/`remove`
# соседних прогонов падали (опыты 1–2 №460).


def _два_коммита(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """Репозиторий теста: первый коммит — `return x`, второй — `return not x`."""
    repo = _git_repo(tmp_path, {"src/mod.py": "def f(x):\n    return x\n"})
    first = _rev(repo, "HEAD")
    (repo / "src" / "mod.py").write_text("def f(x):\n    return not x\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "вторая"], cwd=repo, check=True)
    return repo, first, _rev(repo, "HEAD")


def _rev(repo: pathlib.Path, what: str) -> str:
    return subprocess.run(["git", "rev-parse", what], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.mark.parametrize("rng, ждём", [
    ("A...B", ("A", "...", "B")),
    ("A..B", ("A", "..", "B")),
    ("X", ("", "", "X")),
    ("..B", ("HEAD", "..", "B")),
    ("A..", ("A", "..", "HEAD")),
    ("...B", ("HEAD", "...", "B")),
    (" A ... B ", ("A", "...", "B")),
    ("", ("", "", "HEAD")),
])
def test_грамматика_диапазона_одна(rng, ждём):
    """Пустой конец с любой стороны — `HEAD`, как читает git (`rev-parse ..X` —
    это `HEAD..X`); `head_of` — проекция той же грамматики, а не вторая копия."""
    assert mc.split_range(rng) == ждём
    assert mc.head_of(rng) == ждём[2]


def test_диапазон_разрешается_в_sha(tmp_path):
    repo, first, second = _два_коммита(tmp_path)
    assert mc.resolve_range(repo, "HEAD~1...HEAD") == f"{first}...{second}"
    assert mc.resolve_range(repo, "HEAD~1..HEAD") == f"{first}..{second}"
    assert mc.resolve_range(repo, "HEAD") == second
    assert mc.resolve_range(repo, "..HEAD~1") == f"{second}..{first}"
    with pytest.raises(mc.PreparationError, match="нет-такой") as отказ:
        mc.resolve_range(repo, "HEAD...нет-такой")
    assert "single revision" in str(отказ.value) or "fatal" in str(отказ.value), отказ.value


def test_непонятный_диапазон_код_1_до_копии(tmp_path, monkeypatch, capsys):
    """Отказ подготовки — «сломалась сама проверка» (код 1), а не `unjudged`: плана
    не было. Разрешение идёт до копии — каталога `mutate-*` не заводится."""
    import exit_codes
    repo, _, _ = _два_коммита(tmp_path)
    _quiet_machine(monkeypatch)
    monkeypatch.chdir(repo)
    копии = []
    monkeypatch.setattr(mc.tempfile, "mkdtemp", lambda **kw: копии.append(kw) or str(tmp_path / "x"))
    rc = mc.main(["mutate_check.py", "--range", "HEAD...нет-такой"])
    assert rc == 1 and exit_codes.outcome(rc) == "fail"
    out = capsys.readouterr().out
    assert "подготовка не удалась" in out and "нет-такой" in out, out
    assert копии == []


def test_копия_не_собралась_пишет_fail_доли_и_отпускает_замок(tmp_path, monkeypatch, capsys):
    """Отказ копии — после плана: отчёт и машинная строка доли ложатся, как у
    красной базы, со словом `fail`, а слияние долей видит «шард K: fail», а не
    пропавший файл. Замок отпущен, каталог копии убран (выходной круг 1 №460)."""
    import busy_signals
    import exit_codes
    repo, _, _ = _два_коммита(tmp_path)
    _прогон(tmp_path, monkeypatch, None, секунды={"tests/test_mod.py": 1})
    monkeypatch.chdir(repo)
    каталоги = []
    настоящий = mc.tempfile.mkdtemp

    def mkdtemp(**kw):
        каталоги.append(pathlib.Path(настоящий(**kw)))
        return str(каталоги[-1])
    monkeypatch.setattr(mc.tempfile, "mkdtemp", mkdtemp)

    def сломанная(root, sha, tmp):
        raise mc.PreparationError(f"копия {tmp / 'tree'}: git clone — нет места")
    monkeypatch.setattr(mc, "copy_tree", сломанная)
    отчёт = tmp_path / "отчёт.txt"
    rc = mc.main(["mutate_check.py", "--range", "HEAD~1...HEAD", "--shard", "1/1", "--max", "all",
                  "--timeout", "100", "--report", str(отчёт)])
    assert rc == 1 and exit_codes.outcome(rc) == "fail"
    assert "подготовка не удалась" in capsys.readouterr().out
    строка = json.loads(mc.shard_line_path(отчёт).read_text(encoding="utf-8"))
    assert строка["word"] == "fail" and строка["M"] == строка["P"] > 0, строка
    assert mc.COPY_FAILED in отчёт.read_text(encoding="utf-8")
    assert каталоги and not any(d.exists() for d in каталоги)
    замок = busy_signals.MutationLock(tmp_path)
    assert замок.acquire(), "замок мутатора не отпущен после отказа копии"
    замок.release()


def test_сдвиг_head_после_разрешения_не_меняет_копию(tmp_path, monkeypatch):
    """План строится по разрешённому диапазону, и коммит, сдвинувший HEAD посреди
    прогона, копию не уводит: тесты мутантов идут на SHA, разрешённом до плана."""
    repo, _, second = _два_коммита(tmp_path)
    журнал = _прогон(tmp_path, monkeypatch, None, секунды={"tests/test_mod.py": 1})
    monkeypatch.chdir(repo)
    настоящий = mc.plan_for

    def plan_for(root, rng, shard=None, max_n=None, only_critical=False):
        план = настоящий(root, rng, shard, max_n, only_critical)
        (repo / "src" / "mod.py").write_text("def f(x):\n    return x or 1\n", encoding="utf-8")
        subprocess.run([*_GIT, "commit", "-qam", "сдвиг"], cwd=repo, check=True)
        return план
    monkeypatch.setattr(mc, "plan_for", plan_for)
    ревизии = []
    прежний = mc.run_tests

    def run_tests(cwd, targets, timeout):
        ревизии.append(_rev(cwd, "HEAD"))
        return прежний(cwd, targets, timeout)
    monkeypatch.setattr(mc, "run_tests", run_tests)
    assert mc.main(["mutate_check.py", "--range", "HEAD~1...HEAD", "--timeout", "100"]) == 0
    assert журнал and set(ревизии) == {second}, (ревизии, second)
    assert _rev(repo, "HEAD") != second


def test_копия_встаёт_на_любой_коммит_источника(tmp_path):
    """(а) старая ревизия; (б) коммит отсоединённого HEAD вне веток; (в) коммит
    только в reflog; (г) источник — linked worktree; (д) неглубокий источник;
    (е) объекты у копии свои — `alternates` нет."""
    repo, first, second = _два_коммита(tmp_path)
    work = mc.copy_tree(repo, first, tmp_path / "а")
    assert (work / "src" / "mod.py").read_text(encoding="utf-8").endswith("return x\n")
    assert not (work / ".git" / "objects" / "info" / "alternates").exists()
    subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True)
    (repo / "src" / "mod.py").write_text("def f(x):\n    return 1\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "вне веток"], cwd=repo, check=True)
    вне = _rev(repo, "HEAD")
    assert (mc.copy_tree(repo, вне, tmp_path / "б") / "src" / "mod.py").read_text(encoding="utf-8").endswith("1\n")
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
    (repo / "src" / "mod.py").write_text("def f(x):\n    return 2\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "в reflog"], cwd=repo, check=True)
    в_reflog = _rev(repo, "HEAD")
    subprocess.run(["git", "reset", "-q", "--hard", second], cwd=repo, check=True)
    assert (mc.copy_tree(repo, в_reflog, tmp_path / "в") / "src" / "mod.py").read_text(encoding="utf-8").endswith("2\n")
    соседнее = tmp_path / "соседнее"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(соседнее), first], cwd=repo, check=True)
    assert mc.copy_tree(соседнее, second, tmp_path / "г").joinpath("src", "mod.py").read_text(
        encoding="utf-8").endswith("return not x\n")
    мелкий = tmp_path / "мелкий"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(мелкий)], check=True)
    assert mc.copy_tree(мелкий, second, tmp_path / "д").joinpath("src", "mod.py").exists()


def test_копия_на_неизвестный_коммит_отказ_подготовки(tmp_path):
    """Строка отказа несёт шаг и текст git как текст, а не байты: человек читает
    «fatal: …», а не `b'fatal: …'` (выживший мутатора `text=True → False`)."""
    repo, _, _ = _два_коммита(tmp_path)
    with pytest.raises(mc.PreparationError, match=r"git checkout — fatal: "):
        mc.copy_tree(repo, "0" * 40, tmp_path / "копия")


def _снимок(корень: pathlib.Path) -> dict[str, bytes]:
    return {str(p.relative_to(корень)): p.read_bytes() for p in sorted(корень.rglob("*")) if p.is_file()}


def _права(корень: pathlib.Path, запись: bool) -> None:
    for p in [корень, *корень.rglob("*")]:
        if p.is_symlink():
            continue
        mode = p.stat().st_mode
        p.chmod(mode | 0o200 if запись else mode & ~0o222)


def test_мутатор_не_пишет_в_репозиторий_источник(tmp_path, monkeypatch):
    """Сквозной прогон `main` на СВОЁМ репозитории теста, который на время прогона
    только на чтение: любой путь записи в источник — `Popen`, `os.system`, внуки,
    транзитная запись `worktree add` + `remove`, которой снимок «до/после» не
    видит, — падает. `git worktree prune` на чистом репозитории ничего не пишет,
    его ловит шпион по слову. После возврата прав содержимое — байт в байт."""
    repo, _, _ = _два_коммита(tmp_path)
    журнал = _прогон(tmp_path, monkeypatch, None, секунды={"tests/test_mod.py": 1})
    monkeypatch.chdir(repo)
    вызовы = []
    настоящий = subprocess.Popen

    class Шпион(настоящий):
        def __init__(self, args, *a, **kw):
            вызовы.append([str(x) for x in args] if not isinstance(args, (str, bytes)) else [str(args)])
            super().__init__(args, *a, **kw)
    monkeypatch.setattr(subprocess, "Popen", Шпион)
    до = _снимок(repo)
    _права(repo, запись=False)
    try:
        try:
            (repo / ".git" / "проба").write_text("", encoding="utf-8")
        except PermissionError:
            pass
        else:
            pytest.fail("запрет записи не держит (прогон под root?) — пин судить нечем")
        rc = mc.main(["mutate_check.py", "--range", "HEAD~1...HEAD", "--timeout", "100"])
    finally:
        _права(repo, запись=True)
    assert rc == 0 and журнал, (rc, журнал)
    git = [c for c in вызовы if c and pathlib.Path(c[0]).name == "git"]
    assert git and not [c for c in git if "worktree" in c], git
    assert _снимок(repo) == до


_MC = "tests/test_mutate_check.py"
_BS = "tests/test_busy_signals.py"


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("бюджет, прогонов", [(399.9, 0), (400, 1)])
def test_бюджет_не_хватает_на_базу(tmp_path, monkeypatch, capsys, force, бюджет, прогонов):
    """Набор базы ещё не мерили — нужен худший случай, 4 × --timeout = 400 с.
    Меньше — ни одного прогона, мутанты не запускаются; ровно 400 — база идёт,
    а мутант (замер набора 350 с) при остатке 50 уже не влезает. `--force` снимает гвард занятости, но не бюджет."""
    import exit_codes
    plan = _мутанты("scripts/mutate_check.py", 3)
    журнал = _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 350})
    отчёт = tmp_path / "отчёт.txt"
    argv = ["mutate_check.py", "--range", "HEAD", "--timeout", "100",
            "--budget-s", str(бюджет), "--report", str(отчёт)] + (["--force"] if force else [])
    # ни один мутант не судился — `unjudged`, в CI красный, а не жёлтый (DeepSeek по PR #637)
    assert mc.main(argv) == exit_codes.EXIT_UNJUDGED
    assert len(журнал) == прогонов, журнал
    out = capsys.readouterr().out
    assert "НЕ СУДИЛОСЬ: 3 (прервано: бюджет)" in out, out
    assert отчёт.read_text(encoding="utf-8").startswith(
        "Проверено мутантов: 0 из 3 (остановка: бюджет), выжило: 0"), отчёт.read_text(encoding="utf-8")


def test_бюджет_хватает_на_базу_и_k_мутантов(tmp_path, monkeypatch, capsys):
    """600 с: база 100 (нужно 400), затем мутанты по 100 — пока остаток не
    меньше замера набора. Третий идёт ровно на остатке 100, четвёртый — нет.
    Таймаут мутанта не урезается под остаток: при остатке 300…100 (меньше
    потолка набора 4 × 100) `run_tests` получает всё те же 100 — урезанный
    прогон истёк бы и засчитал мутанта «убитым» ложно."""
    import exit_codes
    plan = _мутанты("scripts/mutate_check.py", 6)
    журнал = _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 100})
    отчёт = tmp_path / "отчёт.txt"
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--timeout", "100",
                    "--max", "5", "--budget-s", "400", "--report", str(отчёт)]) == exit_codes.EXIT_PARTIAL
    assert len(журнал) == 1 + 3, журнал
    assert {t for _, t in журнал} == {100}, журнал
    текст = отчёт.read_text(encoding="utf-8")
    # выборка 5 из 6 — политика, а не неполнота: знаменатель — выборка (№469)
    assert текст.startswith("Проверено мутантов: 3 из 5 (остановка: бюджет), "
                            "выжило: 0\nВыборка: 5 из 6 "), текст
    assert "НЕ СУДИЛОСЬ: 2 (прервано: бюджет)" in текст, текст
    assert "⏹ бюджет — прерываюсь (3/5" in capsys.readouterr().out


def test_оценка_мутанта_это_замер_его_набора(tmp_path, monkeypatch):
    """Ключ базы и ключ мутанта совпадают, хоть `tests_for` и отдаёт новый список
    на каждый вызов. Наборы разной длины: база 10 + 30 при бюджете 60 оставляет
    20 — мутант набора в 10 с идёт, мутант набора в 30 с останавливает прогон.
    Оценка по чужому набору дала бы другой ответ, пропавшая — KeyError."""
    import exit_codes
    а, б = _мутанты("scripts/mutate_check.py", 2), _мутанты("src/busy_signals.py", 1)
    assert len(а) == 2 and len(б) == 1
    журнал = _прогон(tmp_path, monkeypatch, [а[0], б[0], а[1]], секунды={_MC: 10, _BS: 30})
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--timeout", "5",
                    "--budget-s", "60"]) == exit_codes.EXIT_PARTIAL
    assert [ts for ts, _ in журнал] == [(_BS,), (_MC,), (_MC,)], журнал
    # с запасом — каждый мутант судится своим набором, оценка нашлась всем
    журнал = _прогон(tmp_path, monkeypatch, [а[0], б[0], а[1]], секунды={_MC: 10, _BS: 30})
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--timeout", "5",
                    "--budget-s", "1000"]) == 0
    assert [ts for ts, _ in журнал] == [(_BS,), (_MC,), (_MC,), (_BS,), (_MC,)], журнал


def test_отчёт_на_диске_после_каждого_мутанта(tmp_path, monkeypatch):
    """Раннер обрывает job посреди плана: отчёт уже на диске и описывает двух
    проверенных — прежде он писался один раз в конце и пропадал целиком."""
    plan = _мутанты("scripts/mutate_check.py", 5)
    _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1}, падать_на=1 + 3)
    # каталога отчёта ещё нет: первая запись посреди прогона создаёт его сама
    отчёт = tmp_path / "артефакты" / "мутации" / "отчёт.txt"
    with pytest.raises(RuntimeError, match="раннер"):
        mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--report", str(отчёт)])
    текст = отчёт.read_text(encoding="utf-8")
    assert текст.startswith("Проверено мутантов: 2 из 5 (остановка: прогон не дошёл до конца "
                            "плана), выжило: 0"), текст
    assert "НЕ СУДИЛОСЬ: 3" in текст, текст


def test_отчёт_на_диске_и_во_время_базы(tmp_path, monkeypatch):
    """Раннер обрывает job на базе: отчёт уже на диске и говорит, что до мутантов не
    дошли, — прежде база, самый длинный этап, не оставляла ничего (DeepSeek по PR #640)."""
    plan = _мутанты("scripts/mutate_check.py", 3) + _мутанты("src/busy_signals.py", 1)
    _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1, _BS: 1}, падать_на=2)
    отчёт = tmp_path / "отчёт.txt"
    with pytest.raises(RuntimeError, match="раннер"):
        mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--report", str(отчёт)])
    assert отчёт.read_text(encoding="utf-8").startswith(
        "Проверено мутантов: 0 из 4 (остановка: базовый прогон не закончен), выжило: 0"), (
        отчёт.read_text(encoding="utf-8"))


def test_красная_база_оставляет_отчёт(tmp_path, monkeypatch):
    """База покраснела — исход 2 и отчёт с причиной, а не пустое место."""
    plan = _мутанты("scripts/mutate_check.py", 2)
    _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1})
    monkeypatch.setattr(mc, "run_tests", lambda cwd, targets, timeout: False)
    отчёт = tmp_path / "отчёт.txt"
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--report", str(отчёт)]) == 2
    assert отчёт.read_text(encoding="utf-8").startswith(
        "Проверено мутантов: 0 из 2 (остановка: база красная), выжило: 0"), отчёт.read_text(encoding="utf-8")


def test_без_бюджета_поведение_прежнее(tmp_path, monkeypatch, capsys):
    """Без `--budget-s` время не ограничивает ничего: часы уходят на годы вперёд,
    а судится весь план, и исход — «проверено всё, чисто»."""
    plan = _мутанты("scripts/mutate_check.py", 4)
    журнал = _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1e9})
    отчёт = tmp_path / "отчёт.txt"
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--report", str(отчёт)]) == 0
    assert len(журнал) == 1 + 4 and {t for _, t in журнал} == {120}, журнал
    assert отчёт.read_text(encoding="utf-8").startswith("Проверено мутантов: 4 из 4, выжило: 0\n")
    # машинная строка прогона без --shard — доля 1 из 1, вся выборка
    assert _строка(отчёт) == {"K": 1, "N": 1, "M": 4, "P": 4, "word": "ok"}
    out = capsys.readouterr().out
    # шапка прогона печатает корень данных — это путь tmp_path, а в нём имя теста
    assert "бюджет" not in out.replace(str(tmp_path), "")
    # счёт мутантов в строках прогона — с единицы и до конца плана
    assert "  [1/4] убит: " in out and "  [4/4] убит: " in out, out


def test_отчёт_не_судившихся_не_считает_неприменённых():
    """Из пяти: два проверено, один не применился — не судились двое. Не
    применившийся назван своей строкой и в «НЕ СУДИЛОСЬ» не входит."""
    m = _мутанты("scripts/mutate_check.py", 1)[0]
    текст = mc.render_report(mc.Facts(M=5, P=5, full=5, tested=2, skipped=1, aborted="бюджет"),
                             [(m, "узел не нашёлся на своём отрезке")])
    assert текст.startswith("Проверено мутантов: 2 из 5 (остановка: бюджет), выжило: 0"), текст
    assert "НЕ СУДИЛОСЬ: 2 (прервано: бюджет)" in текст, текст
    assert "НЕ ПРИМЕНИЛОСЬ: 1" in текст, текст


# --- Шарды плана и вердикт слияния (№441) ------------------------------------
# План делится на N долей, каждая судится своим job'ом, а вердикт читает их
# машинные строки. Ниже — репозиторий с правкой ровно на шесть мутантов: две
# строки кода добавлены, и на них ложатся `not`, `return`, `>`, `1`, `+`, `return`.

_БАЗА = "def f(a, b):\n    return a\n"
_ПРАВКА = "def f(a, b):\n    if not a:\n        return b > 1\n    return a + b\n"
_ДИАПАЗОН = "HEAD~1...HEAD"


def _репо_с_правкой(tmp_path: pathlib.Path, code: str = _ПРАВКА) -> pathlib.Path:
    """Git-репозиторий, где `src/mod.py` изменён последним коммитом."""
    repo = _git_repo(tmp_path, {"src/mod.py": _БАЗА})
    (repo / "src" / "mod.py").write_text(code, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)
    return repo


def test_шарды_не_пересекаются_и_покрывают_план(tmp_path):
    """Доли одного плана: каждая берёт свои индексы `i % N == K-1`, вместе —
    ровно план, и `planned` (P) у всех один."""
    repo = _репо_с_правкой(tmp_path)
    полный, итого = mc.plan_for(repo, _ДИАПАЗОН)
    assert len(полный) == 6 and итого.planned == 6

    куски = [mc.plan_for(repo, _ДИАПАЗОН, (k, 3)) for k in (1, 2, 3)]
    for k, (часть, t) in zip((1, 2, 3), куски):
        assert t.shard == (k, 3) and t.planned == 6
        ждём = [m for i, m in enumerate(полный) if i % 3 == k - 1]
        assert [(m.path, m.line, m.what) for m in часть] == \
               [(m.path, m.line, m.what) for m in ждём], k
    покрытие = collections.Counter((m.path, m.line, m.what)
                                   for часть, _ in куски for m in часть)
    assert покрытие == collections.Counter((m.path, m.line, m.what) for m in полный)


def test_порядок_сначала_выборка_потом_шард(tmp_path, monkeypatch, capsys):
    """Выборка — это план (№469): `--max 3` берёт три из шести, шард 3/3 — индекс
    2 выборки, один мутант. Обратный порядок (делить план, потом резать долю)
    отдал бы долям разные выборки: шард 3/3 взял бы индексы 2 и 5 полного плана."""
    repo = _репо_с_правкой(tmp_path)
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.chdir(repo)
    calls = {"n": 0}

    def run_tests(cwd, targets, timeout):
        calls["n"] += 1
        return calls["n"] == 1          # база зелёная, мутанты убиты

    monkeypatch.setattr(mc, "run_tests", run_tests)
    отчёт = tmp_path / "отчёт.txt"
    rc = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--shard", "3/3",
                  "--max", "3", "--timeout", "5", "--force", "--report", str(отчёт)])
    out = capsys.readouterr().out
    assert rc == 0, out[-600:]
    assert "Мутантов к проверке: 1 (выборка 3 из 6" in out, out
    data = json.loads(отчёт.with_name(отчёт.name + ".json").read_text(encoding="utf-8"))
    assert (data["K"], data["N"], data["M"], data["P"], data["full"], data["word"]) == \
           (3, 3, 1, 3, 6, "ok")


def test_пустой_шард_печатает_нечего_и_пишет_артефакты(tmp_path, monkeypatch, capsys):
    """Шард назван, полный план был (P > 0), а доле не досталось мутантов: это
    не «в диапазоне нечего». Отчёт и машинная строка нужны слиянию — иначе
    ΣM ≠ P и вердикт красный из-за арифметики, а не из-за дела."""
    import exit_codes
    repo = _репо_с_правкой(tmp_path)
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.chdir(repo)
    отчёт = tmp_path / "отчёт.txt"
    rc = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--shard", "7/7",
                  "--force", "--report", str(отчёт)])
    assert rc == exit_codes.EXIT_NOTHING_TO_CHECK
    out = capsys.readouterr().out
    assert "шард 7 из 7: 0 из 6 — нечего" in out, out
    assert отчёт.exists()
    assert _строка(отчёт) == {"K": 7, "N": 7, "M": 0, "P": 6, "word": "nothing"}


def test_shard_arg_берёт_только_верные_доли():
    """Границы доли: `1/1`, `1/N` и `N/N` — законны, `0/N`, `N+1/N`, `K/0` — нет.
    Выжившие мутанты круга 1 (`n < 1` → `n <= 1`, `1 <= k` → `1 < k`) держались
    ровно на отсутствии этих краёв."""
    import argparse
    for good, want in (("1/1", (1, 1)), ("1/4", (1, 4)), ("4/4", (4, 4)),
                       ("2/4", (2, 4)), (" 3 / 7 ", (3, 7))):
        assert mc._shard_arg(good) == want, good
    for плохой in ("0/4", "5/4", "a/b", "1/0", "0/0", "4", "1/", "/4", "2/4/6", "-1/2"):
        with pytest.raises(argparse.ArgumentTypeError):
            mc._shard_arg(плохой)


@pytest.mark.parametrize("плохой", ["0/4", "5/4", "a/b", "1/0", "4", "1/", "/4"])
def test_неверный_shard_отказ_до_работы(tmp_path, monkeypatch, capsys, плохой):
    """`0/4`, `5/4`, `a/b` отвергаются разбором аргументов до git-корня, дерева и
    лока: тест стоит вне git-репозитория, и дойди код до `git rev-parse` — была бы
    трассировка. Код — 2 от argparse, а не 5: пятёрка значит «корень данных не
    назван», и проба контракта запуска отличает её от опечатки во флаге."""
    import exit_codes
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as отказ:
        mc.main(["mutate_check.py", "--shard", плохой])
    assert отказ.value.code == 2 != exit_codes.EXIT_ROOT_UNNAMED
    assert "--shard" in capsys.readouterr().err


def test_max_arg_выборка_хотя_бы_из_одного():
    """`--max 1` — законная выборка, `0` и отрицательный — ошибка: пустая выборка
    непустого плана давала зелёное «нечего» (входной круг 2 по №469, Sonnet I2).
    Край `1` держит мутант `n < 1` → `n <= 1`."""
    import argparse
    assert mc._max_arg("1") == 1
    assert mc._max_arg("all") is None
    for bad in ("0", "-1"):
        with pytest.raises(argparse.ArgumentTypeError):
            mc._max_arg(bad)


def test_шард_при_пустом_диапазоне_пишет_строку_нечего(tmp_path, monkeypatch, capsys):
    """P = 0 (правка без python-строк) — прогон с шардом всё равно кладёт машинную
    строку: без неё вердикт видит «ни одного файла шарда» и краснеет на каждом PR
    без кода (выходной круг 1 по №441, DS C1). Слово — диагноз всего диапазона."""
    import exit_codes
    repo = _git_repo(tmp_path, {"README.md": "было\n"})
    (repo / "README.md").write_text("стало\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "только текст"], cwd=repo, check=True)
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.chdir(repo)
    отчёт = tmp_path / "отчёт-2.txt"
    rc = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--shard", "2/4", "--max", "all",
                  "--force", "--report", str(отчёт)])
    assert rc == exit_codes.EXIT_NOTHING_TO_CHECK
    assert _строка(отчёт) == {"K": 2, "N": 4, "M": 0, "P": 0, "word": "nothing"}
    assert "шард 2 из 4" not in capsys.readouterr().out, "P = 0 — диагноз диапазона, а не «пустая доля»"


def _шард_пустого_плана(tmp_path, monkeypatch, repo, k=1, n=4):
    """Прогон шарда K/N по последнему коммиту `repo` — код и машинная строка."""
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.chdir(repo)
    отчёт = tmp_path / f"отчёт-{k}.txt"
    rc = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--shard", f"{k}/{n}", "--max", "all",
                  "--force", "--report", str(отчёт)])
    return rc, _строка(отчёт)


def test_шард_при_плане_без_операторов_пишет_unmutable(tmp_path, monkeypatch):
    """Писатель пары «писатель/судья» для слепого пятна: правка из одного `if`
    (узлы есть, операторов нет) даёт шарду код `EXIT_UNMUTABLE` и слово
    `unmutable` в машинной строке — то самое, что судья при P = 0 читает
    предупреждением (выходной круг 2 по №441, DS M3)."""
    import exit_codes
    repo = _репо_с_правкой(tmp_path, "def f(a, b):\n    if a:\n        pass\n    return a\n")
    rc, строка = _шард_пустого_плана(tmp_path, monkeypatch, repo)
    assert rc == exit_codes.EXIT_UNMUTABLE
    assert строка == {"K": 1, "N": 4, "M": 0, "P": 0, "word": "unmutable"}


def test_шард_с_неразобранным_файлом_пишет_partial_и_вердикт_красный(tmp_path, monkeypatch):
    """Сквозь писателя и судью: правка, после которой файл не разбирается, —
    P = 0, но слово шарда `partial`, и вердикт четырёх таких строк красный, а не
    «мутировать нечего» (выходной круг 2 по №441, DS C1)."""
    import exit_codes
    repo = _репо_с_правкой(tmp_path, "def f(a, b):\n    return (a\n")
    rc, строка = _шард_пустого_плана(tmp_path, monkeypatch, repo)
    assert rc == exit_codes.EXIT_PARTIAL
    assert строка == {"K": 1, "N": 4, "M": 0, "P": 0, "word": "partial"}
    полная = json.loads(mc.shard_line_path(tmp_path / "отчёт-1.txt").read_text(encoding="utf-8"))
    каталог = tmp_path / "шарды"
    каталог.mkdir()
    for k in range(1, 5):
        (каталог / f"r{k}.txt{mc.SHARD_LINE_SUFFIX}").write_text(
            json.dumps({**полная, "K": k}), encoding="utf-8")
    assert mc.merge_shards(каталог) == 1


def test_вердикт_с_битым_файлом_показывает_прочитанные_шарды(tmp_path, capsys):
    """Один нечитаемый файл — красный с причиной, но три прочитанных шарда
    остаются в сводке: «шардов 0, M=0, P=0» при трёх отработавших врал бы
    читателю PR (выходной круг 2 по №441, DS M2)."""
    for k in range(1, 4):
        (tmp_path / f"r{k}.txt{mc.SHARD_LINE_SUFFIX}").write_text(json.dumps(_факт(k, 4, 2, 8, "ok")), encoding="utf-8")
    (tmp_path / f"r4.txt{mc.SHARD_LINE_SUFFIX}").write_text('{"K": 4, "N"', encoding="utf-8")
    assert mc.merge_shards(tmp_path) == 1
    out = capsys.readouterr().out
    assert "нечитаемый файл шарда" in out and "r4.txt" in out
    assert "итог: шардов 3, M=6, P=8" in out


def test_вердикт_при_пустом_диапазоне_не_краснеет(tmp_path):
    """P = 0: «нечего» — заметка, «строки есть, мутировать нечего» (слепое пятно,
    №386) — предупреждение, но не красный; «не прочитан файл» — красный."""
    def шарды(каталог, слово):
        каталог.mkdir()
        for k in range(1, 5):
            (каталог / f"r{k}.txt{mc.SHARD_LINE_SUFFIX}").write_text(json.dumps(_факт(k, 4, 0, 0, слово)), encoding="utf-8")
        return каталог
    assert mc.merge_shards(шарды(tmp_path / "a", "nothing")) == 0
    assert mc.merge_shards(шарды(tmp_path / "b", "unmutable")) == 0
    assert mc.merge_shards(шарды(tmp_path / "c", "partial")) == 1
    # оба «нечего» сразу — тоже слепое пятно, а не неполный исход
    смесь = шарды(tmp_path / "d", "nothing")
    for k in (3, 4):
        (смесь / f"r{k}.txt{mc.SHARD_LINE_SUFFIX}").write_text(json.dumps(_факт(k, 4, 0, 0, "unmutable")), encoding="utf-8")
    assert mc.merge_shards(смесь) == 0


def test_вердикт_пишет_отчёт_и_в_новый_каталог(tmp_path):
    """Каталог отчёта слияния создаётся вместе с родителями: артефакт CI
    раскладывается по вложенным путям, которых до шага нет."""
    d = tmp_path / "шарды"
    d.mkdir()
    (d / f"r1.txt{mc.SHARD_LINE_SUFFIX}").write_text(json.dumps(_факт(1, 1, 2, 2, "ok")), encoding="utf-8")
    отчёт = tmp_path / "новый" / "вложенный" / "вердикт.txt"
    assert mc.merge_shards(d, отчёт) == 0
    assert "шарды чисты" in отчёт.read_text(encoding="utf-8")


def test_вердикт_без_файлов_называет_причину(tmp_path, capsys):
    """Пустой каталог артефактов — красный с причиной «ни одного файла шарда»,
    а не «разные N» из пустого множества."""
    (tmp_path / "пусто").mkdir()
    assert mc.merge_shards(tmp_path / "пусто") == 1
    assert "ни одного файла шарда" in capsys.readouterr().out


def test_шард_с_выборкой_судит_выборку_целиком(tmp_path, monkeypatch):
    """Шард 1/1 с `--max 3` при плане из шести: выборка — это план (№469), доля —
    три мутанта, судились все — `ok`, а полный план назван отдельным полем. До
    №469 это был срез и слово `partial` (выходной круг 1 по №441, DS M4)."""
    repo = _репо_с_правкой(tmp_path)
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.chdir(repo)
    calls = {"n": 0}

    def run_tests(cwd, targets, timeout):
        calls["n"] += 1
        return calls["n"] == 1          # база зелёная, мутанты убиты

    monkeypatch.setattr(mc, "run_tests", run_tests)
    отчёт = tmp_path / "отчёт.txt"
    mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--shard", "1/1", "--max", "3",
             "--timeout", "5", "--force", "--report", str(отчёт)])
    data = json.loads(отчёт.with_name(отчёт.name + mc.SHARD_LINE_SUFFIX).read_text(encoding="utf-8"))
    assert (data["M"], data["P"], data["full"], data["word"]) == (3, 3, 6, "ok")


def test_потолок_равный_плану_не_переставляет_мутантов(tmp_path, monkeypatch, capsys):
    """План ровно в потолок — среза нет, и порядок прогона — порядок плана. Срез
    раскладывает мутантов по кругу между файлами; `>=` вместо `>` включал бы его
    на плане, который резать не нужно (выживший мутант круга 1 по №441)."""
    repo = _git_repo(tmp_path, {"src/a.py": _БАЗА, "src/b.py": _БАЗА})
    (repo / "src" / "a.py").write_text(_ПРАВКА, encoding="utf-8")
    (repo / "src" / "b.py").write_text(_ПРАВКА, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "две правки"], cwd=repo, check=True)
    план, _ = mc.plan_for(repo, _ДИАПАЗОН)
    assert {m.path.name for m in план} == {"a.py", "b.py"} and len(план) > 2
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.chdir(repo)
    calls = {"n": 0}

    def run_tests(cwd, targets, timeout):
        calls["n"] += 1
        return calls["n"] == 1

    monkeypatch.setattr(mc, "run_tests", run_tests)
    mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--max", str(len(план)),
             "--timeout", "5", "--force"])
    out = capsys.readouterr().out
    assert "СРЕЗАНО" not in out
    прогон = re.findall(r"\] убит: (src/[ab]\.py:\d+)", out)
    assert прогон == [f"src/{m.path.name}:{m.line}" for m in план], out[-800:]


def test_max_all_снимает_потолок(tmp_path, monkeypatch, capsys):
    """`--max all` — без среза: план длиннее умолчания (60) судится целиком."""
    plan = _мутанты("scripts/mutate_check.py", 70)
    assert len(plan) == 70
    _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1})
    отчёт = tmp_path / "отчёт.txt"
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--max", "all",
                    "--report", str(отчёт)]) == 0
    out = capsys.readouterr().out
    assert "СРЕЗАНО" not in out and "Мутантов к проверке: 70" in out, out
    data = json.loads(отчёт.with_name(отчёт.name + ".json").read_text(encoding="utf-8"))
    assert (data["M"], data["P"], data["N"]) == (70, 70, 1)


def test_умолчание_max_остаётся_60(tmp_path, monkeypatch, capsys):
    """Без `--max` выборка — 60 (решение владельца 30.09): из 70 судятся 60, и
    полный план назван вслух; не взятое выборкой — политика, исход чистый."""
    import exit_codes
    plan = _мутанты("scripts/mutate_check.py", 70)
    _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1})
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force"]) == 0
    out = capsys.readouterr().out
    assert "Мутантов к проверке: 60 (выборка 60 из 70" in out, out
    assert exit_codes.EXIT_PARTIAL != 0   # выборка — не срез: судилась вся, исход чистый


def test_json_пишется_при_красной_базе(tmp_path, monkeypatch):
    """Красная база — тоже исход, и он должен доехать машинной строкой: слово
    `fail`, а не пустое место рядом с отчётом."""
    plan = _мутанты("scripts/mutate_check.py", 2)
    _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1})
    monkeypatch.setattr(mc, "run_tests", lambda cwd, targets, timeout: False)
    отчёт = tmp_path / "отчёт.txt"
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force",
                    "--report", str(отчёт)]) == 2
    data = json.loads(отчёт.with_name(отчёт.name + ".json").read_text(encoding="utf-8"))
    assert (data["word"], data["M"], data["P"]) == ("fail", 2, 2)


def test_json_пишется_при_обрыве_по_бюджету(tmp_path, monkeypatch):
    """Бюджет оборвал прогон до первого мутанта — машинная строка говорит
    `unjudged`: слияние не примет это за покрытие."""
    import exit_codes
    plan = _мутанты("scripts/mutate_check.py", 3)
    _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 350})
    отчёт = tmp_path / "отчёт.txt"
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--timeout", "100",
                    "--budget-s", "399.9", "--force", "--report", str(отчёт)]) == \
        exit_codes.EXIT_UNJUDGED
    data = json.loads(отчёт.with_name(отчёт.name + ".json").read_text(encoding="utf-8"))
    assert (data["word"], data["M"], data["P"]) == ("unjudged", 3, 3)


def test_merge_шардов_зелёный_и_пишет_таблицу(tmp_path, capsys):
    """Полное покрытие и все `ok` — зелёный; таблица (K, M, слово) идёт и в
    вывод, и в файл `--report`."""
    d = tmp_path / "шарды"
    d.mkdir()
    _шард(d, "a.json", 1, 2, 3, 6, "ok")
    _шард(d, "b.json", 2, 2, 3, 6, "ok")
    отчёт = tmp_path / "вердикт.txt"
    assert mc.merge_shards(d, отчёт) == 0
    out = capsys.readouterr().out
    assert "шард 1 из 2: M=3, судилось 3 — ok" in out and "шард 2 из 2: M=3, судилось 3 — ok" in out, out
    assert "итог: шардов 2, M=6, P=6" in out and "шарды чисты" in out, out
    assert отчёт.read_text(encoding="utf-8") == out


def test_merge_шардов_nothing_при_своём_m_ноль_зелёный(tmp_path, capsys):
    """Шард без мутантов (`nothing`, M = 0) среди чистых — не красный: покрытие
    сходится, просто делить было нечего."""
    d = tmp_path / "шарды"
    d.mkdir()
    _шард(d, "a.json", 1, 2, 5, 5, "ok")
    _шард(d, "b.json", 2, 2, 0, 5, "nothing")
    assert mc.merge_shards(d) == 0
    assert "шарды чисты" in capsys.readouterr().out


def test_merge_шардов_p0_все_nothing_заметка(tmp_path, capsys):
    """P = 0 и все шарды — `nothing`: мутировать нечего, зелёный с заметкой."""
    d = tmp_path / "шарды"
    d.mkdir()
    _шард(d, "a.json", 1, 2, 0, 0, "nothing")
    _шард(d, "b.json", 2, 2, 0, 0, "nothing")
    assert mc.merge_shards(d) == 0
    assert "мутировать нечего" in capsys.readouterr().out


@pytest.mark.parametrize("ряды,фраза", [
    ([], "шарды не покрыли план"),                                   # ни одного файла
    ([(1, 2, 6, 6, "ok")], "шарды не покрыли план"),                 # файлов 1, а шардов 2
    ([(1, 2, 3, 6, "ok"), (2, 3, 3, 6, "ok")], "разные N"),
    ([(1, 2, 3, 6, "ok"), (1, 2, 3, 6, "ok")], "повтор или пропуск K"),
    ([(1, 2, 2, 6, "ok"), (2, 2, 2, 6, "ok")], "ΣM ≠ P"),
    ([(1, 2, 3, 6, "ok"), (2, 2, 3, 5, "ok")], "разные выборки"),    # разные P
    ([(1, 2, 3, 6, "partial"), (2, 2, 3, 6, "ok")], "неполный исход"),
    ([(1, 2, 3, 6, "unjudged"), (2, 2, 3, 6, "ok")], "неполный исход"),
    ([(1, 2, 4, 6, "ok"), (2, 2, 2, 6, "nothing")], "неполный исход"),  # nothing с M > 0
    # P > 0 и `unmutable` у шарда — красный; при P = 0 это слепое пятно всего
    # диапазона, предупреждение (test_вердикт_при_пустом_диапазоне_не_краснеет)
    ([(1, 2, 3, 6, "ok"), (2, 2, 3, 6, "unmutable")], "неполный исход"),
    ([(1, 1, 1, 1, "fail")], "держит мерж: выжили мутанты в критичных зонах — 1"),
])
def test_merge_шардов_красный(tmp_path, capsys, ряды, фраза):
    """Каждая строка таблицы вердикта — своим случаем: покрытие (число файлов,
    N, набор K, ΣM), затем исходы шардов, кроме чистого."""
    d = tmp_path / "шарды"
    d.mkdir()
    for i, (k, n, m, p, word) in enumerate(ряды):
        _шард(d, f"{i}.json", k, n, m, p, word)
    assert mc.merge_shards(d) == 1
    assert фраза in capsys.readouterr().out


@pytest.mark.parametrize("ряды,строка", [
    # план есть: неполный — `partial` у первого шарда, чистый второй в перечень не входит
    ([(1, 2, 3, 6, "partial"), (2, 2, 3, 6, "ok")], "шарды дали неполный исход: шард 1: partial"),
    # плана нет у всего диапазона, но один шард не прочитал файл — перечень тот же
    ([(1, 2, 0, 0, "nothing"), (2, 2, 0, 0, "partial")], "шарды дали неполный исход: шард 2: partial"),
    # критичный выживший — своей фразой и раньше неполноты: он держит мерж при любой полноте
    ([(1, 2, 3, 6, "fail"), (2, 2, 3, 6, "partial")],
     "держит мерж: выжили мутанты в критичных зонах — 1"),
    ([(1, 2, 3, 6, "ok"), (2, 2, 3, 6, "fail")],
     "держит мерж: выжили мутанты в критичных зонах — 1"),
])
def test_merge_шардов_неполный_исход_называет_шард_и_его_слово(tmp_path, capsys, ряды, строка):
    """Первая строка вердикта называет КАЖДЫЙ нечистый шард его номером и словом:
    человек идёт в отчёт именно этого шарда. Номер без слова (или слово номера)
    в сводке не говорит, что случилось (ночной мутатор 28.09 — два выживших)."""
    d = tmp_path / "шарды"
    d.mkdir()
    for i, (k, n, m, p, word) in enumerate(ряды):
        _шард(d, f"{i}.json", k, n, m, p, word)
    assert mc.merge_shards(d) == 1
    assert capsys.readouterr().out.splitlines()[0] == строка


def test_merge_шардов_битый_файл_красный(tmp_path, capsys):
    d = tmp_path / "шарды"
    d.mkdir()
    (d / "x.json").write_text("{не json", encoding="utf-8")
    assert mc.merge_shards(d) == 1
    assert "шарды не покрыли план" in capsys.readouterr().out


def test_merge_shards_работает_вне_git_дерева(tmp_path, monkeypatch, capsys):
    """Режим слияния разбирается до `git rev-parse`: job вердикта стоит на голом
    каталоге артефактов, вне git-дерева."""
    d = tmp_path / "шарды"
    d.mkdir()
    _шард(d, "a.json", 1, 1, 2, 2, "ok")
    monkeypatch.chdir(tmp_path)
    assert mc.main(["mutate_check.py", "--merge-shards", str(d)]) == 0
    assert "шарды чисты" in capsys.readouterr().out


# --- `--jobs N`: доли плана параллельно (№444 B) -----------------------------
# Родитель запускает N обычных мутаторов `--shard k/N --max all` на диапазоне,
# разрешённом в SHA один раз, и сводит их `merge_shards`. Замок мутатора
# разделяемый: доли и соседние прогоны держат его вместе.


def test_jobs_arg_границы():
    import argparse
    assert mc._jobs_arg("1") == 1
    assert mc._jobs_arg(str(mc.JOBS_MAX)) == mc.JOBS_MAX
    for плохой in ("0", str(mc.JOBS_MAX + 1), "два", "-1", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            mc._jobs_arg(плохой)


@pytest.mark.parametrize("флаги, слово", [
    (["--jobs", "2", "--shard", "1/4", "--max", "all"], "--shard"),
    (["--jobs", "2", "--max", "all", "--merge-shards", "d"], "--merge-shards"),
    (["--jobs", "2", "--merge-shards", "d"], "--merge-shards"),   # до раннего возврата слияния
])
def test_jobs_несочетаемые_флаги_отказ_кодом_2(tmp_path, monkeypatch, capsys, флаги, слово):
    """Отказ — кодом 2 argparse и до git-корня: тест стоит вне git-репозитория, и
    дойди код до `git rev-parse` или слияния — был бы другой исход."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as отказ:
        mc.main(["mutate_check.py", *флаги])
    assert отказ.value.code == 2
    assert слово in capsys.readouterr().err


def test_jobs_с_конечной_выборкой_законен():
    """Выборка — это план (№469): `--jobs 2 --max 5` и `--jobs 2` с умолчанием 60
    разбором принимаются — доли строят ту же выборку и берут из неё свои индексы."""
    ap = mc.build_parser()
    for флаги in (["--jobs", "2", "--max", "5"], ["--jobs", "2"]):
        args = ap.parse_args(флаги)
        mc.check_pair(ap, args)
        assert args.jobs == 2


def test_jobs_1_проверку_пары_не_включает():
    """`--jobs 1` — сегодняшний путь: доля и конечный потолок с ним законны."""
    ap = mc.build_parser()
    for флаги in (["--shard", "1/4", "--max", "5"], ["--merge-shards", "d"], []):
        assert mc.check_pair(ap, ap.parse_args(флаги)) is None


def test_каждый_флаг_либо_пересылается_долям_либо_родительский():
    """Новый флаг без решения «пересылать ли долям» краснеет здесь, а не теряется
    у долей молча."""
    dests = {a.dest for a in mc.build_parser()._actions} - {"help"}
    assert not set(mc.CHILD_FORWARDED) & set(mc.PARENT_ONLY)
    assert dests == set(mc.CHILD_FORWARDED) | set(mc.PARENT_ONLY), dests


@pytest.mark.parametrize("флаги, хвост", [
    ([], []),
    (["--budget-s", "300", "--force"], ["--budget-s", "300.0", "--force"]),
])
def test_child_argv_таблицей(tmp_path, флаги, хвост):
    """Доля — обычный мутатор этого же скрипта: SHA-диапазон родителя, `--timeout`,
    пересылаемые флаги, `--max all`, своя доля и свой отчёт в каталоге журналов."""
    ap = mc.build_parser()
    args = ap.parse_args(["--range", "main...HEAD", "--timeout", "7", "--jobs", "3",
                          "--max", "all", "--report", "сводка.txt", *флаги])
    argv = mc.child_argv(args, 2, 3, tmp_path, "aaa...bbb")
    assert argv == [sys.executable, str(REPO / "scripts" / "mutate_check.py"),
                    "--range", "aaa...bbb", "--timeout", "7", *хвост,
                    "--max", "all", "--shard", "2/3", "--report", str(tmp_path / "2.txt")]
    # аргументы доли сами проходят разбор и проверку пары
    доля = ap.parse_args(argv[2:])
    assert mc.check_pair(ap, доля) is None and доля.jobs == 1 and доля.shard == (2, 3)


_ТЕСТ_МОДУЛЯ = (
    "import pathlib, sys\n"
    "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))\n"
    "from mod import f\n\n\n"
    "def test_f():\n"
    "    assert f(1, 2) == 3\n"
    "    assert f(0, 5) is True\n")


def _репо_с_тестом(tmp_path: pathlib.Path, тест: str = _ТЕСТ_МОДУЛЯ,
                   зоны: dict | None = None) -> pathlib.Path:
    """Репозиторий с правкой на шесть мутантов и своим тестом модуля: тест убивает
    четырёх, `>` → `>=` и замена единицы выживают (граница b = 1 не проверена).
    `зоны` — `mutation_critical` в `layout.json` базы (None — файла нет)."""
    files = {"src/mod.py": _БАЗА, "tests/test_mod.py": тест}
    if зоны is not None:
        files[mc.ZONES_REL] = json.dumps({"mutation_critical": зоны})
    repo = _git_repo(tmp_path, files)
    (repo / "src" / "mod.py").write_text(_ПРАВКА, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)
    return repo


def _выжившие(текст: str) -> set[str]:
    return {line.strip() for line in текст.splitlines() if line.strip().startswith("ВЫЖИЛ ")}


def test_jobs_2_находит_тех_же_выживших_что_последовательный(tmp_path, monkeypatch, capsys):
    """Настоящие процессы: доли — живые мутаторы со своими копиями и pytest. Выжившие
    и код — те же, что у последовательного `--max all`; код — политика CI 0/1."""
    import exit_codes
    repo = _репо_с_тестом(tmp_path, зоны={"mod": "дверь теста"})
    данные = tmp_path / "данные"
    данные.mkdir()
    monkeypatch.setenv("CHAROITE_ROOT", str(данные))
    monkeypatch.chdir(repo)
    подряд = tmp_path / "подряд.txt"
    rc_подряд = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--max", "all",
                         "--timeout", "60", "--report", str(подряд)])
    out = capsys.readouterr().out
    assert rc_подряд == 1 and exit_codes.outcome(rc_подряд) == "fail", out[-800:]
    ждём = _выжившие(подряд.read_text(encoding="utf-8"))
    assert len(ждём) == 2, ждём

    сводка = tmp_path / "сводка.txt"
    rc = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--jobs", "2", "--max", "all",
                  "--timeout", "60", "--report", str(сводка)])
    out = capsys.readouterr().out
    assert rc == 1, out[-1500:]
    журналы = pathlib.Path(re.search(r"журналы и отчёты — (\S+)", out).group(1))
    отчёты = sorted(журналы.glob("[0-9].txt"))
    assert [p.name for p in отчёты] == ["1.txt", "2.txt"], out[-1500:]
    # долей ровно N и нумерация с единицы: лишняя доля 0 оставила бы свой журнал
    assert sorted(p.name for p in журналы.glob("*.log")) == ["1.log", "2.log"], out[-1500:]
    # у каждой доли свой процесс: строка запуска называет pid этой доли, а не первой
    pids = dict(re.findall(r"доля (\d+)/2: pid (\d+)", out))
    assert set(pids) == {"1", "2"} and pids["1"] != pids["2"], out[-1500:]
    нашли = set().union(*(_выжившие(p.read_text(encoding="utf-8")) for p in отчёты))
    assert нашли == ждём
    текст = сводка.read_text(encoding="utf-8")
    assert текст.startswith("держит мерж: выжили мутанты в критичных зонах — 2"), текст
    assert "итог: шардов 2, M=6, P=6" in текст, текст
    # после сводки — код каждой доли и где её журнал: номер строки — номер доли
    for k in (1, 2):
        assert re.search(rf"доля {k}: код \d+, журнал {re.escape(str(журналы / f'{k}.log'))}", out), out[-1500:]
    assert "доля 1: код 1" in out or "доля 2: код 1" in out, out[-1500:]
    import busy_signals
    assert not busy_signals.mutation_running(данные)
    shutil.rmtree(журналы)


def test_jobs_родитель_без_замка_отказывает_кодом_3_и_долей_не_запускает(tmp_path, monkeypatch, capsys):
    """Замок родителя — первым делом: не взялся — нейтральная строка и код занятости,
    ни одной доли и ни одного каталога журналов."""
    import busy_signals
    monkeypatch.setattr(busy_signals.MutationLock, "acquire", lambda self: False)
    spawned = []
    monkeypatch.setattr(mc.subprocess, "Popen", lambda *a, **kw: spawned.append(a))
    made = []
    monkeypatch.setattr(mc.tempfile, "mkdtemp", lambda *a, **kw: made.append(a))
    args = mc.build_parser().parse_args(["--range", "a...b", "--jobs", "2", "--max", "all"])
    assert mc.run_jobs(args, "a...b", tmp_path) == 3
    assert mc.LOCK_REFUSED in capsys.readouterr().out
    assert spawned == [] and made == []


def test_jobs_доля_родителем_не_становится(tmp_path, monkeypatch, capsys):
    """Доля с теми аргументами, что ей чеканит `child_argv`, попав в `run_jobs`, —
    отказ кодом 2 до замка: ни замка, ни каталога журналов, ни одной доли. Мутант
    входа (`jobs > 1` → `>= 1`) иначе делал каждую долю родителем, и цепочка
    мутаторов в новых сессиях росла мимо убийства прогона (PR #667, доля 1 в CI)."""
    import busy_signals
    замки = []
    monkeypatch.setattr(busy_signals.MutationLock, "acquire",
                        lambda self: замки.append(self) or True)
    spawned = []
    monkeypatch.setattr(mc.subprocess, "Popen", lambda *a, **kw: spawned.append(a))
    made = []
    monkeypatch.setattr(mc.tempfile, "mkdtemp", lambda *a, **kw: made.append(a))
    ap = mc.build_parser()
    родитель = ap.parse_args(["--range", "a...b", "--jobs", "2", "--max", "all"])
    доля = ap.parse_args(mc.child_argv(родитель, 1, 2, tmp_path, "a...b")[2:])
    assert mc.run_jobs(доля, "a...b", tmp_path) == 2
    assert mc.SHARE_REFUSED in capsys.readouterr().out
    assert spawned == [] and made == [] and замки == []


def test_jobs_sigterm_родителя_останавливает_доли_их_finally(tmp_path):
    """SIGTERM родителю: каждая доля получает SIGINT и в своём `finally` убирает
    копию; замок мутатора после — свободен, родитель выходит кодом 128 + 15. Всё —
    быстрее запаса `CHILD_STOP_GRACE_S`: SIGKILL не понадобился."""
    import busy_signals
    import signal
    import time
    медленный = _ТЕСТ_МОДУЛЯ + "\n\ndef test_долго():\n    import time\n    time.sleep(60)\n"
    repo = _репо_с_тестом(tmp_path, медленный)
    данные, врем = tmp_path / "данные", tmp_path / "tmp"
    данные.mkdir()
    врем.mkdir()
    вывод = tmp_path / "родитель.log"
    env = {**os.environ, "CHAROITE_ROOT": str(данные), "TMPDIR": str(врем),
           "PYTHONUNBUFFERED": "1"}
    with вывод.open("w") as out:
        родитель = subprocess.Popen(
            [sys.executable, str(REPO / "scripts" / "mutate_check.py"), "--range", _ДИАПАЗОН,
             "--jobs", "2", "--max", "all", "--timeout", "120"],
            cwd=repo, env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        копии = []
        срок = time.monotonic() + 60
        while len(копии) < 2 and time.monotonic() < срок and родитель.poll() is None:
            time.sleep(0.2)
            копии = [d for d in врем.glob("mutate-*")
                     if not d.name.startswith("mutate-jobs-") and (d / "tree").is_dir()]
        assert len(копии) == 2, вывод.read_text(encoding="utf-8")
        assert busy_signals.mutation_running(данные)
        time.sleep(1.5)                 # доли внутри базового прогона своего набора
        t0 = time.monotonic()
        родитель.send_signal(signal.SIGTERM)
        rc = родитель.wait(timeout=mc.CHILD_STOP_GRACE_S + 30)
        прошло = time.monotonic() - t0
    finally:
        if родитель.poll() is None:
            родитель.kill()
            родитель.wait()
    текст = вывод.read_text(encoding="utf-8")
    assert rc == 128 + signal.SIGTERM, текст
    assert прошло < mc.CHILD_STOP_GRACE_S, (прошло, текст)
    assert not any(d.exists() for d in копии), "доля не убрала свою копию"
    assert not busy_signals.mutation_running(данные)
    журналы = pathlib.Path(re.search(r"журналы и отчёты — (\S+)", текст).group(1))
    for k in (1, 2):
        assert "KeyboardInterrupt" in (журналы / f"{k}.log").read_text(encoding="utf-8"), k
    # итог не потерян (№469): строка «прервано: N из M» и ключ для продолжения, а
    # журнал рассуждённых каждой доли — в корне данных, а не во временном каталоге
    строка = re.search(r"прервано сигналом 15: (\d+) из 6 — продолжить: --resume (\w+)", текст)
    assert строка, текст[-1500:]
    for k in (1, 2):
        журнал = данные / "logs" / f"mutation_run-{строка.group(2)}-{k}.jsonl"
        assert json.loads(журнал.read_text(encoding="utf-8").splitlines()[0])["manifest"] == \
            mc.FACTS_VERSION, k


def test_обработчик_остановки_сначала_глушит_оба_сигнала():
    """Второй сигнал в зазоре между первым и остановкой долей не должен поднять второй
    `_Stopped` изнутри `except`: обработчик глушит SIGINT и SIGTERM ДО `raise`
    (выходной круг 1 по №444 B, I1)."""
    import signal
    прежние = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        with pytest.raises(mc._Stopped) as стоп:
            mc._raise_stopped(signal.SIGTERM, None)
        assert стоп.value.signum == signal.SIGTERM
        assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN
    finally:
        for s, h in прежние.items():
            signal.signal(s, h)


def test_stop_children_добивает_глухую_к_sigint_группу(tmp_path):
    """Доля, не отпустившая SIGINT за запас, получает SIGKILL всей группой."""
    import signal
    import time
    глухая = subprocess.Popen(
        [sys.executable, "-c",
         "import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN); "
         "print('готова', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert глухая.stdout.readline().strip() == "готова"
        t0 = time.monotonic()
        mc.stop_children([глухая], grace=0.5)
        assert time.monotonic() - t0 < 10
        assert глухая.returncode == -signal.SIGKILL
    finally:
        if глухая.poll() is None:
            глухая.kill()
            глухая.wait()
        глухая.stdout.close()



# --- №469: личность мутанта, выборка, зоны, журнал и продолжение ---------------


def test_ключ_мутанта_не_зависит_от_номера_строки(tmp_path):
    """Сдвиг строк (комментарий и пустые строки сверху) не меняет ключи: личность —
    путь, qualname, описание, канонический текст и номер среди одинаковых, без
    строки. Прежняя личность (строка и отрезок) меняла набор выборки на каждом
    пуше со сдвигом (история #616)."""
    code = "def f(a, b):\n    if not a:\n        return b > 1\n    return a + b\n"
    f = tmp_path / "m.py"
    f.write_text(code, encoding="utf-8")
    было = mc.scan(f, set(range(1, 6)), rel="src/m.py").mutations
    сдвиг = "# шапка\n\n\n" + code
    стало = mc.scan(f, set(range(1, 9)), сдвиг, rel="src/m.py").mutations
    assert [m.key for m in было] == [m.key for m in стало]
    assert [m.line + 3 for m in было] == [m.line for m in стало]
    assert len({m.key for m in было}) == len(было), "ключи различимы"
    # одинаковые выражения в одной функции различимы номером
    близнецы = mc.scan(f, {2, 3}, "def g(x):\n    y = x == 1\n    z = x == 1\n",
                       rel="src/m.py").mutations
    assert [m.key.rsplit("#", 1)[1] for m in близнецы if m.bare() == "Eq → NotEq"] == ["0", "1"]


def test_номер_близнеца_считается_по_всему_файлу_а_не_по_диапазону(tmp_path):
    """Второй из двух одинаковых `x == 1` в диапазоне, первый — нет: номер всё
    равно 1. Счёт только по строкам диапазона дал бы 0 — ключ зависел бы от того,
    какую из одинаковых строк правил коммит (входной круг 1 по №469, Sonnet I1)."""
    src = "def g(x):\n    y = x == 1\n    z = x == 1\n"
    f = tmp_path / "m.py"
    только_второй = mc.scan(f, {3}, src, rel="src/m.py").mutations
    eq = [m for m in только_второй if m.bare() == "Eq → NotEq"]
    assert len(eq) == 1 and eq[0].key.endswith("#1"), eq[0].key


def test_канон_ключа_не_зависит_от_версии_python():
    """Эталон — литерал, а не пересчёт тем же интерпретатором: `ast.dump` на 3.12
    и 3.13+ разный (пустые поля), а ключ уходит в журнал и в выборку CI (3.12)
    и машины владельца (входной круг 1 по №469, Sonnet C2)."""
    import ast
    assert mc.canon(ast.parse("def f(): pass").body[0]) == \
        "(FunctionDef name=str:'f' args=(arguments) body=[(Pass)])"
    assert mc.canon(ast.parse("a == None").body[0].value) == \
        "(Compare left=(Name id=str:'a' ctx=(Load)) ops=[(Eq)] comparators=[(Constant value=NoneType:None)])"


class _М:
    """Мутант для выборки: путь, ключ, критичность."""

    def __init__(self, path, key, critical):
        self.path, self.key, self.critical = pathlib.Path(path), key, critical

    @property
    def rank(self):
        return mc.Mutation.rank.fget(self)


def test_выборка_критичные_все_некритичным_пол():
    """Критичные судятся все — потолок их не режет (решение главной по выходному
    кругу 1 №469: иначе большая правка критичной зоны судилась бы частью при
    зелёном вердикте); некритичные добивают до 60, но не меньше пола 15."""
    крит = [_М("src/privacy.py", f"k{i}", True) for i in range(50)]
    прочие = [_М(f"src/x{j}.py", f"n{j}-{i}", False) for j in range(2) for i in range(10)]
    взяли = mc.select(крит + прочие, 60)
    assert sum(m.critical for m in взяли) == 50 and sum(not m.critical for m in взяли) == 15
    # большая критичная правка: все сто критичных и пол некритичным
    много = [_М("src/audio.py", f"a{i}", True) for i in range(100)]
    взяли = mc.select(много + прочие, 60)
    assert sum(m.critical for m in взяли) == 100 and sum(not m.critical for m in взяли) == 15
    # мало критичных — некритичные добивают до 60
    взяли = mc.select(крит[:10] + прочие * 1 + [_М("src/y.py", f"y{i}", False) for i in range(60)], 60)
    assert len(взяли) == 60 and sum(m.critical for m in взяли) == 10
    # некритичных меньше пола — берутся все
    взяли = mc.select(крит + прочие[:5], 60)
    assert sum(m.critical for m in взяли) == 50 and sum(not m.critical for m in взяли) == 5
    # поровну по файлам внутри слоя
    взяли = mc.select(крит + прочие, 60)
    by = collections.Counter(m.path.name for m in взяли if not m.critical)
    assert set(by.values()) <= {7, 8} and sum(by.values()) == 15, by
    # план не длиннее выборки — берётся целиком, в порядке плана
    assert mc.select(прочие, 60) == прочие


def test_выборка_новый_мутант_вытесняет_только_по_хешу():
    """Новый мутант в файле очереди вытесняет из выборки не больше одного старого:
    выборка остальных — подмножество прежней (устойчивость между пушами)."""
    файл = [_М("src/a.py", f"a{i}", False) for i in range(30)]
    было = {m.key for m in mc.select(файл, 10)}
    стало = {m.key for m in mc.select(файл + [_М("src/a.py", "новый", False)], 10)}
    assert len(стало - было - {"новый"}) == 0 and len(было - стало) <= 1


def _репо_с_тестами(tmp_path: pathlib.Path) -> pathlib.Path:
    """База — пустой модуль; правка — двадцать разных сравнений, тест модуля отдельно."""
    repo = _git_repo(tmp_path, {"src/mod.py": "X = 0\n", "tests/test_mod.py": "def test_x():\n    pass\n"})
    тело = "".join(f"def f{i}(a):\n    return a > {i + 1}\n" for i in range(20))
    (repo / "src" / "mod.py").write_text(тело, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)
    return repo


def test_правка_только_теста_и_сдвиг_строк_не_меняют_выборку(tmp_path):
    """Опыт №469: выборка 7 из 60 мутантов; коммит, который правит только тест, —
    та же выборка; коммит, который вставляет комментарий в начало файла выборки
    (строки сдвигаются), — та же выборка на нетронутом коде."""
    repo = _репо_с_тестами(tmp_path)
    база = _rev(repo, "HEAD~1")
    def выборка():
        plan, totals = mc.plan_for(repo, f"{база}...HEAD", max_n=7)
        return {m.key for m in plan}, totals
    было, totals = выборка()
    assert len(было) == 7 and totals.full == 60, totals
    (repo / "tests" / "test_mod.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "только тест"], cwd=repo, check=True)
    assert выборка()[0] == было
    текст = (repo / "src" / "mod.py").read_text(encoding="utf-8")
    (repo / "src" / "mod.py").write_text("# комментарий сверху\n\n" + текст, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "сдвиг строк"], cwd=repo, check=True)
    assert выборка()[0] == было


def test_область_мутатора_только_код_продукта(tmp_path):
    """Скрипт, который не запускает продукт, в план не входит; скрипт, который
    называет `src/`, — входит (№469: замеры и служебное не мутируются)."""
    repo = _git_repo(tmp_path, {"src/mod.py": "X = 0\n",
                                "scripts/bench.py": "X = 0\n",
                                "scripts/tool.py": "X = 0\n"})
    guard = "\n\nif __name__ == '__main__':\n    f(1)\n"
    код = "def f(a):\n    return a > 1\n"
    (repo / "src" / "mod.py").write_text(код + "\nRUN = 'scripts/tool.py'\n", encoding="utf-8")
    (repo / "scripts" / "bench.py").write_text(код + guard, encoding="utf-8")
    (repo / "scripts" / "tool.py").write_text(код + guard, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)
    plan, _ = mc.plan_for(repo, "HEAD~1...HEAD")
    assert {m.path.relative_to(repo).as_posix() for m in plan} == {"src/mod.py", "scripts/tool.py"}


def _зоны(repo: pathlib.Path, зоны: dict | None, msg: str) -> None:
    путь = repo / mc.ZONES_REL
    if зоны is None:
        путь.unlink()
    else:
        путь.parent.mkdir(parents=True, exist_ok=True)
        путь.write_text(json.dumps({"mutation_critical": зоны}), encoding="utf-8")
    subprocess.run([*_GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*_GIT, "commit", "-qm", msg], cwd=repo, check=True)


def test_зоны_судятся_по_базе_и_голове(tmp_path):
    """Список зон — объединение базы и головы: PR, который сам вносит модуль в
    список, держится им сразу; PR, который его снимает, — нет (входной круг 2 по
    №469, Sonnet C2 = GLM C2). Базы без списка (первый PR №469) — заметка."""
    repo = _git_repo(tmp_path, {"src/mod.py": _БАЗА})
    база = _rev(repo, "HEAD")
    (repo / "src" / "mod.py").write_text(_ПРАВКА, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)
    _зоны(repo, {"mod": "дверь"}, "голова вносит mod")
    plan, totals = mc.plan_for(repo, f"{база}...HEAD")
    assert plan and all(m.critical for m in plan)
    assert "списка зон нет" in totals.zones_note
    # база с модулем, голова его сняла — всё равно критичен
    _зоны(repo, {}, "голова снимает mod")
    plan, _ = mc.plan_for(repo, f"{_rev(repo, 'HEAD~1')}...HEAD")
    assert plan == [] or all(m.critical for m in plan)
    plan, _ = mc.plan_for(repo, f"{база}...HEAD")
    assert plan and not any(m.critical for m in plan)
    # точечная запись: только функция f
    _зоны(repo, {"mod::g": "чужая функция"}, "только g")
    plan, _ = mc.plan_for(repo, f"{база}...HEAD")
    assert plan and not any(m.critical for m in plan)


def test_выживший_в_некритичной_зоне_зелёный_со_списком(tmp_path, monkeypatch, capsys):
    """Опыт №469, пара к `test_jobs_2_находит_тех_же_выживших…` (там критичный —
    красный): те же два выживших вне критичных зон — исход 0, оба названы в отчёте
    и в сводке вердикта."""
    repo = _репо_с_тестом(tmp_path, зоны={"другой": "не этот модуль"})
    данные = tmp_path / "данные"
    данные.mkdir()
    monkeypatch.setenv("CHAROITE_ROOT", str(данные))
    monkeypatch.chdir(repo)
    отчёт = tmp_path / "отчёт.txt"
    rc = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--max", "all", "--timeout", "60",
                  "--report", str(отчёт)])
    текст = отчёт.read_text(encoding="utf-8")
    assert rc == 0, текст
    assert len(_выжившие(текст)) == 2 and "вне критичных зон" in текст, текст
    d = tmp_path / "шарды"
    d.mkdir()
    shutil.copy(mc.shard_line_path(отчёт), d / "r.json")
    assert mc.merge_shards(d) == 0
    out = capsys.readouterr().out
    assert "выжили вне критичных зон: 2 — списком ниже, мерж не держат" in out, out


def test_продолжение_судит_только_остаток_и_итог_равен_чистому(tmp_path, monkeypatch, capsys):
    """Опыт №469: прогон обрывается после трёх мутантов из шести, журнал
    рассуждённых остаётся в корне данных; `--resume <ключ>` судит только три
    оставшихся, и итог равен чистому прогону той же выборки."""
    repo = _репо_с_правкой(tmp_path)
    данные = tmp_path / "данные"
    данные.mkdir()
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(данные))
    monkeypatch.chdir(repo)
    вызовы = []
    выживают = {3}

    def run_tests(cwd, targets, timeout):
        вызовы.append(1)
        if len(вызовы) == 5 and обрыв:
            raise RuntimeError("раннер оборвал job")
        # база зелёная; мутант №3 выживает
        return len(вызовы) == 1 or (len(вызовы) - 1) in выживают
    monkeypatch.setattr(mc, "run_tests", run_tests)
    обрыв = True
    with pytest.raises(RuntimeError):
        mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--timeout", "5", "--force"])
    ключ = re.search(r"--resume (\w+)", capsys.readouterr().out).group(1)
    журнал = данные / "logs" / f"mutation_run-{ключ}-1.jsonl"
    строки = [json.loads(x) for x in журнал.read_text(encoding="utf-8").splitlines()]
    assert строки[0]["manifest"] == mc.FACTS_VERSION and len(строки) == 1 + 3, строки
    # продолжение: база + три оставшихся, выживший прежнего прогона сохранён
    вызовы.clear()
    обрыв = False
    выживают = set()
    отчёт = tmp_path / "продолжение.txt"
    rc = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--timeout", "5", "--force",
                  "--resume", ключ, "--report", str(отчёт)])
    out = capsys.readouterr().out
    assert len(вызовы) == 1 + 3, out
    assert "из журнала прогона: 3 рассуждены, судим 3" in out, out
    продолжение = отчёт.read_text(encoding="utf-8")
    # чистый прогон той же выборки: тот же выживший
    вызовы.clear()
    выживают = {3}
    чистый = tmp_path / "чистый.txt"
    rc_чистый = mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--timeout", "5", "--force",
                         "--report", str(чистый)])
    assert rc == rc_чистый == 0
    assert _выжившие(продолжение) == _выжившие(чистый.read_text(encoding="utf-8")) and \
        len(_выжившие(продолжение)) == 1
    assert продолжение.splitlines()[0] == "Проверено мутантов: 6 из 6, выжило: 1 (в критичных зонах: 0)"


def test_продолжение_с_чужим_ключом_или_без_журнала_отказывает(tmp_path, monkeypatch, capsys):
    """`--resume` без журнала — отказ «не найден или истёк», а не «чисто» по
    пустоте; с ключом другой выборки — отказ с названием разошедшейся части."""
    repo = _репо_с_правкой(tmp_path)
    данные = tmp_path / "данные"
    данные.mkdir()
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(данные))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(mc, "run_tests", lambda cwd, targets, timeout: False)
    assert mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--force",
                    "--resume", "0123456789abcdef"]) == 1
    assert "не найден или истёк" in capsys.readouterr().out
    # журнал прогона с --timeout 5, продолжение с --timeout 7 — другой ключ
    mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--timeout", "5", "--force"])
    ключ = re.search(r"--resume (\w+)", capsys.readouterr().out).group(1)
    assert mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--timeout", "7", "--force",
                    "--resume", ключ]) == 1
    assert "разошлись --timeout" in capsys.readouterr().out


def test_journal_read_терпит_оборванную_строку(tmp_path):
    """SIGKILL посреди записи оставляет половину строки: она не ошибка, мутант
    просто не рассуждён."""
    import charoite_paths
    путь = charoite_paths.log_path(tmp_path, mc.RUN_LOG_KIND, part="abc-1", suffix=".jsonl")
    путь.parent.mkdir(parents=True)
    путь.write_text('{"manifest": 2}\n{"key": "a", "outcome": "killed"}\n{"key": "b", "outc',
                    encoding="utf-8")
    manifests, done = mc.journal_read(tmp_path, "abc")
    assert len(manifests) == 1 and set(done) == {"a"}


def test_signal_group_жнёт_зомби_лидера(tmp_path):
    """Замер 30.09 (macOS): `killpg` группы из одного непожатого зомби-лидера —
    `PermissionError`; остановка его жнёт и не падает (родитель 30.09 упал именно
    здесь). На Linux тот же случай — `ProcessLookupError`, исход тот же."""
    import signal
    import time
    p = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    time.sleep(0.5)                   # вышел, не пожат
    assert mc.signal_group(p, signal.SIGINT) is True
    assert mc.stop_children([p], grace=0.1) == []


def test_stop_children_называет_недобитую_долю(monkeypatch):
    """Группа, которую не достать ни SIGINT, ни SIGKILL, — номер доли в ответе,
    а не трассировка и не молчание."""
    import signal

    class Доля:
        pid = 424242
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("доля", timeout)

    def отказ(pid, sig):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(mc.os, "killpg", отказ)
    assert mc.stop_children([Доля()], grace=0.01) == [1]
    assert mc.signal_group(Доля(), signal.SIGKILL) is False


def test_строка_остановки_называет_ключ_и_недобитых(tmp_path):
    d = tmp_path / "журналы"
    d.mkdir()
    for k in (1, 2):
        (d / f"{k}.txt{mc.SHARD_LINE_SUFFIX}").write_text(
            json.dumps(_факт(k, 2, 3, 6, "partial", run="beefbeefbeefbeef")), encoding="utf-8")
    assert mc.stopped_line(d, 15, [2]) == \
        "прервано сигналом 15: 4 из 6 — продолжить: --resume beefbeefbeefbeef; не добиты доли: 2"


def test_слияние_не_читает_старый_формат(tmp_path, capsys):
    """Машинная строка без версии фактов — нечитаемая: по новым правилам её не судить."""
    (tmp_path / "r.json").write_text(json.dumps({"K": 1, "N": 1, "M": 1, "P": 1, "word": "ok"}),
                                    encoding="utf-8")
    assert mc.merge_shards(tmp_path) == 1
    assert "нечитаемый файл шарда" in capsys.readouterr().out


def test_шарды_разных_выборок_не_сводятся(tmp_path, capsys):
    """Доли одного прогона обязаны назвать одну выборку, зоны и ревизии."""
    (tmp_path / "a.json").write_text(json.dumps(_факт(1, 2, 3, 6, "ok", zones="aaa")), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(_факт(2, 2, 3, 6, "ok", zones="bbb")), encoding="utf-8")
    assert mc.merge_shards(tmp_path) == 1
    assert "разные выборки" in capsys.readouterr().out



# --- №469, выходной круг 1 -----------------------------------------------------


def test_диапазон_с_тремя_точками_берёт_общего_предка(tmp_path):
    """`A...B` разрешается в merge-base: сдвиг кончика A (новый коммит в main после
    fetch или между стартами шардов CI) не меняет ни ключа прогона, ни фактов
    (выходной круг 1 по №469, Sonnet I1)."""
    repo = _git_repo(tmp_path, {"src/mod.py": "X = 0\n"})
    предок = _rev(repo, "HEAD")
    subprocess.run(["git", "checkout", "-qb", "ветка"], cwd=repo, check=True)
    (repo / "src" / "mod.py").write_text("def f(a):\n    return a > 1\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "-"], cwd=repo, check=True)
    главная = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
    было = mc.resolve_range(repo, f"{главная}...ветка")
    (repo / "README").write_text("x\n", encoding="utf-8")
    subprocess.run([*_GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*_GIT, "commit", "-qm", "main ушла вперёд"], cwd=repo, check=True)
    стало = mc.resolve_range(repo, f"{главная}...ветка")
    assert было == стало and было.startswith(предок + "...")
    # две точки — как написано: там левый конец и есть база диффа
    assert mc.resolve_range(repo, f"{главная}..ветка").split("..")[0] == _rev(repo, главная)


def test_слишком_глубокий_файл_не_прочитан_а_не_трассировка(tmp_path, monkeypatch):
    """Обход и канон рекурсивны: `RecursionError` на разобравшемся файле — «не
    прочитан» с причиной (выходной круг 1 по №469, Sonnet M1)."""
    def глубоко(node):
        raise RecursionError("maximum recursion depth exceeded")
    monkeypatch.setattr(mc, "canon", глубоко)
    f = tmp_path / "m.py"
    report = mc.scan(f, {2}, "def f(a):\n    return a > 1\n", rel="src/m.py")
    assert report.mutations == [] and report.unparsed.startswith("RecursionError"), report


def test_факты_без_поля_не_читаются_как_чистые(tmp_path, capsys):
    """Строка версии 2 без `survivors` — нечитаемая, а не «шарды чисты» (Sonnet M3)."""
    строка = _факт(1, 1, 3, 3, "ok")
    del строка["survivors"]
    (tmp_path / "r.json").write_text(json.dumps(строка), encoding="utf-8")
    assert mc.merge_shards(tmp_path) == 1
    assert "нечитаемый файл шарда" in capsys.readouterr().out


def test_факты_пишутся_целиком_без_хвостов(tmp_path):
    """Запись через временный файл и `os.replace`: рядом с отчётом не остаётся
    ничего, кроме отчёта и фактов (Sonnet M2)."""
    отчёт = tmp_path / "артефакты" / "отчёт.txt"
    mc.write_artifacts(отчёт, "текст", mc.Facts(M=1, P=1, tested=1), 0)
    mc.write_artifacts(отчёт, "текст 2", mc.Facts(M=1, P=1, tested=1), 0)
    assert sorted(p.name for p in отчёт.parent.iterdir()) == ["отчёт.txt", "отчёт.txt.json"]
    assert отчёт.read_text(encoding="utf-8") == "текст 2\n"


def test_ключ_продолжения_проверяется_разбором():
    """`--resume` — ровно 16 знаков 0-9a-f: `*` и `../` читали бы чужие журналы (Sonnet M4)."""
    import argparse
    assert mc._resume_arg("0123456789abcdef") == "0123456789abcdef"
    for bad in ("*", "../x", "0123456789ABCDEF", "0123456789abcde"):
        with pytest.raises(argparse.ArgumentTypeError):
            mc._resume_arg(bad)


def test_родитель_jobs_называет_причину_отказа_продолжения(tmp_path, monkeypatch, capsys):
    """`--jobs 2 --resume <ключ без журнала>` — отказ родителя с причиной до долей,
    а не «ни одного файла шарда» (выходной круг 1 по №469, Sonnet I4)."""
    repo = _репо_с_правкой(tmp_path)
    данные = tmp_path / "данные"
    данные.mkdir()
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(данные))
    monkeypatch.chdir(repo)
    monkeypatch.setattr(mc, "child_argv", lambda *a, **kw: pytest.fail("доли не запускаются"))
    assert mc.main(["mutate_check.py", "--range", _ДИАПАЗОН, "--jobs", "2", "--force",
                    "--resume", "0123456789abcdef"]) == 1
    assert "журнал прогона 0123456789abcdef не найден или истёк" in capsys.readouterr().out


def test_критичные_не_рассуждённые_бюджетом_держат_мерж(tmp_path, capsys):
    """CI срезал бюджетом часть критичных — вердикт не зелёный и называет, сколько
    критичных не рассуждено и как догнать (решение главной по выходному кругу 1)."""
    for k, (m, t, cm, ct) in enumerate([(5, 5, 2, 2), (5, 3, 3, 1)], 1):
        (tmp_path / f"r{k}.json").write_text(json.dumps(
            _факт(k, 2, m, 10, "ok", tested=t, critical_M=cm, critical_tested=ct)), encoding="utf-8")
    assert mc.merge_shards(tmp_path) == 1
    head = capsys.readouterr().out.splitlines()[0]
    assert head.startswith("держит мерж: критичных не рассуждено 2 из 5"), head
    assert "--only-critical" in head


def test_догон_только_критичных_судит_критичных_выборки(tmp_path):
    """`--only-critical` берёт из выборки ровно критичных — тот же набор, что у CI."""
    repo = _git_repo(tmp_path, {"src/mod.py": _БАЗА})
    база = _rev(repo, "HEAD")
    (repo / "src" / "mod.py").write_text(_ПРАВКА, encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)
    _зоны(repo, {"mod::f": "дверь"}, "зона — функция f")
    (repo / "src" / "other.py").write_text("def g(a):\n    return a > 1\n", encoding="utf-8")
    subprocess.run([*_GIT, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*_GIT, "commit", "-qm", "некритичный модуль"], cwd=repo, check=True)
    все, _ = mc.plan_for(repo, f"{база}...HEAD", max_n=60)
    крит, totals = mc.plan_for(repo, f"{база}...HEAD", max_n=60, only_critical=True)
    assert крит and all(m.critical for m in крит)
    assert {m.key for m in крит} == {m.key for m in все if m.critical}
    assert any(not m.critical for m in все) and totals.planned == len(крит)
