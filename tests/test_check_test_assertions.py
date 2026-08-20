"""Гейт «тест обязан быть способен упасть» сам обязан работать.

Проверка, которая молча пропускает всё, хуже отсутствия проверки: она
выглядит как гарантия. Поэтому здесь и положительные примеры, и
отрицательные — на каждом классе, который гейт заявляет.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import check_test_assertions as gate  # noqa: E402


def _check(tmp_path: pathlib.Path, code: str) -> list[str]:
    f = tmp_path / "test_sample.py"
    f.write_text(code, encoding="utf-8")
    return gate.check(f)


def test_тест_без_проверок_ловится(tmp_path):
    assert _check(tmp_path, "def test_nothing():\n    do_work()\n")


def test_обычный_assert_проходит(tmp_path):
    assert not _check(tmp_path, "def test_ok():\n    assert 1 == 1\n")


def test_ожидание_исключения_проходит(tmp_path):
    code = ("import pytest\n"
            "def test_raises():\n"
            "    with pytest.raises(ValueError):\n"
            "        boom()\n")
    assert not _check(tmp_path, code)


def test_ручной_raise_ошибки_считается_проверкой(tmp_path):
    """Так пишут тест, который ждёт исключения и падает, если его не было."""
    code = ("def test_manual():\n"
            "    try:\n"
            "        boom()\n"
            "    except KeyError:\n"
            "        return\n"
            "    raise AssertionError('должно было упасть')\n")
    assert not _check(tmp_path, code)


def test_unittest_стиль_проходит(tmp_path):
    code = ("class T:\n"
            "    def test_eq(self):\n"
            "        self.assertEqual(1, 1)\n")
    assert not _check(tmp_path, code)


def test_проверки_после_return_ловятся(tmp_path):
    """Утверждение, до которого выполнение не доходит, — тот же мёртвый тест,
    только выглядит настоящим."""
    code = ("def test_unreachable():\n"
            "    return\n"
            "    assert False\n")
    problems = _check(tmp_path, code)
    assert problems and "return" in problems[0]


def test_пропущенный_тест_не_считается_дефектом(tmp_path):
    code = ("import pytest\n"
            "@pytest.mark.skip('нужен симулятор')\n"
            "def test_skipped():\n"
            "    do_work()\n")
    assert not _check(tmp_path, code)


def test_не_тестовые_функции_не_трогаем(tmp_path):
    assert not _check(tmp_path, "def helper():\n    do_work()\n")


def test_битый_файл_не_роняет_гейт(tmp_path):
    problems = _check(tmp_path, "def test_broken(:\n")
    assert problems and "не разбирается" in problems[0]


def test_весь_репозиторий_проходит_гейт():
    """Живая проверка: на текущем дереве тестов гейт обязан молчать."""
    problems = [p for f in sorted((REPO / "tests").rglob("test_*.py"))
                for p in gate.check(f)]
    assert not problems, "\n".join(problems)


def test_проверки_после_return_внутри_if_ловятся(tmp_path):
    """Первая версия смотрела только верхний уровень тела функции — то есть
    проверка против мёртвых утверждений пропускала почти все мёртвые
    утверждения (ревью 20.08, DeepSeek)."""
    code = ("def test_early_exit():\n"
            "    if not ready():\n"
            "        return\n"
            "        assert result == expected\n"
            "    assert True\n")
    problems = _check(tmp_path, code)
    assert problems and "return" in problems[0], problems


def test_проверки_после_return_в_except_ловятся(tmp_path):
    code = ("def test_in_handler():\n"
            "    try:\n"
            "        boom()\n"
            "    except KeyError:\n"
            "        return\n"
            "        assert never_reached()\n"
            "    assert True\n")
    problems = _check(tmp_path, code)
    assert problems and "return" in problems[0], problems


def test_вложенная_функция_не_считается_мёртвым_хвостом(tmp_path):
    """У вложенной функции своя жизнь: её тело исполняется при вызове."""
    code = ("def test_outer():\n"
            "    def helper():\n"
            "        return 1\n"
            "    assert helper() == 1\n")
    assert not _check(tmp_path, code)


def test_skipif_не_освобождает_от_проверок(tmp_path):
    """Условный пропуск: там, где условие ложно, тест ИСПОЛНЯЕТСЯ."""
    code = ("import pytest, sys\n"
            "@pytest.mark.skipif(sys.platform == 'win32', reason='не наш случай')\n"
            "def test_conditional():\n"
            "    run_but_check_nothing()\n")
    assert _check(tmp_path, code), "skipif не должен давать индульгенцию"


def test_строка_skip_в_параметрах_не_отменяет_проверку(tmp_path):
    """Раньше пометка искалась подстрокой по дампу AST: слово «skip» в любом
    аргументе любого декоратора освобождало тест от гейта."""
    code = ("import pytest\n"
            "@pytest.mark.parametrize('v', ['skip-этот-режим'])\n"
            "def test_param(v):\n"
            "    run_but_check_nothing(v)\n")
    assert _check(tmp_path, code)


def test_пропуск_в_теле_равен_пропуску_декоратором(tmp_path):
    code = ("import pytest\n"
            "def test_needs_simulator():\n"
            "    pytest.skip('нужен симулятор')\n")
    assert not _check(tmp_path, code)


def test_пропущенный_класс_освобождает_методы(tmp_path):
    code = ("import pytest\n"
            "@pytest.mark.skip('весь класс не наш')\n"
            "class TestGroup:\n"
            "    def test_inside(self):\n"
            "        do_work()\n")
    assert not _check(tmp_path, code)


def test_ожидание_предупреждения_считается_проверкой(tmp_path):
    code = ("import pytest\n"
            "def test_warns():\n"
            "    with pytest.warns(UserWarning):\n"
            "        do_work()\n")
    assert not _check(tmp_path, code)


def test_режим_ci_каталогом():
    """`main()` — единственная точка входа CI и pre-commit, и до сих пор её
    не звал ни один тест: ломающие правки (`argv` вместо `argv[1:]`, потеря
    ветки одиночного файла) прошли бы мимо всех (ревью 20.08, GLM)."""
    assert gate.main(["gate", str(REPO / "tests")]) == 0


def test_режим_pre_commit_одним_файлом():
    assert gate.main(["gate", str(REPO / "tests" / "test_post_hook.py")]) == 0


def test_плохой_тест_роняет_точку_входа(tmp_path):
    bad = tmp_path / "test_bad.py"
    bad.write_text("def test_nothing():\n    do_work()\n", encoding="utf-8")
    assert gate.main(["gate", str(bad)]) == 1


def test_файл_с_хвостом_test_тоже_проверяется(tmp_path):
    """pytest собирает и `*_test.py` — гейт обязан видеть их тоже."""
    bad = tmp_path / "legacy_test.py"
    bad.write_text("def test_nothing():\n    do_work()\n", encoding="utf-8")
    assert gate.main(["gate", str(tmp_path)]) == 1


def test_нечитаемый_файл_даёт_диагностику_а_не_трейс(tmp_path):
    missing = tmp_path / "test_missing.py"
    problems = gate.check(missing)
    assert problems and "не читается" in problems[0]


def test_чужой_метод_с_именем_failed_не_считается_проверкой(tmp_path):
    """`store.failed(...)` — метод конвейера, а не проверка. Префиксный
    матчинг зачислял его в утверждения, и тест проходил гейт впустую."""
    code = ("def test_x(store, tmp_path):\n"
            "    store.failed(tmp_path / 'a.md', 'e')\n")
    assert _check(tmp_path, code)


def test_чужой_skip_не_глушит_гейт(tmp_path):
    code = ("def test_x(page):\n"
            "    page.skip()\n")
    assert _check(tmp_path, code), "пропуск засчитан по чужому методу"
