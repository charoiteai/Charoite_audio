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


def _nested(fn: ast.FunctionDef, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(fn):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


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


def test_stt_loop_не_затеняет_состояние_внешней_функции():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    outer = next((f for f in ast.walk(tree)
                  if isinstance(f, ast.FunctionDef) and _nested(f, "stt_loop")), None)
    assert outer is not None, "не нашёл функцию, внутри которой живёт stt_loop"
    loop = _nested(outer, "stt_loop")

    state = _state_dicts(outer, loop)
    assert "heard" in state, "словарь автостопа `heard` исчез — тест потерял предмет"

    collisions = sorted(state & _rebound_names(loop))
    assert not collisions, (
        "во вложенном stt_loop переприсвоены словари состояния внешней функции "
        f"{collisions}: Python сделает эти имена локальными для всего потока, и "
        "обращение по ключу упадёт UnboundLocalError — поток умрёт на первой "
        "реплике (инцидент 20.08 с `heard`, встреча обрезана автостопом)")
