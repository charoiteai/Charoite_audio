#!/usr/bin/env python3
"""Тест обязан быть способен упасть.

Покрытие этого не ловит: тест, который вызывает функцию и ничего не
проверяет, покрывает её строки на сто процентов и остаётся зелёным всегда.
Ровно этот класс — «зелёный, но ничего не держит» — и есть самая дорогая
разновидность мёртвого теста: он не просто бесполезен, он гасит тревогу,
потому что галочка в CI выглядит как доказательство.

Проверяем самое грубое и самое частое: в теле `test_*` нет ни одного
`assert`, ни `pytest.raises`, ни `pytest.fail`, ни `self.assert*`. Тонкие
случаи (тавтология `f(x) == f(x)`, мок, подменивший всю проверяемую логику)
статикой не берутся — их ловит только мутационное тестирование, то есть
проверка «верни дефект — тест покраснеет».

Отдельно ловим утверждения, до которых не доходит выполнение: `assert`
после `return` в том же блоке. Такой тест выглядит настоящим и зелёный
всегда.
"""
from __future__ import annotations

import ast
import pathlib
import sys

CHECKERS = ("assert", "raises", "fail", "xfail", "exit")


def _is_assertion(node: ast.AST) -> bool:
    """Узел проверяет что-нибудь?"""
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.Raise):
        # `raise AssertionError(...)` в ветке «сюда попадать нельзя» —
        # полноценная проверка, просто записанная руками: так пишут тест,
        # который ждёт исключения и падает, если его не было.
        exc = node.exc
        target = exc.func if isinstance(exc, ast.Call) else exc
        if isinstance(target, ast.Name) and "Error" in target.id:
            return True
        if isinstance(target, ast.Attribute) and "Error" in target.attr:
            return True
    if isinstance(node, ast.Call):
        name = node.func
        if isinstance(name, ast.Attribute) and any(
                name.attr.startswith(c) for c in CHECKERS):
            return True          # pytest.raises(...), self.assertEqual(...)
        if isinstance(name, ast.Name) and any(
                name.id.startswith(c) for c in CHECKERS):
            return True
    if isinstance(node, ast.With):   # with pytest.raises(...): ...
        return any(_is_assertion(item.context_expr) for item in node.items)
    return False


def _unreachable_tail(body: list[ast.stmt]) -> bool:
    """После `return`/`raise` в том же блоке остались утверждения?"""
    for i, node in enumerate(body):
        if isinstance(node, (ast.Return, ast.Raise)):
            rest = body[i + 1:]
            return any(any(_is_assertion(n) for n in ast.walk(s)) for s in rest)
    return False


def check(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:                      # noqa: BLE001
        return [f"{path}:{e.lineno}: не разбирается ({e.msg})"]

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        # Заглушки помечаются явно — pytest.skip внутри или декоратор skip.
        decorators = ast.dump(ast.Module(body=list(node.decorator_list), type_ignores=[]))
        if "skip" in decorators or "xfail" in decorators:
            continue
        if not any(_is_assertion(n) for n in ast.walk(node)):
            problems.append(
                f"{path}:{node.lineno}: {node.name} — ни одной проверки: "
                f"тест не способен упасть")
        elif _unreachable_tail(node.body):
            problems.append(
                f"{path}:{node.lineno}: {node.name} — проверки после return: "
                f"выполнение до них не доходит")
    return problems


def main(argv: list[str]) -> int:
    roots = [pathlib.Path(a) for a in argv[1:]] or [pathlib.Path("tests")]
    files: list[pathlib.Path] = []
    for root in roots:
        files.extend(sorted(root.rglob("test_*.py")) if root.is_dir() else [root])

    problems = [p for f in files for p in check(f)]
    for p in problems:
        print(p)
    if problems:
        print(f"\nТестов без проверок: {len(problems)}. "
              f"Тест, который не может упасть, не держит ничего.")
        return 1
    print(f"Проверено файлов: {len(files)} — у каждого теста есть чем упасть.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
