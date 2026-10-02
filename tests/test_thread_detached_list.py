"""Список брошенных потоков — утверждённый в обе стороны (№415).

AST по `src/` собирает вызовы `threads.spawn`/`threads.timer` с `detached=`
и сверяет пары (файл, имя потока) со списком ниже. Односторонняя сверка
молча пропускала бы новый брошенный поток (правило ширится без решения) или
держала бы снятый (список врёт о коде).

Имя берётся литералом; единственное исключение — слои демона: тринадцать
циклов стоят одним вызовом в цикле по кортежу пар «имя, функция», и имена
раскрываются из этого кортежа. По вычисленному имени список не сверить,
поэтому имя — либо строка на месте, либо первая половина литеральной пары.

Поток в списке — тот, что СОЗНАТЕЛЬНО живёт дальше своей границы: демон не
дожидается слоёв встречи, консоль — прогрева, тень ⚡ — ответа. Поток, который
кто-то дожидается (`join`), обязан быть НЕ в списке: его границу держит тот,
кто зовёт.
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"

#: Утверждённые брошенные потоки, парами (путь от `src/`, имя потока).
#: Пара повторяется столько раз, сколько вызовов: сторож авто-подсказок
#: заводит `auto-hint` и в старте, и в перезапуске — один слой, два места.
DETACHED: tuple[tuple[str, str], ...] = (
    ("daemon.py", "import-prune"),
    ("daemon.py", "orphan-rebuild-chain"),
    ("daemon.py", "llm-warmup"),
    ("daemon.py", "memory-warm"),
    ("daemon.py", "cloud-thread-refine"),
    ("daemon.py", "frame-drop-report"),
    ("daemon.py", "fast-trigger-sender"),
    ("daemon.py", "hint-manual"),
    ("daemon.py", "answer-ask"),
    ("daemon.py", "topic-expand"),
    ("daemon.py", "summary-manual"),
    ("daemon.py", "stt-loop"),
    ("daemon.py", "think-loop"),
    ("daemon.py", "thread-loop"),
    ("daemon.py", "instant-loop"),
    ("daemon.py", "cloud-loop"),
    ("daemon.py", "fast-trigger-loop"),
    ("daemon.py", "deja-vu-loop"),
    ("daemon.py", "dialog-markup-loop"),
    ("daemon.py", "name-loop"),
    ("daemon.py", "minutes-loop"),
    ("daemon.py", "live-context-loop"),
    ("daemon.py", "stdin-loop"),
    ("daemon.py", "autostop-loop"),
    ("daemon.py", "auto-hint"),
    ("daemon.py", "auto-hint"),
    ("decision_gate.py", "gate-shadow"),
    # сторож родителя в ребёнке потока Nemotron (№540): живёт, пока жив процесс движка,
    # и кончает его сам — ждать его некому
    ("diarize_nemotron.py", "nemotron-parent-watch"),
    # тень потока Nemotron (№478): отсрочка перед убийством, ожидание выхода ребёнка и
    # строка человеку не держат ни захват, ни выход демона — их никто не дожидается
    ("live_nemotron.py", "nemotron-live-kill"),
    ("live_nemotron.py", "nemotron-live-death"),
    ("live_nemotron.py", "nemotron-live-say"),
    ("llm.py", "fit-cache-sweep"),
    ("main.py", "console-warmup"),
    ("main.py", "console-stt"),
)


def _spawn_calls(tree: ast.Module):
    """Вызовы `threads.spawn`/`threads.timer` в файле."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr in ("spawn", "timer")
                and isinstance(func.value, ast.Name) and func.value.id == "threads"):
            yield node


def _literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _pair_names(value: ast.AST) -> list[str] | None:
    """Имена из кортежа пар «имя, функция» — или None, если это не он."""
    if not isinstance(value, ast.Tuple):
        return None
    имена: list[str] = []
    for elt in value.elts:
        if not (isinstance(elt, ast.Tuple) and len(elt.elts) == 2):
            return None
        s = _literal(elt.elts[0])
        if s is None:
            return None
        имена.append(s)
    return имена


def _loop_names(tree: ast.Module) -> dict[str, list[str]]:
    """Имя-переменная цикла → имена потоков: кортеж пар «имя, функция», по
    которому идёт `for имя, функция in …`. Так стоят слои демона: имена не в
    вызове, а в кортеже, и вызов один на тринадцать потоков."""
    tuple_values: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            tuple_values[node.targets[0].id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            tuple_values[node.target.id] = node.value
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        target = node.target
        if not (isinstance(target, ast.Tuple) and len(target.elts) == 2
                and isinstance(target.elts[0], ast.Name)):
            continue
        iterable = node.iter
        if isinstance(iterable, ast.Name):
            iterable = tuple_values.get(iterable.id)
        if iterable is None:
            continue
        имена = _pair_names(iterable)
        if имена:
            out[target.elts[0].id] = имена
    return out


def _detached_calls() -> list[tuple[str, str]]:
    """Пары (файл, имя) у вызовов с `detached=` по всему `src/`."""
    pairs: list[tuple[str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()
        loops = _loop_names(tree)
        for node in _spawn_calls(tree):
            keywords = {k.arg: k.value for k in node.keywords if k.arg}
            if "detached" not in keywords:
                continue
            name = keywords.get("name")
            literal = _literal(name)
            if literal is not None:
                pairs.append((rel, literal))
            elif isinstance(name, ast.Name) and name.id in loops:
                pairs.extend((rel, имя) for имя in loops[name.id])
            else:
                raise AssertionError(
                    f"{rel}: имя detached-потока не разобрать — ни литерал, ни "
                    f"кортеж пар «имя, функция»: {ast.unparse(node)}")
    return sorted(pairs)


def test_список_detached_совпадает_с_кодом():
    """Список и код равны как множества с кратностью — в обе стороны."""
    pairs = _detached_calls()
    лишние = [p for p in pairs if pairs.count(p) > DETACHED.count(p)]
    пропали = [p for p in DETACHED if DETACHED.count(p) > pairs.count(p)]
    assert not лишние, f"брошены без решения (вне списка): {лишние}"
    assert not пропали, f"в списке, а в коде нет: {пропали}"


def test_у_detached_потока_роль_и_причина_на_месте():
    """`detached` — строка-причина, а не флаг: у каждого вызова с `detached=`
    рядом обязательны `name=` и `role=`, и причина не пуста."""
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _spawn_calls(tree):
            keywords = {k.arg: k.value for k in node.keywords if k.arg}
            if "detached" not in keywords:
                continue
            reason = _literal(keywords["detached"])
            assert reason is not None and reason.strip(), (
                f"{path.relative_to(SRC)}: detached без причины-строки: {ast.unparse(node)}")
            assert "name" in keywords and "role" in keywords, (
                f"{path.relative_to(SRC)}: у брошенного потока нет имени или роли: "
                f"{ast.unparse(node)}")
