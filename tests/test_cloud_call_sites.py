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
REPO = SRC.parent

# Реестр выходов в сеть: (путь от корня репозитория, функция) → вид выхода.
# Вид решает, какой предикат у выхода: `subprocess` — запуск `claude` с
# вычищенным env и бинарём из резолвера; `sdk` — клиент Tinfoil в процессе
# (конструктор и есть аттестация, сеть). Ключ — путь, а не имя файла: два
# `main` в разных папках не должны склеиваться (№792, in3 K7).
EXITS = {
    ("src/daemon.py", "cloud_loop"): "subprocess",
    # 05.08: облачная дописка к подсказке стала ревизором нити — то же место
    # выхода в сеть, новое имя и новая работа (правки строк вместо блока «☁️»).
    ("src/daemon.py", "cloud_thread_refine"): "subprocess",
    # разбор после встречи переехал из graph_updater.main в отдельный воркер:
    # тот ждёт claude с таймаутом, проверяет ответ и держит границы правок.
    # graph_updater теперь запускает питон, а не claude, — выходом в сеть быть
    # перестал, и держать его в списке значило бы охранять пустое место.
    # run() берёт замок графа и решает режим; сам вызов claude — в _run_locked.
    ("scripts/cloud_review.py", "_run_locked"): "subprocess",
    ("scripts/nightly_claude_cores.py", "main"): "subprocess",
    ("scripts/nightly_dossier_review.py", "review"): "subprocess",
    # ночная ревизия у Tinfoil (№792): аттестующий конструктор и поток ответа
    ("src/night_cloud.py", "_attest"): "sdk",
    ("src/night_cloud.py", "send"): "sdk",
}
NETWORK_EXITS = tuple(k for k, kind in EXITS.items() if kind == "subprocess")
SDK_EXITS = tuple(k for k, kind in EXITS.items() if kind == "sdk")
# Пакеты сетевого клиента Tinfoil: импортирует их только дверь, и только
# внутри функций за гейтом — верхний уровень двери тянут тесты без SDK.
SDK_PACKAGES = ("tinfoil", "openai", "httpx")
SDK_DOOR = "src/night_cloud.py"

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


def _is_sdk_call(node: ast.AST) -> bool:
    """Сетевой вызов SDK Tinfoil: аттестующий конструктор или отправка чата."""
    if not isinstance(node, ast.Call):
        return False
    func = ast.unparse(node.func)
    return func == "TinfoilAI" or func.endswith(".TinfoilAI") \
        or func.endswith(".chat.completions.create")


def _guarded_subprocess_calls(fn: ast.AST) -> list[tuple[ast.Call, bool]]:
    return _guarded_calls(fn, _is_subprocess_call)


def _guarded_calls(fn: ast.AST, is_exit) -> list[tuple[ast.Call, bool]]:
    """Сетевые вызовы функции и доказан ли privacy-гейт на их пути.

    Это маленький анализ потока управления, а не поиск по файлу. Разрешение
    переносится внутрь положительной ветки `if privacy.…` либо за ранний
    fail-closed выход `if not privacy.…: return/continue`. Вложенные функции
    не наследуют доказательство: каждая точка выхода отвечает за себя.
    """
    found: list[tuple[ast.Call, bool]] = []

    def visit_expr(node: ast.AST, permitted: bool) -> None:
        if is_exit(node):
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
    fn = _func(REPO / "src/daemon.py", "cloud_loop")
    assert "toggles" in _names(fn), "живой тумблер UI не спрашивают перед отправкой"


def _strips_the_key(node: ast.AST) -> bool:
    """Выражение — это словарь окружения, из которого вычищен ключ: сам
    фильтр `k != "ANTHROPIC_API_KEY"` или вызов канонического сборщика
    `cloud.cli_env()` — его тело проверяет test_cli_env_itself_strips_the_key,
    а выходы обязаны идти в том же окружении, что зонд (DS r2 по #499)."""
    if isinstance(node, ast.Call) and ast.unparse(node.func) in ("cloud.cli_env", "cli_env") \
            and not node.args and not node.keywords:
        return True
    return any(
        isinstance(n, ast.Compare)
        and any(isinstance(op, ast.NotEq) for op in n.ops)
        and "ANTHROPIC_API_KEY" in _consts(n)
        for n in ast.walk(node))


def test_cli_env_itself_strips_the_key():
    """Канонический сборщик окружения, на который ссылаются выходы, сам
    вычищает ключ — иначе доверие к нему в _strips_the_key пустое."""
    fn = _func(SRC / "cloud.py", "cli_env")
    assert _strips_the_key(fn), "cloud.cli_env больше не фильтрует ANTHROPIC_API_KEY"


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
    path = REPO / filename
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
    path = REPO / filename
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


def _calls_claude_bin(fn: ast.AST) -> bool:
    """Вызов cloud.claude_bin() — канонический способ добыть бинарь (D-П4).

    После свода резолвера точки выхода не содержат строкового литерала —
    сторож, ловивший только константы, ослеп бы на новый выход в этом же,
    теперь каноническом, стиле (круг-1 по #406, DS). Видим оба написания:
    cloud.claude_bin() и голый claude_bin() после from-импорта. Граница
    по вложенным def — та же, что у _own_consts: cloud_thread_refine
    объявлен внутри main, и его вызов не должен приписываться родителю.
    """
    body = fn.body if hasattr(fn, "body") else [fn]
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "claude_bin":
                return True
            if isinstance(f, ast.Name) and f.id == "claude_bin":
                return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def test_the_guard_still_sees_a_launch_and_ignores_a_model_name():
    """Сторож различает запуск процесса и имя модели — проверяем оба случая."""
    for launcher in ("claude", "/opt/homebrew/bin/claude", "claude -p prompt"):
        assert _mentions_claude({launcher}), f"пропущен запуск: {launcher}"
    for model in ("claude-opus-5", "claude-haiku-4-5", "claude-haiku-4-5-20251001"):
        assert not _mentions_claude({model}), f"имя модели принято за запуск: {model}"


def test_the_guard_sees_the_resolver_call_style():
    """Канонический стиль D-П4 — без литерала — сторож тоже видит.

    И не приписывает вызов вложенной функции её родителю: за вложенный
    def отвечает он сам (круг-1 по #406, DS).
    """
    import textwrap
    fn = ast.parse(textwrap.dedent("""
        def leaked(cfg):
            claude = cloud.claude_bin()
            return claude
    """)).body[0]
    assert _calls_claude_bin(fn)
    fn = ast.parse(textwrap.dedent("""
        def bare(cfg):
            return claude_bin()
    """)).body[0]
    assert _calls_claude_bin(fn)
    parent = ast.parse(textwrap.dedent("""
        def outer(cfg):
            def inner():
                return cloud.claude_bin()
            return inner
    """)).body[0]
    assert not _calls_claude_bin(parent)
    assert _calls_claude_bin(parent.body[0])


def test_no_other_place_starts_claude():
    """Новый выход в сеть должен попасть в список выше, а не появиться тихо.

    Сторож обязан видеть все три укрытия, где может родиться вызов:
    обычные def, async def (у корутины те же права запустить процесс) и
    модульный уровень (константа с командой вне всякой функции). И не
    только src/ — scripts/ ходят теми же дорогами.
    """
    known = {f"{f}:{fn}" for f, fn in NETWORK_EXITS}
    # Резолвер пути к CLI (D-П4) упоминает бинарь, но сеть не трогает и
    # запуска не содержит — регистрировать его выходом значило бы требовать
    # выключатель у функции без запроса. Его охраняет test_claude_resolver.py.
    known.add("src/cloud.py:claude_bin")
    # Зонд `claude --version` (аудит 05.09) запускает CLI, но ничего не
    # отправляет: версия печатается локально. Выключатель — у вызывающих
    # ночных шагов, которые зовут зонд после privacy.cloud_enrich_enabled.
    known.add("src/cloud.py:probe_claude")
    known.add("src/cloud.py:claude_bin_checked")
    # Рецепт регистрации MCP-сервера (№336): `claude mcp add …` — текст,
    # который владелец копирует в свой терминал; сервер его не запускает и
    # процессов не порождает, в сеть ничего не уходит.
    known.add("src/mcp_server.py:_registration")
    found = set()
    resolved = set()
    for root in (SRC, SRC.parent / "scripts"):
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                key = f"{path.relative_to(REPO)}:{node.name}"
                if _calls_claude_bin(node):
                    resolved.add(key)
                if _mentions_claude(_own_consts(node)) or key in resolved:
                    found.add(key)
            top: set[str] = set()
            stack = list(ast.iter_child_nodes(tree))
            while stack:
                node = stack.pop()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    top.add(node.value)
                stack.extend(ast.iter_child_nodes(node))
            if _mentions_claude(top) or _calls_claude_bin(tree):
                found.add(f"{path.relative_to(REPO)}:<module>")
    unknown = sorted(found - known)
    assert not unknown, (
        "появился новый путь запуска claude: " + ", ".join(unknown)
        + " — добавьте его в NETWORK_EXITS и убедитесь, что там есть выключатель")
    # Обратная сторона (идея из #407): зарегистрированный выход ОБЯЗАН
    # добывать бинарь резолвером — вернувшийся литерал или личный
    # shutil.which в известной точке тоже поломка дедупа, а не только
    # новый тихий выход.
    missing = sorted({f"{f}:{fn}" for f, fn in NETWORK_EXITS} - resolved)
    assert not missing, (
        "известный выход обязан звать cloud.claude_bin(), а не держать "
        "свою копию резолва: " + ", ".join(missing))


# ── Выходы вида `sdk` (№792): клиент Tinfoil в процессе, без `claude` ──


@pytest.mark.parametrize("filename,func", SDK_EXITS)
def test_sdk_exit_is_dominated_by_the_privacy_gate(filename, func):
    """Каждый сетевой вызов SDK перекрыт privacy-гейтом на своём пути.

    Предикат — доминирование, а не порядок текста (in3 DS-I8): гейт во
    вложенном `if` или в соседней функции выход не закрывает.
    """
    fn = _func(REPO / filename, func)
    calls = _guarded_calls(fn, _is_sdk_call)
    assert calls, f"{filename}:{func} числится выходом SDK, но не зовёт ни TinfoilAI, ни .chat.completions.create"
    unsafe = [ast.unparse(call) for call, guarded in calls if not guarded]
    assert not unsafe, f"{filename}:{func}: вызов SDK не перекрыт privacy-гейтом: {unsafe}"
    own = _own_consts(fn)
    assert not (own & set(KILL_SWITCH_NAMES)), f"{filename}:{func} проверяет имя рубильника вручную"


@pytest.mark.parametrize("source,expected", [
    ("""
def exit(cfg, key):
    if not privacy.cloud_enrich_enabled(cfg):
        raise privacy.PrivacyRefused("нет")
    return TinfoilAI(api_key=key)
""", True),
    ("""
def exit(cfg, key, diagnostic):
    if diagnostic:
        if not privacy.cloud_enrich_enabled(cfg):
            raise privacy.PrivacyRefused("нет")
    return TinfoilAI(api_key=key)
""", False),
    ("""
def exit(cfg, api):
    stream = api.chat.completions.create(model="m", messages=[])
    if not privacy.cloud_enrich_enabled(cfg):
        return
    return stream
""", False),
    ("""
def exit(cfg, key):
    if privacy.cloud_enrich_enabled(cfg):
        pass
    return tinfoil.TinfoilAI(api_key=key)
""", False),
])
def test_sdk_guard_is_tied_to_the_call_path(source, expected):
    """Мутации гейта для вида sdk — те же, что для запуска процесса."""
    fn = next(n for n in ast.walk(ast.parse(source))
              if isinstance(n, ast.FunctionDef) and n.name == "exit")
    calls = _guarded_calls(fn, _is_sdk_call)
    assert len(calls) == 1
    assert calls[0][1] is expected


def _py_files():
    for root in (SRC, REPO / "scripts"):
        yield from sorted(root.rglob("*.py"))


def _sdk_imports(tree: ast.AST) -> list[tuple[ast.AST, str]]:
    """Импорты пакетов SDK по корневому имени, включая importlib и __import__
    с литералом: `import httpx as h`, `from openai.types import X`,
    `importlib.import_module("tinfoil.client")` (in3 DS-I9)."""
    out = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) \
                and ast.unparse(node.func) in ("importlib.import_module", "import_module", "__import__"):
            names = [node.args[0].value]
        for name in names:
            if name.split(".")[0] in SDK_PACKAGES:
                out.append((node, name))
    return out


def test_sdk_packages_are_imported_only_inside_the_door():
    """Пакеты SDK импортирует только дверь — и только внутри функций.

    Импорт на верхнем уровне двери сделал бы её неимпортируемой без SDK (CI,
    тесты, машина без extra), а импорт в другом модуле — второй путь в сеть
    мимо гейта (in3 K6, DS-I9).
    """
    offenders = []
    for path in _py_files():
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits = _sdk_imports(tree)
        if rel != SDK_DOOR:
            offenders += [f"{rel}:{node.lineno} {name}" for node, name in hits]
            continue
        inside = {id(n) for f in ast.walk(tree)
                  if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(f)}
        offenders += [f"{rel}:{node.lineno} {name} (верхний уровень)"
                      for node, name in hits if id(node) not in inside]
    assert not offenders, "импорт SDK вне функций двери: " + ", ".join(offenders)


def test_the_import_detector_sees_every_shape():
    """Сторож импорта сам нуждается в тесте: каждая форма ловится."""
    for src in ("import httpx", "import httpx as h", "from openai.types import X",
                "import tinfoil.client", "importlib.import_module('tinfoil')",
                "__import__('openai')", "from tinfoil import TinfoilAI"):
        assert _sdk_imports(ast.parse(src)), src
    for src in ("import requests", "from .openai_like import x", "__import__(name)",
                "import httpx_mock_free_name"):
        assert not _sdk_imports(ast.parse(src)), src


def test_no_other_place_calls_the_sdk():
    """TinfoilAI и .chat.completions.create — только в зарегистрированных функциях.

    Обратная сторона реестра: новый вызов SDK в другом месте — новый выход в
    сеть, и он обязан попасть в EXITS со своим гейтом, а не появиться тихо.
    """
    known = {f"{f}:{fn}" for f, fn in SDK_EXITS}
    found = set()
    for path in _py_files():
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner: dict[int, str] = {}
        for f in ast.walk(tree):
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(f):
                    owner[id(n)] = f.name   # внутренний def перезапишет внешний: обход сверху вниз
        for node in ast.walk(tree):
            if _is_sdk_call(node):
                found.add(f"{rel}:{owner.get(id(node), '<module>')}")
    unknown = sorted(found - known)
    assert not unknown, "вызов SDK вне реестра: " + ", ".join(unknown)
    assert known <= found, "зарегистрированный выход SDK не зовёт SDK: " + ", ".join(sorted(known - found))
