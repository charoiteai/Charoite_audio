"""Нормализация текста и имён пакета графа — одно место на две формы.

`norm` — сборка Unicode, регистр и ё→е: ей режутся слова и стемы (`graph_nodes`).
`fold` — `norm` плюс полноширинные латиница и цифры в обычные: ей сравниваются
пути и имена папок — поиск (`graph_search.norm`), роли документа и предикаты схемы
хранилища (`graph_schema`). Пока `fold` жила в `graph_search`, схема сравнивала роли
своей третьей нормализацией (NFC + casefold), и роль, различимая инвариантом схемы,
могла склеиться в предикате потребителя (№422 PR B, развилка F1).

Модуль — член пакета `charoite_graph`, только stdlib.
"""
from __future__ import annotations

import unicodedata

#: Полноширинные латиница, цифры и знаки (U+FF01–U+FF5E) → ASCII: ＹｕＰａｙ → YuPay.
FULLWIDTH = {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}


def norm(s: str) -> str:
    """Регистр и ё→е, поверх единой формы Unicode.

    NFC первой строкой: macOS отдаёт имя файла в разложенной форме, где «ё» —
    это «е» плюс отдельные точки. Без сборки слово режется на «е» и «лка» ещё
    до стемминга, и узел перестаёт узнавать сам себя (GLM, круг 2 по №291)."""
    if not s.isascii() and not unicodedata.is_normalized("NFC", s):
        s = unicodedata.normalize("NFC", s)
    return s.lower().replace("ё", "е")


def fold(s: str) -> str:
    """`norm` и полноширинные латиница и цифры → обычные: форма сравнения путей,
    имён папок и ролей. Одна на поиск и на схему хранилища."""
    return norm(s).translate(FULLWIDTH)
