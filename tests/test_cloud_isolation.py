"""Каждый headless `claude -p` в репозитории обязан быть изолирован.

Стенограммы, ядра и досье — чужие слова: инъекция из них не должна
дотягиваться до инструментов облачного вызова («прочитай ~/.ssh/… и вставь
в отчёт»). Аудит 14.08 нашёл два голых вызова (ревизия нити, ревизия досье)
и один с разрешённым чтением файлов при полностью инлайновом материале
(ревизия ядер). Правило одно: вызов «только текст» несёт
cloud.text_only_args(), а вызов с осознанным доступом (облачный разбор
встречи, где право писать выдаёт privacy) — как минимум --setting-sources ""
и --strict-mcp-config: без них на headless действуют пользовательские
allowlist'ы из ~/.claude/settings.json.

Тест сканирует исходники: новый голый вызов провалит его, а не ревью.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import cloud  # noqa: E402

# [claude..., "-p"  — начало сборки argv любого headless-вызова;
# имя переменной бывает claude / claude_bin.
_CALL = re.compile(r"\[\s*claude\w*\s*,\s*\"-p\"")
# Окно после начала вызова, в котором обязаны стоять флаги изоляции:
# сборка команды с промптом длиннее короткой, но конечной.
_WINDOW = 2600


def _call_blocks() -> list[tuple[str, str]]:
    blocks = []
    for folder in ("src", "scripts"):
        for p in sorted((ROOT / folder).glob("*.py")):
            text = p.read_text(encoding="utf-8")
            for m in _CALL.finditer(text):
                blocks.append((f"{folder}/{p.name}", text[m.start():m.start() + _WINDOW]))
    return blocks


def test_text_only_args_contract():
    """Контракт «только текст»: полный запрет инструментов + отрез настроек."""
    args = cloud.text_only_args()
    joined = " ".join(args)
    assert "--disallowedTools" in args
    denied = args[args.index("--disallowedTools") + 1]
    for tool in ("Bash", "Read", "Write", "Edit", "Grep", "Glob",
                 "WebFetch", "WebSearch", "Task",
                 # обходные дороги к файлам и командам (ревью 15.08):
                 # скиллы исполняют шелл, BashOutput читает фоновые процессы
                 "Skill", "SlashCommand", "BashOutput", "KillShell"):
        assert tool in denied, f"{tool} выпал из запретительного списка"
    assert "--setting-sources" in args, "без него действуют пользовательские allowlist'ы"
    assert args[args.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in joined


def test_every_headless_claude_call_is_isolated():
    blocks = _call_blocks()
    assert len(blocks) >= 4, (
        "вызовы claude -p не нашлись — регэксп протух, тест ничего не стережёт"
    )
    for where, block in blocks:
        isolated = "text_only_args" in block or "--setting-sources" in block
        assert isolated, (
            f"{where}: headless claude -p без изоляции — добавь "
            "cloud.text_only_args() (только текст) или, для вызова с осознанным "
            "доступом к файлам, как минимум --setting-sources \"\" и "
            "--strict-mcp-config"
        )


def test_no_call_grants_tools_silently():
    """Разрешение инструментов — только в graph_updater (его выдаёт privacy)."""
    for where, block in _call_blocks():
        if "--allowedTools" in block:
            assert where == "src/graph_updater.py", (
                f"{where}: --allowedTools вне облачного разбора встречи — "
                "новому контуру инструменты выдаются только через "
                "осознанный контракт с privacy-ключом"
            )
