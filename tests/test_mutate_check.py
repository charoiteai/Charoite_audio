"""Мутатор сам обязан быть проверен — он инструмент доверия.

Инструмент, который молча говорит «всё хорошо», хуже отсутствия
инструмента: он выглядит как гарантия. Первая версия ровно это и делала —
объявляла мутанта выжившим там, где та же мутация руками роняла девять
тестов. Причина: рабочее дерево поднималось от текущего HEAD, а номера
строк брались из другого диапазона, и мутации ложились мимо — в
комментарии и пустые места.
"""
import ast
import pathlib
import sys


REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import mutate_check as mc  # noqa: E402


def _mutate(tmp_path: pathlib.Path, code: str, lines: set[int]):
    f = tmp_path / "sample.py"
    f.write_text(code, encoding="utf-8")
    return mc.mutations_for(f, lines)


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
    mut = next(m for m in mc.mutations_for(f, {2}) if "Gt" in m.what)

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


def test_зависший_прогон_считается_убитым(tmp_path, monkeypatch):
    """Мутант, подвесивший тесты, изменил поведение — это kill, а не выживший;
    иначе один такой съедает весь ночной бюджет и попадает в отчёт как
    «тесты не заметили»."""
    import subprocess

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    monkeypatch.setattr(subprocess, "run", hang)
    assert mc.run_tests(tmp_path, ["tests"], timeout=1) is False


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
    mut = next(m for m in mc.mutations_for(f, {6}) if "Gt" in m.what)

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
    mut = next(m for m in mc.mutations_for(f, {2}) if "NotIn" in m.what)

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
    mut = next(m for m in mc.mutations_for(f, {2}) if "Gt" in m.what)

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


def test_устаревший_байткод_не_судит_мутанта_по_чужому_коду(tmp_path):
    """Python сверяет .pyc с исходником по mtime в секундах и размеру. Два
    мутанта одной длины, записанные в одну секунду, для него один файл:
    второй исполнялся байткодом первого и получал ЕГО вердикт. Так 21.08
    «выжил» мутант 6.0→0 в stt_runtime, под который тест написан и который
    руками падает (DeepSeek независимо: «отчёт пережил этот тест»)."""
    import os

    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "import pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))\n"
        "import mod\n"
        "def test_value():\n"
        "    assert mod.value() == 1\n", encoding="utf-8")
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
    assert not list(src.glob("__pycache__/*")), "мутатор оставил байткод в дереве"


def test_явный_return_none_не_мутируется(tmp_path):
    """`return None` → `return None` — тождество; в отчёте оно читалось как
    выживший мутант и тонуло среди настоящих (партия D, 22.08)."""
    muts = _mutate(tmp_path, "def f(x):\n    if x is None:\n        return None\n    return x\n", {3, 4})
    assert [m.line for m in muts if m.what.startswith("return")] == [4], [str(m) for m in muts]
