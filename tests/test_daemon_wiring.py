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
import sys

import pytest

DAEMON = pathlib.Path(__file__).resolve().parent.parent / "src" / "daemon.py"
sys.path.insert(0, str(DAEMON.parent))


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


# ------------------------------------------------ тень потока Nemotron (№478)

def _in_loop_body(stmt: ast.stmt):
    """Узлы оператора без вложенных циклов и функций: их continue — не наш."""
    stack = [stmt]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.For, ast.While, ast.FunctionDef, ast.Lambda)):
                stack.append(child)


def _state_values(node: ast.AST) -> list[object] | None:
    """Значения, которые выражение может дать: константа, условное из констант или
    `live_nemotron.diarized_state(...)` — её ответы берутся у неё самой на всех входах."""
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, ast.Call) and _call_name(node) == "live_nemotron.diarized_state":
        import live_nemotron
        return sorted({live_nemotron.diarized_state(failed, jobs)
                       for failed in (False, True) for jobs in (None, [])})
    if isinstance(node, ast.IfExp):
        a, b = _state_values(node.body), _state_values(node.orelse)
        return None if a is None or b is None else a + b
    return None


def _branches(node: ast.If) -> list[list[ast.stmt]]:
    out = [node.body]
    while len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
        node = node.orelse[0]
        out.append(node.body)
    out.append(node.orelse)
    return out


def nemotron_wiring_problems(source: str) -> list[str]:
    """Нарушения проводки тени Nemotron: строка на каждый принятый чанк, слушатель
    кадров, остановка до пересборки. Пустой список — порядок."""
    import live_nemotron
    tree = ast.parse(source)
    main = _function(tree, "main")
    stt = _function(main, "stt_loop") if main else None
    if main is None or stt is None:
        return ["нет main или stt_loop — сторож смотрит мимо"]
    problems = []
    listener = [n for n in ast.walk(main) if _call_name(n) == "nemotron_shadow.attach"
                and [a.id for a in n.args if isinstance(a, ast.Name)] == ["hub"]]
    if not any(_call_name(n) == "live_nemotron.start" for n in ast.walk(main)) or not listener:
        problems.append("main не поднимает тень или не вешает её слушателем кадров")
    loop = next((n for n in ast.walk(stt) if isinstance(n, ast.For)
                 and isinstance(n.target, ast.Name) and n.target.id == "placed"), None)
    if loop is None:
        return problems + ["в stt_loop нет цикла по принятым чанкам"]
    calls = [n for n in ast.walk(stt) if _call_name(n) == "nemotron_shadow.note_chunk"]
    at = [k for k, stmt in enumerate(loop.body)
          if any(_call_name(n) == "nemotron_shadow.note_chunk" for n in ast.walk(stmt))]
    if len(calls) != 1 or len(at) != 1:
        return problems + [f"stt_loop не зовёт nemotron_shadow.note_chunk ровно раз в теле цикла "
                           f"по чанкам (вызовов {len(calls)})"]
    k = at[0]
    args = calls[0].args
    if len(args) != 2 or not all(isinstance(a, ast.Name) for a in args) \
            or [a.id for a in args] != ["placed", "tracker_state"]:
        problems.append("note_chunk зовётся не с (placed, tracker_state)")
    # Режим `on` (№478 B): строку чанка плана stream пишет label_chunk. Оба вызова — в одном
    # операторе-развилке, по одному в каждой ветке: каждый путь пишет ровно одну строку.
    labels = [n for n in ast.walk(stt) if _call_name(n) == "nemotron_shadow.label_chunk"]
    fork = loop.body[k]
    if len(labels) != 1 or not isinstance(fork, ast.If) or fork.orelse == [] \
            or not any(n is labels[0] for s in fork.body for n in ast.walk(s)) \
            or not any(n is calls[0] for s in fork.orelse for n in ast.walk(s)):
        problems.append("label_chunk и note_chunk — не две ветки одной развилки (строка тени на каждый путь)")
    elif [a.id for a in labels[0].args[:2] if isinstance(a, ast.Name)] != ["placed", "tracker_state"]:
        problems.append("label_chunk зовётся не с (placed, tracker_state, …)")
    elif not any(isinstance(t, ast.Try)
                 and any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in t.handlers)
                 and any(n is labels[0] for b in t.body for n in ast.walk(b))
                 for t in ast.walk(fork)):
        # исключение label_chunk роняло бы нить STT молча до конца встречи (выходной круг
        # GLM по №478 B, I1): вызов — под перехватом, чанк уходит трекеру
        problems.append("label_chunk не под перехватом — сбой раскладки убьёт нить STT")
    for stmt in loop.body[:k]:
        for n in _in_loop_body(stmt):
            if isinstance(n, (ast.Continue, ast.Break, ast.Return)):
                problems.append(f"выход из цикла до note_chunk (строка {n.lineno}) — чанк без строки тени")
    plan = next((s for s in loop.body[:k] if isinstance(s, ast.If)
                 and "plan" in {n.id for n in ast.walk(s.test) if isinstance(n, ast.Name)}), None)
    if plan is None:
        problems.append("выбор плана раскладки не стоит до note_chunk")
    else:
        for branch in _branches(plan):
            values = [v for s in branch if isinstance(s, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "tracker_state" for t in s.targets)
                      for v in (_state_values(s.value) or [object()])]
            if not values:
                problems.append("ветка плана без состояния трекера (tracker_state)")
            elif any(v not in live_nemotron.CHUNK_STATES for v in values):
                problems.append(f"состояние трекера вне набора {live_nemotron.CHUNK_STATES}")
    final = [t for t in ast.walk(main) if isinstance(t, ast.Try)
             and any(_call_name(n) == "subprocess.Popen" for s in t.finalbody for n in ast.walk(s))]
    ok_stop = False
    for t in final:
        idx = {name: next((i for i, s in enumerate(t.finalbody)
                           if any(_call_name(n) == name for n in ast.walk(s))), None)
               for name in ("nemotron_shadow.stop", "subprocess.Popen")}
        if idx["nemotron_shadow.stop"] is not None and idx["nemotron_shadow.stop"] < idx["subprocess.Popen"]:
            ok_stop = True
    if not ok_stop:
        problems.append("финал main не гасит тень до запуска пересборки (стоп)")
    # №533: на выходе повисшего ребёнка убивает таймер с отсрочкой выхода, а не штатной 5 с
    exit_grace = any(_call_name(n) == "nemotron_shadow.stop"
                     and any(k.arg == "grace" and isinstance(k.value, ast.Attribute)
                             and k.value.attr == "EXIT_GRACE_S" for k in n.keywords)
                     for t in final for s in t.finalbody for n in ast.walk(s))
    if not exit_grace:
        problems.append("финал main гасит тень без отсрочки выхода EXIT_GRACE_S (отсрочка)")
    # №533: журнал тени дописывается даже если хаб упал — close в finally вокруг hub.stop
    closes = any(isinstance(t, ast.Try)
                 and any(_call_name(n) == "hub.stop" for s in t.body for n in ast.walk(s))
                 and any(_call_name(n) == "nemotron_shadow.close" for s in t.finalbody for n in ast.walk(s))
                 for t in ast.walk(main))
    if not closes:
        problems.append("финал main не закрывает тень в finally вокруг hub.stop (закрытие)")
    return problems


def test_the_daemon_wires_the_nemotron_shadow():
    assert nemotron_wiring_problems(DAEMON.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("old, new, says", [
    ("                nemotron_shadow.note_chunk(placed, tracker_state)", "                pass",
     "ровно раз"),
    ("                # строка тени — на КАЖДЫЙ",
     "                if jobs is None:\n                    continue\n                # строка тени — на КАЖДЫЙ",
     "выход из цикла"),
    ('                    tracker_state = "off"', "                    pass", "без состояния"),
    ('                    tracker_state = "off"', '                    tracker_state = "plain"', "вне набора"),
    ("tracker_state = live_nemotron.diarized_state(split_failed, tracker_jobs)", 'tracker_state = "split"',
     "вне набора"),
    ("                        jobs = nemotron_shadow.label_chunk(\n                            placed, tracker_state,",
     "                        jobs = nemotron_shadow.label_chunk(\n                            placed, 'pieces',",
     "label_chunk зовётся не с"),
    ("                else:\n                    nemotron_shadow.note_chunk(placed, tracker_state)",
     "                nemotron_shadow.note_chunk(placed, tracker_state)\n                if False:\n                    pass",
     "не две ветки"),
    ("                    except Exception as e:  # noqa: BLE001 — раскладка по потоку и её запас упали: чанк — трекеру",
     "                    except KeyboardInterrupt as e:",
     "не под перехватом"),
    ("        nemotron_shadow.attach(hub)", "        pass", "слушателем"),
    ("        nemotron_shadow.stop(grace=live_nemotron.EXIT_GRACE_S)\n        # Пересборка",
     "        pass\n        # Пересборка", "стоп"),
    ("        nemotron_shadow.stop(grace=live_nemotron.EXIT_GRACE_S)\n        # Пересборка",
     "        nemotron_shadow.stop()\n        # Пересборка", "отсрочка"),
    ("            nemotron_shadow.close(live_nemotron.STOP_GRACE_S)", "            pass", "закрытие"),
    ("        try:\n            hub.stop()  # финализирует", "        hub.stop()\n        try:\n            pass  # финализирует",
     "закрытие"),
])
def test_the_nemotron_guard_turns_red_on_a_broken_wiring(old, new, says):
    problems = nemotron_wiring_problems(_broken(old, new))
    assert any(says in p for p in problems), problems
