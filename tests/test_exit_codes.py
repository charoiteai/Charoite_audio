"""Коды возврата конвейера — одно место, без копий и без импортов (№173)."""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import exit_codes  # noqa: E402


def test_exit_codes_module_has_no_imports():
    """Модуль тянут из короткоживущих скриптов — он не должен оплачивать
    ничьи зависимости (ни requests, ни llm_health, ни даже pathlib)."""
    tree = ast.parse((ROOT / "src" / "exit_codes.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert exit_codes.EXIT_NO_SPEECH == 3 and exit_codes.EXIT_NO_GRAPH == 4


def test_every_reader_takes_the_codes_from_exit_codes():
    """graph_updater, rebuild_transcript и import_meeting читают одни константы:
    копия литералов в импорте молча расходилась бы при перенумерации."""
    import graph_updater
    import rebuild_transcript
    import import_meeting

    assert graph_updater.EXIT_NO_SPEECH is exit_codes.EXIT_NO_SPEECH
    assert rebuild_transcript.EXIT_NO_GRAPH is exit_codes.EXIT_NO_GRAPH
    assert import_meeting.EXIT_NO_SPEECH is exit_codes.EXIT_NO_SPEECH
    src = (ROOT / "scripts" / "import_meeting.py").read_text(encoding="utf-8")
    assert "no_speech, no_graph = 3, 4" not in src, "в импорте снова копия литералов"
    assert "from exit_codes import" in (ROOT / "src" / "graph_updater.py").read_text(encoding="utf-8")
