"""Коды возврата конвейера — одно место, без копий и без импортов (№173)."""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import exit_codes  # noqa: E402

READERS = ("src/graph_updater.py", "src/rebuild_transcript.py", "scripts/import_meeting.py")
NAMES = {"EXIT_NO_SPEECH", "EXIT_NO_GRAPH"}


def test_exit_codes_module_has_no_imports():
    """Модуль тянут из короткоживущих скриптов — он не должен оплачивать
    ничьи зависимости (ни requests, ни llm_health, ни даже pathlib)."""
    tree = ast.parse((ROOT / "src" / "exit_codes.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert exit_codes.EXIT_NO_SPEECH == 3 and exit_codes.EXIT_NO_GRAPH == 4


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
    for rel in READERS:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        assert NAMES <= _imports_from_exit_codes(tree), f"{rel}: не все коды взяты из exit_codes"
        assert not (NAMES & _assigned(tree)), f"{rel}: своя копия EXIT_*"
    src = (ROOT / "scripts" / "import_meeting.py").read_text(encoding="utf-8")
    assert "no_speech, no_graph" not in src, "в импорте снова алиасы кодов"
