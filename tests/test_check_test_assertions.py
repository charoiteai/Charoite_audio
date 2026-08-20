"""Гейт «тест обязан быть способен упасть» сам обязан работать.

Проверка, которая молча пропускает всё, хуже отсутствия проверки: она
выглядит как гарантия. Поэтому здесь и положительные примеры, и
отрицательные — на каждом классе, который гейт заявляет.
"""
import pathlib
import sys

import pytest

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
