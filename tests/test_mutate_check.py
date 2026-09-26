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
    N, P, U = exit_codes.EXIT_NOTHING_TO_CHECK, exit_codes.EXIT_PARTIAL, exit_codes.EXIT_UNJUDGED
    таблица = [
        # выжившие, проверено, план, срезано, не применилось → код
        ([],        0,  0, 0, 0, N),   # плана не было вовсе
        ([],        0, 40, 0, 0, U),   # прервано на первом мутанте — не «нечего» и не «часть»
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
    # Счётчики плана (№386): пустой план при строках в диапазоне — «мутировать
    # нечего», а не «строк нет»; непрочитанный файл — неполнота при любом плане.
    U, T = exit_codes.EXIT_UNMUTABLE, mc.ScanTotals
    со_счётчиками = [
        ([],     0,  0,  0, 0, T(files_in=1, lines_in=5, nodes=3), U),
        # строки есть, кода в них нет (комментарий, константа модуля) — «нечего»,
        # а не слепое пятно операторов (критика DS круга 1 по #630)
        ([],     0,  0,  0, 0, T(files_in=1, lines_in=5, lines_constant=2), N),
        ([],     0,  0,  0, 0, T(), N),
        ([],     0,  0, 10, 0, T(files_in=1, lines_in=5), P),   # срез важнее «нечего»
        ([],     0,  0,  0, 0, T(files_in=2, lines_in=5, files_unreadable=2), P),
        ([],     0,  0,  0, 0, T(files_in=2, lines_in=5, files_unreadable=1), P),
        ([],    40, 40,  0, 0, T(files_in=2, lines_in=5, files_unreadable=1), P),
        ([],    40, 40,  0, 0, T(files_in=2, lines_in=5), 0),
        (["м"], 40, 40,  0, 0, T(files_in=2, lines_in=5, files_unreadable=1), 1),
    ]
    for survivors, tested, planned, dropped, skipped, totals, ждём in со_счётчиками:
        got = mc.verdict_code(survivors, tested, planned, dropped, skipped, totals)
        assert got == ждём, f"{(survivors, tested, planned, dropped, skipped, totals)}: {got}, ждали {ждём}"
    # и класс исхода согласован с каноном
    assert exit_codes.outcome(mc.verdict_code([], 0, 40, 0, 0)) == "unjudged"
    assert exit_codes.outcome(mc.verdict_code([], 3, 40, 0, 0)) == "partial"
    assert exit_codes.outcome(mc.verdict_code([], 40, 40, 0, 0)) == "ok"
    assert exit_codes.outcome(mc.verdict_code([], 0, 0, 0, 0, T(files_in=1, lines_in=1, nodes=1))) == "unmutable"


def _quiet_machine(monkeypatch):
    import busy_signals
    monkeypatch.setattr(busy_signals, "machine_busy", lambda root: [])


def test_есть_изменённые_строки_но_ломать_нечего(monkeypatch, capsys):
    """Ветка «строки есть, мутировать нечего» отвечает своим кодом и печатает
    счётчики плана: под общим «нечего» 25.09 ноль мутантов от правки условия
    читался как пустой диапазон (№386). Прежний тест подменял `mutations_for`,
    а план пустел раньше — на `git show` несуществующей ревизии."""
    import exit_codes
    _quiet_machine(monkeypatch)
    totals = mc.ScanTotals(files_in=2, lines_in=7, lines_constant=3, nodes=11)
    monkeypatch.setattr(mc, "plan_for", lambda root, rng: ([], totals))
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
    totals = mc.ScanTotals(files_in=1, lines_in=2, lines_constant=1, nodes=0)
    monkeypatch.setattr(mc, "plan_for", lambda root, rng: ([], totals))
    assert mc.main(["mutate_check.py", "--range", "A...B"]) == exit_codes.EXIT_NOTHING_TO_CHECK
    out = capsys.readouterr().out
    assert "нет кода" in out and "нет изменённых строк" not in out, out
    for число in ("файлов 1", "строк 2", "констант модуля 1", "узлов AST 0"):
        assert число in out, out


def test_нет_изменённых_строк_это_nothing(monkeypatch, capsys):
    import exit_codes
    _quiet_machine(monkeypatch)
    monkeypatch.setattr(mc, "changed_lines", lambda root, rng: {})
    assert mc.main(["mutate_check.py", "--range", "A...B"]) == exit_codes.EXIT_NOTHING_TO_CHECK
    assert "нет изменённых строк" in capsys.readouterr().out


def test_ни_один_файл_не_прочитался_это_неполнота(monkeypatch, capsys):
    """Файл, чей `git show` не прочитался, раньше молча выпадал из плана, и
    пустой план отвечал «нечего» (№386)."""
    import exit_codes
    _quiet_machine(monkeypatch)
    monkeypatch.setattr(mc, "changed_lines",
                        lambda root, rng: {REPO / "scripts" / "mutate_check.py": {1, 2}})
    assert mc.main(["mutate_check.py", "--range", "HEAD...нет-такой-ревизии"]) == exit_codes.EXIT_PARTIAL
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

    assert totals == mc.ScanTotals(files_in=2, lines_in=4, lines_constant=1, nodes=3,
                                   files_unreadable=1), totals
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
    assert mc.verdict_code([], len(plan), len(plan), 0, 0, totals) == mc.EXIT_PARTIAL


def test_план_берёт_изменённые_строки_из_git(tmp_path):
    """Сквозь `changed_lines`: две ревизии, изменена одна строка."""
    repo = _git_repo(tmp_path, {"src/mod.py": "def f(x):\n    return x\n"})
    (repo / "src" / "mod.py").write_text("def f(x):\n    return not x\n", encoding="utf-8")
    subprocess.run([*_GIT, "commit", "-qam", "правка"], cwd=repo, check=True)

    plan, totals = mc.plan_for(repo, "HEAD~1...HEAD")

    assert totals == mc.ScanTotals(files_in=1, lines_in=1, nodes=3), totals
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
    monkeypatch.setattr(mc, "plan_for", lambda root, rng: ([mut], mc.ScanTotals(files_in=1, lines_in=1)))
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
    не зависеть. Возвращает журнал вызовов `run_tests`: (набор, таймаут)."""
    _quiet_machine(monkeypatch)
    monkeypatch.setenv("CHAROITE_ROOT", str(tmp_path))
    monkeypatch.setattr(mc, "plan_for", lambda root, rng: (list(plan), mc.ScanTotals(files_in=1, lines_in=1)))
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
    assert текст.startswith("Проверено мутантов: 3 из 6 (срезано --max: 1; остановка: бюджет), "
                            "выжило: 0"), текст
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


def test_без_бюджета_поведение_прежнее(tmp_path, monkeypatch, capsys):
    """Без `--budget-s` время не ограничивает ничего: часы уходят на годы вперёд,
    а судится весь план, и исход — «проверено всё, чисто»."""
    plan = _мутанты("scripts/mutate_check.py", 4)
    журнал = _прогон(tmp_path, monkeypatch, plan, секунды={_MC: 1e9})
    отчёт = tmp_path / "отчёт.txt"
    assert mc.main(["mutate_check.py", "--range", "HEAD", "--force", "--report", str(отчёт)]) == 0
    assert len(журнал) == 1 + 4 and {t for _, t in журнал} == {120}, журнал
    assert отчёт.read_text(encoding="utf-8") == "Проверено мутантов: 4 из 4, выжило: 0\n"
    out = capsys.readouterr().out
    assert "бюджет" not in out
    # счёт мутантов в строках прогона — с единицы и до конца плана
    assert "  [1/4] убит: " in out and "  [4/4] убит: " in out, out


def test_отчёт_не_судившихся_не_считает_неприменённых():
    """Из пяти: два проверено, один не применился — не судились двое. Не
    применившийся назван своей строкой и в «НЕ СУДИЛОСЬ» не входит."""
    m = _мутанты("scripts/mutate_check.py", 1)[0]
    текст = mc.render_report(2, [], [(m, "узел не нашёлся на своём отрезке")], 5, 0,
                             "бюджет", mc.ScanTotals())
    assert текст.startswith("Проверено мутантов: 2 из 5 (остановка: бюджет), выжило: 0"), текст
    assert "НЕ СУДИЛОСЬ: 2 (прервано: бюджет)" in текст, текст
    assert "НЕ ПРИМЕНИЛОСЬ: 1" in текст, текст
