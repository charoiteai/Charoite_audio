"""Мутатор сам обязан быть проверен — он инструмент доверия.

Инструмент, который молча говорит «всё хорошо», хуже отсутствия
инструмента: он выглядит как гарантия. Первая версия ровно это и делала —
объявляла мутанта выжившим там, где та же мутация руками роняла девять
тестов. Причина: рабочее дерево поднималось от текущего HEAD, а номера
строк брались из другого диапазона, и мутации ложились мимо — в
комментарии и пустые места.
"""
import ast
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
    monkeypatch.setattr(busy_signals, "machine_busy", lambda root: ["живая запись"])
    assert mc.main(["mutate_check.py", "--range", "HEAD...HEAD"]) == 3
    assert "машина занята" in capsys.readouterr().out
    # пустой диапазон — «проверять нечего» ИМЕННО этим кодом, а не любым не-3:
    # прежний `!= 3` проходил и при 0, то есть весь смысл круга 2 не держался
    import exit_codes
    assert mc.main(["mutate_check.py", "--range", "HEAD...HEAD", "--force"]) == exit_codes.EXIT_NOTHING_TO_CHECK
    assert "машина занята" not in capsys.readouterr().out
    assert exit_codes.outcome(exit_codes.EXIT_NOTHING_TO_CHECK) == "nothing"


def test_таблица_исхода_прогона():
    """Состояние прогона → код возврата, все случаи в одной таблице.

    Прежняя лестница `if` в конце `main` спрашивала `tested == 0` раньше
    полноты, и прогон, прерванный на первом мутанте при плане из сорока,
    отвечал «проверять было нечего»; ни один тест туда не доставал, потому что
    покрыт был только пустой диапазон (круг 4 по №339, обе головы).
    """
    import exit_codes
    N, P = exit_codes.EXIT_NOTHING_TO_CHECK, exit_codes.EXIT_PARTIAL
    таблица = [
        # выжившие, проверено, план, срезано, не применилось → код
        ([],        0,  0, 0, 0, N),   # плана не было вовсе
        ([],        0, 40, 0, 0, P),   # прервано на первом мутанте — не «нечего»
        ([],        3, 40, 0, 0, P),   # прервано посередине
        ([],       30, 40, 10, 0, P),  # срезано потолком
        ([],       39, 40, 0, 1, P),   # один не применился
        ([],       40, 40, 0, 0, 0),   # проверен весь план, чисто
        (["м"],     1, 40, 0, 0, 1),   # выживший важнее неполноты
        (["м"],    40, 40, 0, 0, 1),
        # Срез потолком: плана не осталось, но проверять БЫЛО что. Без этих строк
        # `dropped` не влияет на ответ ни в одном состоянии, и мутант «убрать
        # dropped» выживает (круг 5: GLM I1, DS I1 — независимо).
        ([],        0,  0, 10, 0, 7),   # --max 0 срезал весь план — неполно, не «нечего»
        ([],       40, 40, 10, 0, 7),   # судили весь остаток, но часть срезана
        (["м"],    40, 40, 10, 0, 1),   # выживший важнее и среза
        ([],        0,  0,  0, 0, 6),   # плана не было вовсе — вот это «нечего»
    ]
    for survivors, tested, planned, dropped, skipped, ждём in таблица:
        got = mc.verdict_code(survivors, tested, planned, dropped, skipped)
        assert got == ждём, f"{(survivors, tested, planned, dropped, skipped)}: {got}, ждали {ждём}"
    # и класс исхода согласован с каноном
    assert exit_codes.outcome(mc.verdict_code([], 0, 40, 0, 0)) == "partial"
    assert exit_codes.outcome(mc.verdict_code([], 40, 40, 0, 0)) == "ok"


def test_есть_изменённые_строки_но_ломать_нечего(monkeypatch, capsys):
    """Ветка «строки есть, мутировать нечего» (комментарий, докстринг, строковая
    константа) отвечает «проверять нечего», а не успехом. Мутатор нашёл её
    непокрытой в CI по №339: прежний тест гонял пустой диапазон и до неё не
    доходил."""
    import busy_signals
    import exit_codes
    monkeypatch.setattr(busy_signals, "machine_busy", lambda root: [])
    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {REPO / "scripts" / "mutate_check.py": {1}})
    monkeypatch.setattr(mc, "mutations_for", lambda *a, **k: [])
    assert mc.main(["mutate_check.py", "--range", "A...B"]) == exit_codes.EXIT_NOTHING_TO_CHECK
    assert "ничего мутируемого" in capsys.readouterr().out


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


def test_мутатор_из_чужого_клона_берёт_канон_своего_дерева(tmp_path):
    """Мутатор одного клона, запущенный из каталога другого, собирает процесс из
    своего дерева: канон и сигналы занятости берутся рядом со скриптом. Вторая
    вставка `src/` git-корня текущего каталога брала канон чужого клона, и
    сверка корня кода роняла мутатор трассировкой (круг 1 по коду №331, Opus M2).
    Без `CHAROITE_ROOT` — ровно тот путь, где канон спрашивают о корне кода."""
    import exit_codes
    чужой = tmp_path / "другой-клон"
    # клон целиком, а не один канон: сигналы занятости сами кладут свой каталог
    # первым в sys.path, и с одним каноном в чужом `src/` дефект не воспроизводился
    shutil.copytree(REPO / "src", чужой / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (чужой / "scripts").mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q"], cwd=чужой, check=True)
    subprocess.run([*git, "add", "-A"], cwd=чужой, check=True)
    subprocess.run([*git, "commit", "-qm", "проба"], cwd=чужой, check=True)
    env = {k: v for k, v in os.environ.items() if k != "CHAROITE_ROOT"}
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "mutate_check.py"),
                          "--range", "HEAD...HEAD", "--force"],
                         cwd=чужой, env=env, capture_output=True, text=True, timeout=120, check=False)
    assert "Traceback" not in out.stderr, out.stderr[-800:]
    assert out.returncode == exit_codes.EXIT_NOTHING_TO_CHECK, (out.returncode, out.stdout[-400:])
