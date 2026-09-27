"""Проводка контуров демона: у каждого контура ⚡ — названный вызов в известном месте.

`daemon.main()` — 2800 строк без шва, через который в него вошёл бы тест: демон
не поднимается без PortAudio, Ollama и живого звука. Условия проводки тени шлюза
решений переехали в `decision_gate.shadow_for` и там судятся тестами, но три
вызова остались в демоне, и их снятие не заметил бы ни pytest, ни мутатор: у
мутатора нет оператора «убрать вызов» (выходной круг 3 по #651, DS I1).

Здесь проводка — запись в таблице: функция, вызов, и где он обязан стоять. Сторож
структурный (AST) и потому ловит написание, а не поведение, — поэтому у него есть
отрицательная сторона: те же правила, приложенные к испорченным копиям исходника,
обязаны краснеть. Настоящий гейт — разрез ⚡ из `main()` в функцию, которую тест
исполняет (фаза 5 брифа модулей, №324); до него таблица — честный временный гейт.
"""
import ast
import pathlib

import pytest

DAEMON = pathlib.Path(__file__).resolve().parent.parent / "src" / "daemon.py"


def _call_name(node: ast.AST) -> str:
    """`gate_shadow.start(q)` → `gate_shadow.start`; не вызов имени — пустая строка."""
    if not isinstance(node, ast.Call):
        return ""
    parts, f = [], node.func
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
        return ".".join(reversed(parts))
    return ""


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _statement_with(body: list[ast.stmt], call: str) -> tuple[list[ast.stmt], int] | None:
    """Список операторов и номер того, что содержит вызов `call`, — на любой глубине
    блоков, но без вложенных функций: у них свой вызов и свой срок жизни."""
    for i, stmt in enumerate(body):
        if any(_call_name(n) == call for n in ast.walk(stmt)
               if not isinstance(n, (ast.FunctionDef, ast.Lambda))):
            for field in ("body", "orelse", "finalbody", "handlers"):
                inner = getattr(stmt, field, None)
                if isinstance(inner, list) and inner and isinstance(inner[0], ast.stmt):
                    found = _statement_with(inner, call)
                    if found:
                        return found
            return body, i
    return None


def wiring_problems(source: str) -> list[str]:
    """Нарушения проводки тени в исходнике демона; пустой список — порядок."""
    tree = ast.parse(source)
    main = _function(tree, "main")
    loop = _function(main, "instant_loop") if main else None
    if main is None or loop is None:
        return ["нет main или instant_loop — сторож смотрит мимо"]
    problems = []
    if not any(_call_name(n) == "decision_gate.shadow_for" for n in ast.walk(main)):
        problems.append("main не зовёт decision_gate.shadow_for")
    start = _statement_with(loop.body, "gate_shadow.start")
    finish = _statement_with(loop.body, "shadow.finish")
    if start is None:
        problems.append("instant_loop не зовёт gate_shadow.start")
    if finish is None:
        problems.append("instant_loop не зовёт shadow.finish")
    if start is None or finish is None:
        return problems
    (body_s, i), (body_f, j) = start, finish
    if body_s is not body_f or j <= i:
        problems.append("shadow.finish не стоит после gate_shadow.start в том же блоке — "
                        "условие или цикл между ними оставят прогон без исхода")
        return problems
    if not isinstance(body_f[j], ast.Expr):
        problems.append("shadow.finish — не отдельный оператор: исход идёт мимо цикла ⚡")
    for stmt in body_s[i + 1:j]:
        for n in ast.walk(stmt):
            if isinstance(n, (ast.Continue, ast.Break, ast.Return)):
                problems.append(f"между start и finish выход из цикла (строка {n.lineno}) — "
                                "прогон тени останется без исхода и строки")
    return problems


def test_the_daemon_wires_the_shadow():
    assert wiring_problems(DAEMON.read_text(encoding="utf-8")) == []


def _broken(old: str, new: str) -> str:
    src = DAEMON.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"образец порчи не найден: {old!r}"
    return src.replace(old, new)


@pytest.mark.parametrize("old, new, says", [
    ("gate_shadow = decision_gate.shadow_for(cfg, instant_on)",
     "gate_shadow = decision_gate.Shadow(None, print)", "shadow_for"),
    ("shadow = gate_shadow.start(q)", "shadow = decision_gate.IDLE", "gate_shadow.start"),
    ("                shadow.finish(decision_gate.outcome_of(",
     "                if answer:\n                    shadow.finish(decision_gate.outcome_of(", "том же блоке"),
    ("                answer = \"\".join(parts)\n                shadow.finish(",
     "                answer = \"\".join(parts)\n                if not answer:\n                    continue\n"
     "                shadow.finish(", "выход из цикла"),
    ("                shadow.finish(decision_gate.outcome_of(",
     "                ok = shadow.finish(decision_gate.outcome_of(", "не отдельный оператор"),
])
def test_the_guard_turns_red_on_a_broken_wiring(old, new, says):
    """Отрицательная сторона: сторож по написанию обязан краснеть на порче проводки,
    иначе он сторожит пустоту."""
    problems = wiring_problems(_broken(old, new))
    assert any(says in p for p in problems), problems
