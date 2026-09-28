"""Константа в теле NamedTuple не становится полем кортежа (№477).

Ловушка: в теле `typing.NamedTuple` аннотированное имя — поле. Под
`from __future__ import annotations` так и с `ClassVar` (без отложенных
аннотаций `ClassVar` падает `TypeError` при создании класса), и с простой
аннотацией `RACE: str = "race"`. Атрибут класса тогда — дескриптор поля, а не
строка: сравнение с ним работает, только пока с обеих сторон тот же объект, а в
JSON и в лог уходит мусор. Голое имя без аннотации (`RACE = "race"`) — строка
класса; так устроены `WriteOutcome`, `SummaryOutcome`, `CanonOutcome`.

Правило — функция от дерева, как `layout_map._file_roots`. Код продукта (src,
scripts) — по деревьям `layout_map.inventory()`; тесты раскладка не разбирает
(вид `out`), их обход прямой и шире прецедента `test_check_test_assertions.py`:
`*.py`, а не только `test_*.py` — ловушка равно вероятна в conftest и хелперах.
Законных полей в верхнем регистре в проекте нет, таблицы исключений нет.
"""
from __future__ import annotations

import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import layout_map as lm  # noqa: E402


def constant_fields(tree: ast.Module) -> list[str]:
    """`строка: Класс.ИМЯ` — аннотированное имя в верхнем регистре в теле класса,
    среди баз которого `NamedTuple` (голый или `typing.NamedTuple`)."""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any((isinstance(b, ast.Name) and b.id == "NamedTuple")
                   or (isinstance(b, ast.Attribute) and b.attr == "NamedTuple") for b in node.bases):
            continue
        for stmt in node.body:
            if (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
                    and stmt.target.id.isupper()):
                hits.append(f"{stmt.lineno}: {node.name}.{stmt.target.id}")
    return hits


def _src(body: str) -> ast.Module:
    return ast.parse("from __future__ import annotations\nimport typing\nfrom typing import ClassVar, NamedTuple\n"
                     + body)


def test_the_rule_catches_an_annotated_constant_in_every_spelling():
    assert constant_fields(_src("class A(typing.NamedTuple):\n    x: int\n    Y: str = 'y'\n")) == ["6: A.Y"]
    assert constant_fields(_src("class A(NamedTuple):\n    x: int\n    Y: ClassVar[str] = 'y'\n")) == ["6: A.Y"]
    assert constant_fields(_src("class A(typing.NamedTuple):\n    x: int\n    Y: typing.ClassVar[str] = 'y'\n"
                                "    Z: int\n")) == ["6: A.Y", "7: A.Z"]


def test_the_rule_leaves_bare_constants_fields_and_other_classes_alone():
    assert constant_fields(_src("class A(typing.NamedTuple):\n    x: int\n    Y = 'y'\n")) == []
    assert constant_fields(_src("class A(typing.NamedTuple):\n    state: str | None\n    refused: str = ''\n")) == []
    assert constant_fields(_src("class A:\n    Y: str = 'y'\n")) == []          # не NamedTuple: аннотация — не поле


def test_no_namedtuple_in_product_code_turns_a_constant_into_a_field():
    inv = lm.inventory(REPO)
    trees = {rel: info.tree for rel, info in inv.files.items() if info.tree is not None}
    assert any(rel.startswith("scripts/") for rel in trees) and any(rel.startswith("src/") for rel in trees), (
        "инвентарь без деревьев src или scripts — сторож сторожил бы пустоту")
    hits = [f"{rel}:{h}" for rel, tree in sorted(trees.items()) for h in constant_fields(tree)]
    assert hits == [], "константа в теле NamedTuple стала полем — снять аннотацию:\n" + "\n".join(hits)


def test_no_namedtuple_in_the_tests_turns_a_constant_into_a_field():
    files = sorted((REPO / "tests").rglob("*.py"))
    assert (REPO / "tests" / "conftest.py") in files
    hits = [f"{f.relative_to(REPO)}:{h}" for f in files
            for h in constant_fields(ast.parse(f.read_text(encoding="utf-8")))]
    assert hits == [], "константа в теле NamedTuple стала полем — снять аннотацию:\n" + "\n".join(hits)
