"""MCP-сервер должен подниматься на той версии mcp, которую ставит pip.

pyproject разрешает `mcp>=1.0`, а в 2.0 класс переехал: `FastMCP` из
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


# ── Аудит 13.09, зона 4 ──────────────────────────────────────────────────

import re  # noqa: E402
import subprocess  # noqa: E402


def _transcripts(tmp_path, monkeypatch, name="2026-09-13_1200.md", text="# Встреча\nтело\n"):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    (tdir / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(mcp_server, "TRANSCRIPTS", tdir)
    return tdir


def test_latest_skips_a_file_removed_between_glob_and_stat(tmp_path, monkeypatch):
    """Ретеншн демона убирает файл между glob и stat — FileNotFoundError валил
    любой MCP-инструмент (GLM M3)."""
    tdir = _transcripts(tmp_path, monkeypatch)
    (tdir / "2026-09-13_1300.md").write_text("x", encoding="utf-8")
    real_stat = pathlib.Path.stat

    def stat(self, *a, **k):
        if self.name == "2026-09-13_1300.md":
            raise FileNotFoundError(2, "gone", str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", stat)
    assert mcp_server._latest().name == "2026-09-13_1200.md"


def test_live_transcript_keeps_dashes_inside_speech(tmp_path, monkeypatch):
    """Самодельный split("---") резал стенограмму на первом «---» в сказанном;
    граница заметок — transcript.notes_start (DS M4)."""
    head = mcp_server.transcript.NOTES_HEAD
    _transcripts(tmp_path, monkeypatch,
                 text="# Встреча\nсказали --- и продолжили\nещё реплика\n" + head + "заметка модели\n")
    out = mcp_server.sufler_live_transcript()
    assert "и продолжили" in out and "ещё реплика" in out
    assert "заметка модели" not in out
    tail = mcp_server.sufler_live_transcript(max_chars=0)     # 0 давал всю стенограмму (GLM M4)
    assert tail.startswith("[") and len(tail.split("\n", 1)[1]) == 1


def test_status_pattern_matches_the_daemon_process_not_an_editor():
    """pgrep -f «src/daemon.py» совпадал с редактором, где открыт файл (GLM M4)."""
    pat = re.compile(mcp_server.DAEMON_PATTERN)
    assert pat.search("/opt/venv/bin/python3 /Users/x/charoite/src/daemon.py")
    assert pat.search("python src/daemon.py --flag")
    assert not pat.search("vim src/daemon.py")
    assert not pat.search("less /tmp/src/daemon.py.bak")
    assert pat.search("/x/.venv/bin/python3 -u /y/src/daemon.py")
    assert not pat.search("python -m pylint src/daemon.py"), "скрипт не первый аргумент — не демон (GLM M6)"


def test_make_minutes_fits_a_long_transcript_like_the_daemon(tmp_path, monkeypatch):
    """Инструмент собирал промпт с полной стенограммой при num_ctx 8192: Ollama
    молча обрезала начало, и усечённые минутки ложились поверх полных (GLM I1)."""
    tdir = _transcripts(tmp_path, monkeypatch, text="# Встреча\n" + "реплика\n" * 100)
    seen = {}

    class Fake:
        def fit(self, transcript):
            return "[сжато: сводки частей]"

        def complete(self, prompt, **kw):
            seen["prompt"] = prompt
            return "- **Кто** — что — срок"

    monkeypatch.setattr(mcp_server, "_client", lambda: Fake())
    out = mcp_server.sufler_make_minutes()
    assert "[сжато: сводки частей]" in seen["prompt"] and "реплика\nреплика" not in seen["prompt"]
    assert "Минутки сохранены" in out and (tdir / "2026-09-13_1200_minutes.md").exists()


def test_update_graph_timeout_is_a_message_not_a_crash(tmp_path, monkeypatch):
    def run(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=mcp_server.GRAPH_UPDATE_TIMEOUT)

    monkeypatch.setattr(mcp_server.subprocess, "run", run)
    out = mcp_server.sufler_update_graph()
    assert "20 мин" in out and "прерван" in out
