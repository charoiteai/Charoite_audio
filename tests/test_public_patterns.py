"""Второй рубеж обезличивания: форматы, которые видны без списка имён.

Поимённый список маркеров живёт только на машине автора — в CI его нет и
быть не должно: перечень того, что мы прячем, сам по себе чувствителен, а
секреты GitHub вдобавок не отдаются в PR из форков, то есть проверка не
сработала бы ровно в самом опасном случае.

Здесь проверяются публичные шаблоны: они ничего не выдают своим видом, но
ловят самый частый способ утечки — скопированный кусок конфига, лога или
путь с рабочей машины.
"""
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import check_private_markers as cpm  # noqa: E402


def _hits(text: str) -> list[str]:
    """Какие шаблоны срабатывают на строке."""
    return [name for name, raw in cpm.PUBLIC_PATTERNS.items()
            if re.search(raw, text)]


@pytest.mark.parametrize("line,expected", [
    ("base_url: https://payments-gw-lan-main.intranet", "внутренний хост"),
    ("host: reports.corp", "внутренний хост"),
    ("owner: ivanov@company-name.ru", "почта на непубличном домене"),
    ("graph_dir: /Users/realname/Documents/Проект", "личный путь"),
    ("Согласовал Петров И.И.", "фамилия с инициалами"),
])
def test_leaks_are_caught(line, expected):
    assert expected in _hits(line), f"утечка прошла мимо: {line}"


@pytest.mark.parametrize("line", [
    # Синтетические пользователи: примеры и тесты обязаны показывать пути.
    'let out = replacing("graph_dir", with: "/Users/a/My Vault")',
    '"/Users/x/iCloud/Documents/Проект/Встречи/2026-08-03.md"',
    "graph_dir: /Users/ПУТЬ/К/Documents",
    # Домашний адрес в публичном репозитории ничего не выдаёт — правила про
    # приватные сети нет намеренно, иначе тесты политики удалённых хостов
    # («10.0.0.5 отвергается») краснели бы вечно.
    'for host in ["10.0.0.5", "192.168.1.7", "ollama.local"]',
    "http://127.0.0.1:11434",
    # Публичная почта проекта.
    "charoiteai@gmail.com",
    # Обычный русский текст с заглавной буквы — не фамилия с инициалами.
    "Чароит слушает встречу локально. Ничего не уходит наружу.",
])
def test_no_false_alarms(line):
    assert _hits(line) == [], f"ложная тревога на: {line}"


def test_clean_tree_passes():
    """Само дерево обязано быть чистым — иначе страж бесполезен с рождения."""
    files = cpm.tracked_files()
    assert cpm.scan_public(files) == []


def test_ci_runs_the_public_check():
    """Сторож проводки: проверка должна стоять в workflow, иначе она есть,
    но публичное дерево по-прежнему защищено только локальным хуком."""
    wf = (REPO / ".github/workflows/supply-chain.yml").read_text(encoding="utf-8")
    assert "check_private_markers.py --public-only" in wf, \
        "второй рубеж отключён — обезличивание снова держится на одном хуке"


def test_private_list_is_not_required_for_public_mode(monkeypatch, tmp_path):
    """В CI приватного списка нет. Публичный режим обязан работать без него —
    иначе на раннере проверка тихо превратится в успех."""
    monkeypatch.setenv("CHAROITE_MARKERS", str(tmp_path / "нет-такого-файла.txt"))
    assert cpm.scan_public(cpm.tracked_files()) == []
