"""MCP-сервер должен подниматься на той версии mcp, которую ставит pip.

requirements разрешают `mcp>=1.0`, а в 2.0 класс переехал: `FastMCP` из
`mcp.server.fastmcp` стал `MCPServer` в `mcp.server`. У нового пользователя
установка проходила успешно, а сервер падал на импорте — то есть проверять
надо не «объявлена ли зависимость», а «поднимается ли сервер здесь и сейчас».

Импорт намеренно прямой, без importorskip: пропущенный тест выглядит как
зелёный и молчит ровно в том случае, ради которого написан.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mcp_server  # noqa: E402


def test_server_is_up():
    assert mcp_server.mcp is not None


def test_server_keeps_the_api_the_file_relies_on():
    server = mcp_server.mcp
    # Эти два метода одинаковы в обеих ветках API — на них и держится файл.
    assert callable(getattr(server, "tool", None))
    assert callable(getattr(server, "run", None))
