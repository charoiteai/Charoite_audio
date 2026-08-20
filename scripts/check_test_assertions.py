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

# Способы сказать «здесь что-то проверяется». Хелпер с собственным именем
# (`def check_roundtrip(...)`, внутри assert) гейт не видит — известное
# ограничение: угадывать «а вдруг внутри проверка» значило бы пропускать
# всё подряд. Такой хелпер стоит назвать `assert_*`.
# Префикс — только у `assert`: методы `assertEqual`, `assertRaises` и прочие
# почти всегда проверки. Остальное — ТОЧНЫМ именем: префиксный матчинг
# зачислял в проверки `store.failed(...)` (такой метод есть в конвейере и
# зовётся в тестах), `sys.exit(...)`, любой `exit_*` — то есть тест, который
# ничего не проверяет, проходил гейт (ревью 20.08, GLM).
EXACT = {"raises", "fail", "xfail", "exit", "warns", "deprecated_call",
         "skipTest"}


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
        ident = (name.attr if isinstance(name, ast.Attribute)
                 else name.id if isinstance(name, ast.Name) else "")
        if ident.startswith("assert") or ident in EXACT:
            return True          # pytest.raises(...), self.assertEqual(...)
    return False


def _blocks(node: ast.stmt) -> list[list[ast.stmt]]:
    """Вложенные последовательности инструкций этого узла."""
    out = []
    for field in ("body", "orelse", "finalbody"):
        seq = getattr(node, field, None)
        if isinstance(seq, list) and seq and isinstance(seq[0], ast.stmt):
            out.append(seq)
    for handler in getattr(node, "handlers", []) or []:
        out.append(handler.body)
    return out


def _unreachable_tail(body: list[ast.stmt]) -> bool:
    """После `return`/`raise` в ТОМ ЖЕ блоке остались утверждения?

    Рекурсивно: `return` внутри `if`/`try`/`for` — самый частый случай, а
    первая версия смотрела только верхний уровень тела функции. То есть
    проверка, написанная против мёртвых утверждений, сама пропускала почти
    все мёртвые утверждения — и её собственный тест покрывал ровно тот
    тривиальный случай, который работал (ревью 20.08, DeepSeek).
    """
    for i, node in enumerate(body):
        if isinstance(node, (ast.Return, ast.Raise)):
            rest = body[i + 1:]
            if any(any(_is_assertion(n) for n in ast.walk(s)) for s in rest):
                return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue          # вложенная функция живёт своей жизнью
        if any(_unreachable_tail(block) for block in _blocks(node)):
            return True
    return False


SKIP_MARKS = {"skip", "xfail"}      # БЕЗУСЛОВНЫЕ; skipif освобождения не даёт:
                                    # на машине, где условие ложно, тест
                                    # исполняется, и проверки ему нужны


def _skip_name(dec: ast.expr) -> str:
    """Имя пометки декоратора: `pytest.mark.skip(...)` → `skip`."""
    node = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_skipped(fn: ast.AST, cls: ast.ClassDef | None = None) -> bool:
    """Тест явно помечен как пропускаемый — своим декоратором, декоратором
    класса или `pytest.skip()` в теле (то же намерение, другая запись)."""
    marks = list(getattr(fn, "decorator_list", []))
    if cls is not None:
        marks += list(cls.decorator_list)
    if any(_skip_name(d) in SKIP_MARKS for d in marks):
        return True
    # В теле — только явные идиомы пропуска: `pytest.skip(...)` и
    # `self.skipTest(...)`. Раньше годилось любое имя `skip`, то есть чужой
    # `page.skip()` глушил гейт целиком (ревью 20.08, GLM).
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        owner = n.func.value
        owner_name = owner.id if isinstance(owner, ast.Name) else ""
        if (owner_name, n.func.attr) in {("pytest", "skip"), ("self", "skipTest")}:
            return True
    return False


def check(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as e:                      # noqa: BLE001
        return [f"{path}:{e.lineno}: не разбирается ({e.msg})"]
    except OSError as e:
        # Голый стектрейс вместо диагностики — плохая работа гейта: человек
        # должен видеть, ЧТО не прочиталось, а не трейс интерпретатора.
        return [f"{path}: не читается ({e})"]

    classes: dict[ast.AST, ast.ClassDef] = {}
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef):
            for child in cls.body:
                classes[child] = cls

    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        if _is_skipped(node, classes.get(node)):
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
        # И `test_*.py`, и `*_test.py`: pytest собирает оба, а гейт видел
        # только первый — файл `foo_test.py` без проверок проходил CI, хотя
        # исполнялся (ревью 20.08, GLM).
        if root.is_dir():
            files.extend(sorted(set(root.rglob("test_*.py")) | set(root.rglob("*_test.py"))))
        else:
            files.append(root)

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
