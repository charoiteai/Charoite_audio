"""Коды возврата конвейера — одно место, без копий и без импортов (№173)."""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import exit_codes  # noqa: E402

#: Кто какие коды читает. Раньше список имён был общим на всех читателей, и код,
#: который читает ОДИН потребитель, вписать было некуда: `EXIT_ROOT_UNNAMED`
#: (демон) и `EXIT_NOTHING_TO_CHECK` (мутатор) остались бы вне гейта, хотя их
#: читают через границу процесса — launchd, приёмка, CI (круг 2 по №339, DS M5).
READERS = {
    "src/graph_updater.py": {"EXIT_NO_SPEECH", "EXIT_NO_GRAPH"},
    "src/rebuild_transcript.py": {"EXIT_NO_SPEECH", "EXIT_NO_GRAPH"},
    "scripts/import_meeting.py": {"EXIT_NO_SPEECH", "EXIT_NO_GRAPH"},
    "src/daemon.py": {"EXIT_ROOT_UNNAMED"},
    "scripts/mutate_check.py": {"EXIT_NOTHING_TO_CHECK", "EXIT_PARTIAL"},
}
#: Значения — снимок: их читают процессы вне этого репозитория (launchd, CI,
#: приёмка), и молчаливая перенумерация ломает их без единого красного теста.
VALUES = {"EXIT_NO_SPEECH": 3, "EXIT_NO_GRAPH": 4, "EXIT_ROOT_UNNAMED": 5, "EXIT_NOTHING_TO_CHECK": 6,
          "EXIT_PARTIAL": 7}
#: Имена — не рукописный список, а всё, что объявил модуль: пятая константа
#: без снимка значения иначе прошла бы мимо гейта (круг 3 по №339, DS I3).
NAMES = {n for n in dir(exit_codes) if n.startswith("EXIT_")}


def test_exit_codes_module_has_no_imports():
    """Модуль тянут из короткоживущих скриптов — он не должен оплачивать
    ничьи зависимости (ни requests, ни llm_health, ни даже pathlib)."""
    tree = ast.parse((ROOT / "src" / "exit_codes.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert {n: getattr(exit_codes, n) for n in NAMES} == VALUES, (
        "новый код или изменённое значение: их читают процессы вне репозитория — внести в VALUES осознанно")
    assert len(set(VALUES.values())) == len(VALUES), "два кода с одним значением снаружи неразличимы"
    # классификатор знает КАЖДЫЙ код и не путает их между собой
    assert {exit_codes.outcome(v) for v in VALUES.values()} <= {"nothing", "partial", "fail"}
    assert exit_codes.outcome(0) == "ok" and exit_codes.outcome(1) == "fail"


def _imports_from_exit_codes(tree: ast.AST) -> set[str]:
    return {a.name for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module == "exit_codes" for a in n.names}


def _assigned(tree: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                for leaf in ast.walk(t):
                    if isinstance(leaf, ast.Name):
                        out.add(leaf.id)
    return out


def test_every_reader_imports_the_codes_and_keeps_no_copy():
    """graph_updater, rebuild_transcript и import_meeting берут коды из
    exit_codes и не присваивают EXIT_* сами: копия литералов молча
    расходилась бы при перенумерации. Проверка по AST — без исполнения
    верхних уровней модулей (они тянут requests/llm_health/stt; GLM r1)."""
    for rel, свои in READERS.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        assert свои <= _imports_from_exit_codes(tree), f"{rel}: коды не взяты из exit_codes"
        assert not (NAMES & _assigned(tree)), f"{rel}: своя копия EXIT_*"
    src = (ROOT / "scripts" / "import_meeting.py").read_text(encoding="utf-8")
    assert "no_speech, no_graph" not in src, "в импорте снова алиасы кодов"


def test_the_readers_list_is_the_repository_itself():
    """Список читателей выведен из репозитория, а не написан от руки: новый файл
    с `from exit_codes import …` обязан появиться в `READERS` — иначе он вне
    гейта «без копий», и следующий код повторит побег (круг 3 по №339, DS I3).

    Сканируем наш python (`layout_map.PYTHON_AREAS`), чтобы список областей был
    один на проект, а не пятая копия литерала `src/`.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import layout_map as lm  # noqa: E402

    найдено = {}
    for область in lm.PYTHON_AREAS:
        for файл in sorted((ROOT / область).rglob("*.py")):
            rel = файл.relative_to(ROOT).as_posix()
            if rel == "src/exit_codes.py":
                continue
            имена = _imports_from_exit_codes(ast.parse(файл.read_text(encoding="utf-8")))
            if имена:
                найдено[rel] = имена
    assert set(найдено) == set(READERS), (
        f"читатели кодов разошлись со списком: появились {sorted(set(найдено) - set(READERS))}, "
        f"исчезли {sorted(set(READERS) - set(найдено))}")
    for rel, имена in найдено.items():
        assert имена == READERS[rel], f"{rel}: импортирует {sorted(имена)}, объявлено {sorted(READERS[rel])}"
