"""Шапка после BOM: запись поля не заводит вторую шапку и не теряет BOM.

Прямые тесты `frontmatter`: мутатор подбирает тесты по импорту модуля, а BOM-тесты
досье импортируют `dossier` — мутанты `_put_line` оставались несудимыми (CI по 3e8a49f3)."""

from charoite_graph import frontmatter

BOM = "\ufeff"


def test_scalar_field_after_a_bom_keeps_one_head():
    text = BOM + "---\nтема: т\n---\n# т\n"
    out = frontmatter.with_field(text, "ревизия", "2026-10-30")
    assert out == BOM + '---\nтема: т\nревизия: "2026-10-30"\n---\n# т\n', out


def test_list_field_after_a_bom_keeps_one_head():
    text = BOM + "---\nтема: т\n---\n# т\n"
    out = frontmatter.with_list_field(text, "участники", ["Мария"])
    # `list_field` по тексту с BOM шапку не видит (на базе так же — долг №637), поэтому строкой целиком
    assert out == BOM + '---\nтема: т\nучастники: ["Мария"]\n---\n# т\n', out
