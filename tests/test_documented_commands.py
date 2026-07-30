"""Команда из документации обязана работать в том окружении, что README создаёт.

README ставит зависимости в `.venv` (`python3 -m venv .venv && .venv/bin/pip
install -r requirements.txt`), а дальше предлагает запускать скрипты системным
`python3`, у которого этих пакетов нет. Проверено руками в чистом окружении:

    $ python3 scripts/memory_bench.py --demo
    ModuleNotFoundError: No module named 'yaml'

Это первая команда, которой продукт показывают до первой встречи («ещё нет
встреч? одна команда проверяет весь контур»), и вторая — импорт старых записей.
То есть трейсбек стоит ровно там, где складывается первое впечатление, а
установка — известная причина, по которой люди бросают локальные инструменты.

`scripts/doctor.py` — исключение по замыслу: он написан без внешних
зависимостей именно затем, чтобы работать любым питоном и рассказать, чего не
хватает. Поэтому правило не «всюду .venv», а «команда исполнима тем
интерпретатором, который в ней написан».

Второе требование файла: скрипт, названный в пользовательской документации,
при запуске не тем питоном обязан объяснить, что делать, а не падать
трейсбеком. Человек копирует команды из статей, чатов и старых версий README —
и продукт, который на это отвечает стеной трассировки, теряет его насовсем.
"""
import ast
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# «python3 scripts/foo.py …», «python src/bar.py …», «.venv/bin/python scripts/foo.py»
_CMD = re.compile(r"(?P<runner>\.venv/bin/python|python3?)\s+(?P<script>(?:src|scripts)/[\w./-]+\.py)")

# Документация для пользователя: её читают до того, как разберутся в устройстве.
USER_DOCS = ("README.md", "README.ru.md", "README.zh.md",
             "docs/SETUP.md", "docs/SETUP.ru.md", "docs/SETUP.zh.md",
             "demo/README.md", "demo/README.ru.md", "demo/README.zh.md")

_LOCAL = {p.stem for p in (REPO / "src").glob("*.py")} | \
         {p.stem for p in (REPO / "scripts").glob("*.py")}


def _module_level_imports(path: pathlib.Path) -> set[str]:
    """Внешние пакеты, которые нужны СРАЗУ при запуске файла.

    Импорты внутри функций не считаются: они выполняются, только если до них
    дошли, и скрипт успевает сказать что-то человеческое. Именно так устроен
    doctor.py.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in tree.body:                      # только верхний уровень
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
        elif isinstance(node, ast.Try):         # защищённый импорт с рецептом
            continue
    return {m for m in out
            if m not in sys.stdlib_module_names and m not in _LOCAL}


def _documented_commands() -> list[tuple[str, int, str, str, pathlib.Path]]:
    found = []
    for doc in USER_DOCS:
        path = REPO / doc
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for m in _CMD.finditer(line):
                script = REPO / m.group("script")
                if script.exists():
                    found.append((doc, i, m.group("runner"), m.group("script"), script))
    return found


def test_the_scanner_finds_commands_at_all():
    """Сканер по документации — сам нуждается в проверке: пустой список молча
    сделал бы зелёным любой тест ниже."""
    cmds = _documented_commands()
    assert len(cmds) >= 6, f"команды в документации перестали находиться: {cmds}"
    assert {c[3] for c in cmds} >= {"scripts/doctor.py", "scripts/memory_bench.py"}


def test_documented_commands_work_in_the_environment_the_docs_create():
    broken = []
    for doc, line, runner, script, path in _documented_commands():
        needs = _module_level_imports(path)
        if needs and runner != ".venv/bin/python":
            broken.append(f"{doc}:{line}: `{runner} {script}` — "
                          f"нужны пакеты из .venv ({', '.join(sorted(needs))})")
    assert not broken, (
        "команда из документации упадёт ModuleNotFoundError у того, кто следовал "
        "этой же документации:\n  " + "\n  ".join(broken))


def test_doctor_stays_runnable_by_any_python():
    """Диагностика обязана работать до установки зависимостей — иначе она
    бесполезна ровно в тот момент, когда нужна."""
    assert not _module_level_imports(REPO / "scripts" / "doctor.py"), \
        "doctor.py потянул внешний пакет на верхнем уровне — он больше не запустится " \
        "системным питоном, а именно этим он и ценен"


def _scripts_in_user_docs() -> set[str]:
    return {script for _, _, _, script, _ in _documented_commands()}


def test_user_facing_scripts_explain_themselves_instead_of_crashing():
    """Не тот питон — понятное объяснение, а не трейсбек.

    Проверка структурная: скрипт либо не требует внешних пакетов вовсе, либо
    ставит объяснение (`deps.explain_missing()`) до первого такого импорта.
    """
    silent = []
    for rel in sorted(_scripts_in_user_docs()):
        path = REPO / rel
        text = path.read_text(encoding="utf-8")
        if not _module_level_imports(path):
            continue
        if "deps.explain_missing()" not in text:
            silent.append(rel)
    assert not silent, (
        "запуск не тем питоном даст трейсбек вместо рецепта: " + ", ".join(silent))
