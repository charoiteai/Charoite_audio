"""Поток STT не должен затенять состояние автостопа.

Инцидент 20.08: во вложенной функции `stt_loop` появилась локальная переменная
с именем `heard` — тем же, каким выше по функции назван словарь автостопа.
Python делает такое имя локальным для ВСЕЙ вложенной функции, поэтому строка
`heard["at"] = time.monotonic()` (отметка речи) падала UnboundLocalError. Поток
STT умирал на первой же реплике: подсказок в живой встрече не было, отметка
речи не проставлялась никогда, и автостоп через пять минут глушил идущую
встречу, считая, что речи не было ни разу.

Ошибка пережила проверку двумя головами и CI: тесты автостопа работают с чистой
функцией `autostop.decide`, а падал поток вокруг неё.
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "daemon.py"


def _nested_functions(fn: ast.FunctionDef) -> list[ast.FunctionDef]:
    """Функции, определённые внутри данной (на любой глубине)."""
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.FunctionDef) and n is not fn]


def _rebound_names(fn: ast.FunctionDef) -> set[str]:
    """Имена, которым внутри присваивают ЗНАЧОК ЦЕЛИКОМ (`x = ...`).

    Присваивание по ключу (`x["k"] = ...`) сюда не входит: оно имя локальным не
    делает, а именно оно и стоит в отметке речи — иначе тест ловил бы сам себя.
    """
    freed = {n for node in ast.walk(fn) if isinstance(node, (ast.Nonlocal, ast.Global))
             for n in node.names}
    out: set[str] = set()
    for node in ast.walk(fn):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For)):
            targets = [node.target]
        for tgt in targets:
            if isinstance(tgt, ast.Name) and tgt.id not in freed:
                out.add(tgt.id)
            elif isinstance(tgt, (ast.Tuple, ast.List)):
                for sub in tgt.elts:
                    if isinstance(sub, ast.Name) and sub.id not in freed:
                        out.add(sub.id)
    return out


def _state_dicts(fn: ast.FunctionDef, skip: ast.FunctionDef) -> set[str]:
    """Словари состояния внешней функции: `x = {...}` вне вложенного потока."""
    inner = {id(n) for n in ast.walk(skip)}
    out: set[str] = set()
    for node in ast.walk(fn):
        if id(node) in inner or not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                out.add(tgt.id)
    return out


def test_вложенные_потоки_не_затеняют_состояние_внешней_функции():
    """Проверяются ВСЕ вложенные функции, а не только stt_loop.

    Ограничить проверку одним потоком значило бы ловить ровно тот случай,
    который уже случился, и пропустить следующий в соседней функции
    (ревью 20.08, локальная голова).
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    outers = [f for f in ast.walk(tree)
              if isinstance(f, ast.FunctionDef) and _nested_functions(f)]
    assert outers, "не нашёл ни одной функции с вложенными"

    checked, bad = 0, []
    for outer in outers:
        for inner in _nested_functions(outer):
            state = _state_dicts(outer, inner)
            if not state:
                continue
            checked += 1
            for name in sorted(state & _rebound_names(inner)):
                bad.append(f"{outer.name} → {inner.name}: {name}")

    assert checked, "тест потерял предмет: словарей состояния не нашлось"
    assert not bad, (
        "вложенная функция переприсваивает словарь состояния внешней: "
        f"{bad}. Python сделает имя локальным для всей вложенной функции, и "
        "обращение по ключу упадёт UnboundLocalError — поток умрёт на первой "
        "же итерации (инцидент 20.08 с `heard`: живая встреча обрезана "
        "автостопом через пять минут)")


def test_словарь_автостопа_на_месте():
    """Страховка от того, что тест переживёт предмет проверки."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    main = next(f for f in ast.walk(tree)
                if isinstance(f, ast.FunctionDef) and f.name == "main")
    loop = next(f for f in _nested_functions(main) if f.name == "stt_loop")
    assert "heard" in _state_dicts(main, loop)
