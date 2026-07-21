"""MCP-сервер суфлёра: даёт Claude Code живой доступ к встрече.

Инструменты читают файлы transcripts/ (пишутся атомарно) и Ollama —
работают независимо от того, запущен ли демон из UI.
Запуск (регистрируется через `claude mcp add`):
  ~/Project/sufler/.venv/bin/python ~/Project/sufler/src/mcp_server.py
"""
from __future__ import annotations

import pathlib
import subprocess

import requests
from mcp.server.fastmcp import FastMCP

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg_text(root):
    """config.yaml, а без него — config.example.yaml (свежий клон)."""
    p = root / "config" / "config.yaml"
    if not p.exists():
        p = root / "config" / "config.example.yaml"
    return p.read_text(encoding="utf-8")

TRANSCRIPTS = ROOT / "transcripts"


def _cfg() -> dict:
    import yaml
    try:
        return yaml.safe_load(_cfg_text(ROOT))
    except Exception:
        return {}


_CFG = _cfg()
_LLM = _CFG.get("llm", {})
OLLAMA = _LLM.get("base_url", "http://localhost:11434").rstrip("/")
MODEL = _LLM.get("model", "qwen3.6:35b-a3b")  # боевая модель из конфига, не хардкод

mcp = FastMCP("sufler")


# производные файлы, которые пишутся ПОЗЖЕ стенограммы и не должны считаться «последней»
_DERIVED = ("_minutes.md", "_hints.md", "_разбор.md", "_ревизия_claude.md")


def _latest(pattern: str = "*.md") -> pathlib.Path | None:
    files = [
        p for p in TRANSCRIPTS.glob(pattern)
        if not p.name.endswith(_DERIVED)
    ]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


@mcp.tool()
def sufler_status() -> str:
    """Статус суфлёра: идёт ли встреча, какой файл стенограммы, размер."""
    running = subprocess.run(["pgrep", "-f", "src/daemon.py"], capture_output=True).returncode == 0
    f = _latest()
    if not f:
        return f"Демон: {'работает' if running else 'остановлен'}. Стенограмм нет."
    return (
        f"Демон: {'работает — встреча идёт' if running else 'остановлен'}.\n"
        f"Последняя стенограмма: {f.name} ({f.stat().st_size} байт, "
        f"обновлена {f.stat().st_mtime:.0f})"
    )


@mcp.tool()
def sufler_live_transcript(max_chars: int = 6000) -> str:
    """Живая стенограмма текущей/последней встречи (хвост, реплики по спикерам)."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    text = f.read_text(encoding="utf-8")
    body = text.split("---")[0]  # без раздела ко-мышления
    return f"[{f.name}]\n" + (body[-max_chars:] if len(body) > max_chars else body)


@mcp.tool()
def sufler_notes() -> str:
    """Ко-мышление встречи: 📌 контрольные точки, 💎 ценные факты, 💭 мысли модели."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    text = f.read_text(encoding="utf-8")
    if "## Ко-мышление" not in text:
        return "Заметок ко-мышления пока нет."
    return text.split("## Ко-мышление", 1)[1].strip()


@mcp.tool()
def sufler_make_minutes() -> str:
    """Сгенерировать минутки последней встречи локальной моделью и сохранить файлом."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    transcript = f.read_text(encoding="utf-8")
    r = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": "gemma4:26b",
            "stream": False,
            "messages": [
                {"role": "system", "content": "Ты секретарь встречи. Пишешь точные, сухие минутки по-русски, markdown."},
                {"role": "user", "content": f"Стенограмма:\n\n{transcript}\n\nСоставь минутки: дата, участники, темы, решения, поручения (Кто|Что|Срок), открытые вопросы, риски. Только факты."},
            ],
        },
        timeout=600,
    )
    out = r.json().get("message", {}).get("content", "")
    mpath = f.with_name(f.stem + "_minutes.md")
    mpath.write_text(out, encoding="utf-8")
    return f"Минутки сохранены: {mpath}\n\n{out[:2000]}"


@mcp.tool()
def sufler_hints() -> str:
    """Сохранённые подсказки последней встречи (авто и ручные)."""
    f = _latest()
    if not f:
        return "Стенограмм нет."
    h = f.with_name(f.stem + "_hints.md")
    return h.read_text(encoding="utf-8")[-4000:] if h.exists() else "Подсказок пока нет."


@mcp.tool()
def sufler_update_graph() -> str:
    """Обновить Obsidian-граф по последней встрече (сущности, связи, решения)."""
    import sys as _sys

    r = subprocess.run(
        [_sys.executable, str(ROOT / "src" / "graph_updater.py")],
        capture_output=True, text=True, timeout=600,
    )
    return (r.stdout + r.stderr).strip() or "готово"


if __name__ == "__main__":
    mcp.run()
