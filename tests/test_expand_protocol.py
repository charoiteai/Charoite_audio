"""Контракт кнопки ⏮ между macOS-приложением и python-демоном.

Два быстрых клика раньше запускали две скрытые LLM-генерации. Протокол
должен иметь явный жизненный цикл, а обе стороны — свою защиту от дубля:
клиент закрывает обычный UI-сценарий, демон защищает stdin как границу.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAEMON = ROOT / "src" / "daemon.py"
SERVICE = ROOT / "app" / "Sources" / "CharoiteApp" / "Services" / "SuflerService.swift"


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              and node.name == name)
    return ast.get_source_segment(source, fn) or ""


def test_daemon_serializes_expand_and_always_finishes_protocol():
    body = _function_source(DAEMON, "expand_topic")
    assert "acquire(blocking=False)" in body
    assert '"type": "expand_started"' in body
    assert '"type": "expand_done"' in body
    assert "finally:" in body
    assert "expand_lock.release()" in body


def test_macos_expand_protocol_stays_removed():
    """Кнопка ⏮ и её протокол убраны из приложения (пакет владельца 24.08,
    PR #394): сервис не держит isExpanding и не потребляет expand-события.
    Демонная половина протокола жива для headless — тест выше."""
    # Комментарии срезаем: упоминание «expand_done намеренно игнорируется»
    # в комментарии не должно ронять тест (круг-3 по #394, Codex Minor).
    source = "\n".join(
        line.split("//", 1)[0] for line in SERVICE.read_text(encoding="utf-8").splitlines())
    assert "isExpanding" not in source
    assert "expand_started" not in source
    assert "expand_done" not in source
    assert "requestExpand" not in source
