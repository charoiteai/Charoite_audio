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


def test_неизвестный_модуль_даёт_весь_набор():
    """«Не нашли тестов» не значит «его никто не проверяет» — берём всё."""
    assert mc.tests_for(REPO, REPO / "src" / "нет_такого_модуля.py") == ["tests"]


def test_зависший_прогон_считается_убитым(tmp_path, monkeypatch):
    """Мутант, подвесивший тесты, изменил поведение — это kill, а не выживший;
    иначе один такой съедает весь ночной бюджет и попадает в отчёт как
    «тесты не заметили»."""
    import subprocess

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=1)

    monkeypatch.setattr(subprocess, "run", hang)
    assert mc.run_tests(tmp_path, ["tests"], timeout=1) is False
