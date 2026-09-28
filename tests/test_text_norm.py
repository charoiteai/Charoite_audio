"""Нормализация пакета графа: `norm` и `fold` — одно место на поиск и схему (№422, PR B)."""
from __future__ import annotations

import pathlib
import sys
import unicodedata

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from charoite_graph import graph_nodes, graph_search  # noqa: E402
from charoite_graph import text_norm  # noqa: E402


@pytest.mark.parametrize("сырое, ждём", [
    ("Ёлки", "елки"),
    (unicodedata.normalize("NFD", "Ёмкий"), "емкий"),
    ("ABC abc", "abc abc"),
    ("", ""),
], ids=["ё-и-регистр", "разложенная-форма", "латиница", "пусто"])
def test_norm_собирает_форму_регистр_и_ё(сырое, ждём):
    """NFC до всего остального: разложенная «ё» — это «е» и точки, без сборки она
    не превратилась бы в «е» после `lower`."""
    assert text_norm.norm(сырое) == ждём


def test_norm_не_трогает_полноширинные():
    """Полноширинные знаки — забота `fold`, не `norm`: стеммер узлов режет слова без
    них, и их смена поменяла бы стемы."""
    assert text_norm.norm("ＹｕＰａｙ") == "ｙｕｐａｙ"


@pytest.mark.parametrize("сырое, ждём", [
    ("ＹｕＰａｙ", "yupay"),
    ("Ｃｏｒｅｓ／１２", "cores/12"),
    ("＿ИНДЕКС", "_индекс"),
    ("～", "~"),
    ("！", "!"),
    ("｟", "｟"),
], ids=["латиница", "цифры-и-косая", "подчёркивание", "последний-в-диапазоне", "первый-в-диапазоне",
        "за-диапазоном"])
def test_fold_сворачивает_полноширинные(сырое, ждём):
    """`fold` = `norm` плюс U+FF01–U+FF5E в ASCII; знак за диапазоном не трогается."""
    assert text_norm.fold(сырое) == ждём


def test_старые_имена_это_те_же_функции():
    """`graph_nodes.norm` и `graph_search.norm` — тот же объект, что у `text_norm`: одна
    нормализация на пакет, а не три копии тела."""
    assert graph_nodes.norm is text_norm.norm
    assert graph_search.norm is text_norm.fold


def test_norm_не_трогает_unicode_без_нужды(monkeypatch):
    """Горячий путь: `norm` зовут на каждое слово индекса. ASCII не проверяется на форму
    вовсе, собранная (NFC) строка не пересобирается; пересборка — только у разложенной."""
    calls = []
    real_is, real_norm = unicodedata.is_normalized, unicodedata.normalize
    monkeypatch.setattr(unicodedata, "is_normalized", lambda f, s: (calls.append("проверка"), real_is(f, s))[1])
    monkeypatch.setattr(unicodedata, "normalize", lambda f, s: (calls.append("сборка"), real_norm(f, s))[1])
    text_norm.norm("Plain ASCII")
    assert calls == []
    text_norm.norm("Ёлки")
    assert calls == ["проверка"]
    calls.clear()
    text_norm.norm(real_norm("NFD", "Ёлки"))
    assert calls == ["проверка", "сборка"]
