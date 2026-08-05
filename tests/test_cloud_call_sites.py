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
import re

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

# (файл, функция, что должно упоминаться внутри)
NETWORK_EXITS = (
    ("daemon.py", "cloud_loop"),
    # 05.08: облачная дописка к подсказке стала ревизором нити — то же место
    # выхода в сеть, новое имя и новая работа (правки строк вместо блока «☁️»).
    ("daemon.py", "cloud_thread_refine"),
    # разбор после встречи переехал из graph_updater.main в отдельный воркер:
    # тот ждёт claude с таймаутом, проверяет ответ и держит границы правок.
    # graph_updater теперь запускает питон, а не claude, — выходом в сеть быть
    # перестал, и держать его в списке значило бы охранять пустое место.
    ("cloud_review.py", "run"),
    ("nightly_claude_cores.py", "main"),
    ("nightly_dossier_review.py", "review"),
)

# Оба имени одного рубильника. Точка выхода не должна упоминать их сама —
# знать их обязан только src/privacy.py.
KILL_SWITCH_NAMES = ("CHAROITE_NO_CLOUD", "SUFLER_NO_CLOUD")


def _func(path: pathlib.Path, name: str) -> ast.FunctionDef:
    if not path.exists():   # выходы живут и в scripts/, не только в src/
        path = path.parent.parent / "scripts" / path.name
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


_PRIVACY_GATES = ("cloud_live_enabled", "cloud_hints_enabled",
                  "cloud_enrich_enabled", "cloud_edit_graph_enabled")
_LAUNCHERS = ("run", "Popen", "check_output", "check_call")


def _privacy_polarity(test: ast.AST) -> bool | None:
    """True: ветка разрешена privacy; False: ветка — отказ; None: не гейт.

    Принимаем только прямой вызов privacy или его `not`. Это намеренно уже,
    чем поиск упоминания: `False and privacy.…` и имя, присвоенное где-то в
    модуле, не доказывают, что конкретный сетевой вызов закрыт.
    """
    if isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute) \
            and test.func.attr in _PRIVACY_GATES:
        return True
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        nested = _privacy_polarity(test.operand)
        return None if nested is None else not nested
    return None


def _always_exits(body: list[ast.stmt]) -> bool:
    """Заканчивает ли ветка текущий путь до следующих операторов блока."""
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.If):
        return _always_exits(last.body) and _always_exits(last.orelse)
    return False


def _is_subprocess_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
        and node.func.attr in _LAUNCHERS


def _guarded_subprocess_calls(fn: ast.AST) -> list[tuple[ast.Call, bool]]:
    """Сетевые вызовы функции и доказан ли privacy-гейт на их пути.

    Это маленький анализ потока управления, а не поиск по файлу. Разрешение
    переносится внутрь положительной ветки `if privacy.…` либо за ранний
    fail-closed выход `if not privacy.…: return/continue`. Вложенные функции
    не наследуют доказательство: каждая точка выхода отвечает за себя.
    """
    found: list[tuple[ast.Call, bool]] = []

    def visit_expr(node: ast.AST, permitted: bool) -> None:
        if _is_subprocess_call(node):
            found.append((node, permitted))
        if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            return
        for child in ast.iter_child_nodes(node):
            visit_expr(child, permitted)

    def visit_stmt(stmt: ast.stmt, permitted: bool) -> None:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return

        # Сначала выражения самого оператора, но не вложенные блоки stmt.
        for child in ast.iter_child_nodes(stmt):
            if isinstance(child, (ast.stmt, ast.ExceptHandler)):
                continue
            visit_expr(child, permitted)

        if isinstance(stmt, ast.If):
            polarity = _privacy_polarity(stmt.test)
            visit_block(stmt.body, permitted or polarity is True)
            visit_block(stmt.orelse, permitted or polarity is False)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            visit_block(stmt.body, permitted)
            visit_block(stmt.orelse, permitted)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            visit_block(stmt.body, permitted)
        elif isinstance(stmt, ast.Try):
            visit_block(stmt.body, permitted)
            for handler in stmt.handlers:
                visit_block(handler.body, permitted)
            visit_block(stmt.orelse, permitted)
            visit_block(stmt.finalbody, permitted)
        elif isinstance(stmt, ast.Match):
            for case in stmt.cases:
                if case.guard is not None:
                    visit_expr(case.guard, permitted)
                visit_block(case.body, permitted)

    def visit_block(body: list[ast.stmt], permitted: bool) -> None:
        flowing = permitted
        for stmt in body:
            visit_stmt(stmt, flowing)
            if not isinstance(stmt, ast.If):
                continue
            polarity = _privacy_polarity(stmt.test)
            if polarity is False and _always_exits(stmt.body):
                flowing = True
            elif polarity is True and _always_exits(stmt.orelse):
                flowing = True

    visit_block(fn.body, False)
    return found


def _subprocess_calls(fn: ast.AST) -> list[ast.Call]:
    return [call for call, _guarded in _guarded_subprocess_calls(fn)]


def test_cloud_loop_respects_the_live_toggle():
    fn = _func(SRC / "daemon.py", "cloud_loop")
    assert "toggles" in _names(fn), "живой тумблер UI не спрашивают перед отправкой"


def _strips_the_key(node: ast.AST) -> bool:
    """Выражение — это словарь окружения, из которого вычищен ключ."""
    return any(
        isinstance(n, ast.Compare)
        and any(isinstance(op, ast.NotEq) for op in n.ops)
        and "ANTHROPIC_API_KEY" in _consts(n)
        for n in ast.walk(node))


def _bindings(scope: ast.AST, name: str) -> list[ast.AST]:
    """Все выражения, которые присваивались этому имени в области видимости."""
    out = []
    for n in ast.walk(scope):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets):
            out.append(n.value)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and \
                isinstance(n.target, ast.Name) and n.target.id == name:
            out.append(n.value)
    return out


@pytest.mark.parametrize("filename,func", NETWORK_EXITS)
def test_api_key_is_stripped_before_calling_claude(filename, func):
    """Только подписка. Ключ в env увёл бы вызов на потокенный биллинг.

    Сторож смотрит на АРГУМЕНТ вызова, а не на присутствие фильтра в теле
    функции. Прежняя версия проверяла, что где-то внутри есть сравнение
    `k != "ANTHROPIC_API_KEY"`, — и пропускала мутацию, при которой фильтр
    остаётся на месте нетронутым, а в процесс уходит другой словарь:

        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        subprocess.run([claude_bin, ...], env=os.environ)   # 488 passed

    Ровно это и происходит при неудачном merge или рефакторинге, и ровно
    это переводит продукт с подписки на потокенный биллинг.
    """
    path = SRC / filename
    if not path.exists():
        path = path.parent.parent / "scripts" / path.name
    module = ast.parse(path.read_text(encoding="utf-8"))
    fn = _func(path, func)

    calls = _subprocess_calls(fn)
    assert calls, f"{filename}:{func} числится выходом в сеть, но никого не запускает"

    for call in calls:
        env_kw = next((k for k in call.keywords if k.arg == "env"), None)
        assert env_kw is not None, (
            f"{filename}:{func} запускает процесс без env= — дочерний процесс "
            f"наследует окружение целиком, вместе с ANTHROPIC_API_KEY")
        assert isinstance(env_kw.value, ast.Name), (
            f"{filename}:{func} передаёт env={ast.unparse(env_kw.value)}: это не "
            f"вычищенный словарь, а окружение как есть")

        name = env_kw.value.id
        bound = _bindings(fn, name) or _bindings(module, name)
        assert bound, f"{filename}:{func} передаёт env={name}, но такого имени нигде не присваивают"
        for value in bound:
            assert _strips_the_key(value), (
                f"{filename}:{func}: env={name} присваивают "
                f"«{ast.unparse(value)}» — без фильтра k != ANTHROPIC_API_KEY")


@pytest.mark.parametrize("filename,func", NETWORK_EXITS)
def test_switch_is_asked_through_privacy(filename, func):
    """Выключатель спрашивают у privacy, а не проверяют env своими руками.

    Сторож знал две вещи — что точка выхода зарегистрирована и что ключ
    вычищается, — но не знал третьей: КАК спрошено разрешение. В
    nightly_claude_cores стояла своя проверка `os.environ.get("SUFLER_NO_CLOUD")`,
    и после переименования проекта она не увидела CHAROITE_NO_CLOUD: ночная
    ревизия отправляла граф в Anthropic при выключенном рубильнике. Имён у
    рубильника два, и знать их обязано одно место — src/privacy.py.
    """
    path = SRC / filename
    if not path.exists():
        path = path.parent.parent / "scripts" / path.name
    fn = _func(path, func)
    launches = _guarded_subprocess_calls(fn)
    assert launches, f"{filename}:{func} числится выходом в сеть, но никого не запускает"
    unsafe = [ast.unparse(call) for call, guarded in launches if not guarded]
    assert not unsafe, (
        f"{filename}:{func}: конкретный запуск не перекрыт privacy-гейтом на "
        f"своём пути управления: {unsafe}. Проверка в соседней функции или "
        "несвязанной ветке не защищает сетевой выход")

    own = _own_consts(fn)
    assert not (own & set(KILL_SWITCH_NAMES)), (
        f"{filename}:{func} проверяет имя рубильника вручную: "
        f"{sorted(own & set(KILL_SWITCH_NAMES))}. Имена живут в privacy.KILL_SWITCHES — "
        f"своя проверка знает одно имя из двух и пропускает второе")


@pytest.mark.parametrize("source,expected", [
    ("""
def exit(cfg):
    if not privacy.cloud_enrich_enabled(cfg):
        return
    subprocess.run([\"claude\"])
""", True),
    ("""
def exit(cfg, diagnostic):
    if diagnostic:
        if not privacy.cloud_enrich_enabled(cfg):
            return
    subprocess.run([\"claude\"])
""", False),
    ("""
def exit(cfg):
    subprocess.run([\"claude\"])
    if not privacy.cloud_enrich_enabled(cfg):
        return
""", False),
    ("""
def exit(cfg):
    if False and not privacy.cloud_enrich_enabled(cfg):
        return
    subprocess.run([\"claude\"])
""", False),
])
def test_privacy_guard_is_tied_to_the_launch_path(source, expected):
    """Мутации: упоминание privacy рядом больше не сохраняет тест зелёным."""
    fn = next(n for n in ast.walk(ast.parse(source))
              if isinstance(n, ast.FunctionDef) and n.name == "exit")
    launches = _guarded_subprocess_calls(fn)
    assert len(launches) == 1
    assert launches[0][1] is expected


def test_privacy_knows_both_switch_names():
    """Список имён рубильника — единственный источник правды."""
    src = (SRC / "privacy.py").read_text(encoding="utf-8")
    for name in KILL_SWITCH_NAMES:
        assert name in src, f"privacy.py не знает про {name}"


# Имя модели — не бинарник: «claude-opus-5» запустить нельзя. Сторож ловит
# строку, которой стартует процесс, и одно от другого обязан отличать, иначе
# модуль, который ТОЛЬКО называет модель (src/cloud.py — одно место для
# дефолтов вместо литерала в каждой точке выхода), объявляется новым выходом в
# сеть и с него требуется фильтр ANTHROPIC_API_KEY. Покрытие при этом не
# сужается: исключаются ровно строки вида claude-<версия>, а «claude»,
# «/opt/homebrew/bin/claude» и «claude -p …» ловятся по-прежнему.
_MODEL_NAME = re.compile(r"^claude-[a-z0-9.\-]+$")


def _mentions_claude(consts: set[str]) -> bool:
    return any((c.startswith("claude") or c.endswith("/claude"))
               and not _MODEL_NAME.match(c) for c in consts)


def test_the_guard_still_sees_a_launch_and_ignores_a_model_name():
    """Сторож различает запуск процесса и имя модели — проверяем оба случая."""
    for launcher in ("claude", "/opt/homebrew/bin/claude", "claude -p prompt"):
        assert _mentions_claude({launcher}), f"пропущен запуск: {launcher}"
    for model in ("claude-opus-5", "claude-haiku-4-5", "claude-haiku-4-5-20251001"):
        assert not _mentions_claude({model}), f"имя модели принято за запуск: {model}"


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
