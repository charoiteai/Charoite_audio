"""Выключатель обязан стоять там, где уходит запрос, а не рядом.

src/privacy.py решает, разрешено ли облако. Но решение бесполезно, если
какой-то путь к сети его не спрашивает. Ровно так и есть: cloud_live
проверяется в fire_question (авто-путь), а cloud_loop — тред, который
реально запускает `claude -p` с куском стенограммы — не проверяет ничего.
До него ведёт вторая дорога: команда `cloud` из stdin, то есть кнопка
«Claude» (⌘⇧⏎) в приложении. Конфиг без cloud_live, SUFLER_NO_CLOUD в
окружении — неважно: нажатие отправляет 2200 символов стенограммы.

Второй инвариант того же уровня — биллинг. Облако вызывается только через
Claude Code по подписке, поэтому ANTHROPIC_API_KEY вычищается из env
дочернего процесса: с ключом в окружении тот же вызов ушёл бы на
потокенный биллинг Anthropic API. Это записано в комментариях и держится
на внимательности — здесь оно становится проверяемым.

Тесты структурные (по AST), а не поведенческие: daemon.py не импортируется
без PortAudio, а поднимать демон, Ollama и `claude` ради проверки, что в
функции стоит проверка, — не тот размен. Инвариант тут именно структурный:
«у каждого выхода в сеть есть выключатель и вычищенный env».
"""
import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

# (файл, функция, что должно упоминаться внутри)
NETWORK_EXITS = (
    ("daemon.py", "cloud_loop"),
    ("graph_updater.py", "main"),
)


def _func(path: pathlib.Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"{path.name}: функция {name} не найдена")


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _consts(node: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _own_consts(fn: ast.AST) -> set[str]:
    """Строки самой функции, без вложенных.

    cloud_loop объявлен внутри main, и обычный обход отдал бы main все его
    строки: сканер ниже нашёл бы «новый путь запуска claude» в функции, где
    его нет. Граница по вложенным def — вложенная функция отвечает за себя.
    """
    out: set[str] = set()
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
        stack.extend(ast.iter_child_nodes(node))
    return out


def test_cloud_loop_checks_the_switch():
    """Тред, который запускает `claude -p`, обязан спросить выключатель сам."""
    fn = _func(SRC / "daemon.py", "cloud_loop")
    assert "cloud_live" in _names(fn), (
        "cloud_loop отправляет стенограмму, ни разу не посмотрев на cloud_live: "
        "ручной запрос (кнопка «Claude», ⌘⇧⏎ → stdin `cloud`) идёт мимо выключателя")


def test_cloud_loop_respects_the_live_toggle():
    fn = _func(SRC / "daemon.py", "cloud_loop")
    assert "toggles" in _names(fn), "живой тумблер UI не спрашивают перед отправкой"


@pytest.mark.parametrize("filename,func", NETWORK_EXITS)
def test_api_key_is_stripped_before_calling_claude(filename, func):
    """Только подписка. Ключ в env увёл бы вызов на потокенный биллинг.

    Не просто «литерал упомянут» — иначе setdefault("ANTHROPIC_API_KEY", …)
    прошёл бы сторожа. Требуется сравнение на НЕравенство с этим именем:
    форма фильтра `if k != "ANTHROPIC_API_KEY"`.
    """
    fn = _func(SRC / filename, func)
    strips = any(
        isinstance(node, ast.Compare)
        and any(isinstance(op, ast.NotEq) for op in node.ops)
        and "ANTHROPIC_API_KEY" in _consts(node)
        for node in ast.walk(fn))
    assert strips, \
        f"{filename}:{func} зовёт claude без фильтра k != ANTHROPIC_API_KEY в env"


def _mentions_claude(consts: set[str]) -> bool:
    return any(c.startswith("claude") or c.endswith("/claude") for c in consts)


def test_no_other_place_starts_claude():
    """Новый выход в сеть должен попасть в список выше, а не появиться тихо.

    Сторож обязан видеть все три укрытия, где может родиться вызов:
    обычные def, async def (у корутины те же права запустить процесс) и
    модульный уровень (константа с командой вне всякой функции). И не
    только src/ — scripts/ ходят теми же дорогами.
    """
    known = {f"{f}:{fn}" for f, fn in NETWORK_EXITS}
    found = set()
    for root in (SRC, SRC.parent / "scripts"):
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and _mentions_claude(_own_consts(node)):
                    found.add(f"{path.name}:{node.name}")
            top: set[str] = set()
            stack = list(ast.iter_child_nodes(tree))
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    top.add(node.value)
                stack.extend(ast.iter_child_nodes(node))
            if _mentions_claude(top):
                found.add(f"{path.name}:<module>")
    unknown = sorted(found - known)
    assert not unknown, (
        "появился новый путь запуска claude: " + ", ".join(unknown)
        + " — добавьте его в NETWORK_EXITS и убедитесь, что там есть выключатель")
