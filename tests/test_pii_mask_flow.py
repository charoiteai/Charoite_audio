"""Маска стоит на пути данных, а не рядом (№793, этап A).

В четырёх местах, где текст встречи уходит в облако после встречи и ночью,
транспорт получает только `<m>.text`, где `m` — единственная привязка
`cloud_door.mask_prompt(...)` в той же функции, сделанная раньше отправки.
Тинфойл ночи ядер получает текст только через `_ask_tinfoil`, а его зовут с
`<m>.text`. Структурно, по AST — как соседний `test_cloud_call_sites.py`:
проверка «в промпт не ушёл сырой текст» поведенчески требовала бы облака.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: (файл, функция) → как транспорт берёт промпт: `-p` в списке аргументов CLI
#: или ключ `prompt=` у сборщика команды.
MASKED_EXITS = {
    ("scripts/cloud_review.py", "_run_locked"): "prompt_kw",
    ("scripts/nightly_claude_cores.py", "main"): "dash_p",
    ("scripts/nightly_dossier_review.py", "review"): "dash_p",
}
TINFOIL = ("scripts/nightly_claude_cores.py", "_ask_tinfoil")
#: Выходы без маски — этап C №793: живые подсказки держит выключенный ключ облака
#: (сказано в PRIVACY). SDK-выходы Tinfoil берут уже маскированный текст из `_ask_tinfoil`.
UNMASKED_STAGE_C = {
    ("src/daemon.py", "cloud_loop"),
    ("src/daemon.py", "cloud_thread_refine"),
}


def test_every_network_exit_is_masked_or_named_as_stage_c():
    """Один реестр выходов — `test_cloud_call_sites.EXITS`: новый выход без маски
    и без строки здесь краснеет (n793-out1 DS-10)."""
    import test_cloud_call_sites as sites
    exits = set(sites.NETWORK_EXITS)
    assert exits == set(MASKED_EXITS) | UNMASKED_STAGE_C, exits ^ (set(MASKED_EXITS) | UNMASKED_STAGE_C)
    assert not set(MASKED_EXITS) & UNMASKED_STAGE_C


def _tree(rel: str) -> ast.Module:
    return ast.parse((REPO / rel).read_text(encoding="utf-8"))


def _func(rel: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(_tree(rel)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"{rel}: функция {name} не найдена")


def _is_mask_call(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "mask_prompt" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "cloud_door")


def _mask_binding(fn: ast.FunctionDef) -> tuple[str, int]:
    """Имя единственной привязки `x = cloud_door.mask_prompt(...)` и её строка."""
    binds = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and _is_mask_call(n.value)]
    assert len(binds) == 1, f"{fn.name}: привязок mask_prompt {len(binds)}, нужна одна"
    (target,) = binds[0].targets
    assert isinstance(target, ast.Name), f"{fn.name}: mask_prompt — в простое имя"
    name = target.id
    other = [n for n in ast.walk(fn)
             if isinstance(n, (ast.Name,)) and n.id == name and isinstance(n.ctx, ast.Store)
             and n is not target]
    assert not other, f"{fn.name}: имя {name} переприсваивается после маски"
    return name, binds[0].lineno


def _is_masked_text(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "text"
            and isinstance(node.value, ast.Name) and node.value.id == name)


def _prompt_args(fn: ast.FunctionDef, how: str) -> list[ast.AST]:
    out: list[ast.AST] = []
    for node in ast.walk(fn):
        if how == "dash_p" and isinstance(node, ast.List):
            for i, el in enumerate(node.elts[:-1]):
                if isinstance(el, ast.Constant) and el.value == "-p":
                    out.append(node.elts[i + 1])
        if how == "prompt_kw" and isinstance(node, ast.Call):
            out.extend(k.value for k in node.keywords if k.arg == "prompt")
    return out


@pytest.mark.parametrize("where, how", sorted(MASKED_EXITS.items()))
def test_transport_gets_only_masked_text(where, how):
    fn = _func(*where)
    name, line = _mask_binding(fn)
    args = _prompt_args(fn, how)
    assert args, f"{where}: не нашёл, где промпт уходит в транспорт — сторож ослеп"
    for a in args:
        assert _is_masked_text(a, name), f"{where}:{a.lineno}: в транспорт уходит не {name}.text"
        assert a.lineno > line, f"{where}:{a.lineno}: отправка раньше маски"


def test_tinfoil_is_called_only_with_masked_text():
    fn = _func("scripts/nightly_claude_cores.py", "main")
    name, line = _mask_binding(fn)
    calls = [n for n in ast.walk(_tree(TINFOIL[0]))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == TINFOIL[1]]
    assert calls, "вызов _ask_tinfoil не найден — сторож ослеп"
    for c in calls:
        assert len(c.args) >= 2 and _is_masked_text(c.args[1], name) and c.lineno > line, \
            f"_ask_tinfoil:{c.lineno}: промпт не {name}.text"


def _send_calls(node: ast.AST, where: str, out: list) -> None:
    """Вызовы `night_cloud.send` с ближайшей объемлющей функцией (или `<module>`)."""
    for child in ast.iter_child_nodes(node):
        inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else where
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                and child.func.attr == "send" and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "night_cloud"):
            out.append(where)
        _send_calls(child, inner, out)


def test_night_cloud_send_is_reached_only_through_ask_tinfoil():
    callers = []
    for path in (*(REPO / "src").rglob("*.py"), *(REPO / "scripts").rglob("*.py")):
        found: list[str] = []
        _send_calls(ast.parse(path.read_text(encoding="utf-8")), "<module>", found)
        callers.extend((str(path.relative_to(REPO)), f) for f in found)
    assert set(callers) == {TINFOIL}, f"night_cloud.send зовут мимо маски: {callers}"


def test_reply_is_unmasked_before_it_is_written():
    """Ответ модели идёт через `unmask_reply` в каждом из мест."""
    for where in MASKED_EXITS:
        fn = _func(*where)
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "unmask_reply"]
        assert calls, f"{where}: ответ не разворачивается из ID"


@pytest.mark.parametrize("where", sorted(w for w, how in MASKED_EXITS.items() if how == "dash_p"))
def test_cli_exit_under_mask_runs_in_the_empty_workdir(where):
    """CLI кладёт cwd в системный промпт: вызов `claude -p` ночи задаёт `cwd=` с
    `cloud_door.empty_workdir()`, иначе в облако уходит путь кода (n793-final-opus M1)."""
    fn = _func(*where)
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and n.args
             and isinstance(n.args[0], ast.List) and _prompt_args(n.args[0], "dash_p")]
    assert calls, f"{where}: не нашёл вызова CLI — сторож ослеп"
    for c in calls:
        cwd = next((k.value for k in c.keywords if k.arg == "cwd"), None)
        assert cwd is not None and "empty_workdir" in ast.unparse(cwd), f"{where}:{c.lineno}: cwd не пустой каталог"
