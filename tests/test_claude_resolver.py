"""Единый резолвер Claude CLI (партия D-П4 карты оздоровления).

Пять точек выхода держали свою копию `shutil.which("claude") or
"/opt/homebrew/bin/claude"`. Копии сходятся в cloud.claude_bin():
поведенческие тесты фиксируют PATH-резолв и fallback, структурный —
что литерал не расползается обратно.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import cloud  # noqa: E402


def test_resolves_from_path(monkeypatch):
    asked = []

    def which(name):
        asked.append(name)
        return "/x/bin/claude"

    monkeypatch.setattr(cloud.shutil, "which", which)
    assert cloud.claude_bin() == "/x/bin/claude"
    assert asked == ["claude"]


def test_falls_back_to_homebrew_when_missing(monkeypatch):
    # Отсутствие в PATH — задокументированный Homebrew-путь; существование
    # не проверяется нарочно: точка выхода получает честный ENOENT.
    monkeypatch.setattr(cloud.shutil, "which", lambda name: None)
    assert cloud.claude_bin() == "/opt/homebrew/bin/claude"


def test_no_stray_copies_of_the_resolver():
    # Дедуп держится: литерал живёт только в cloud.py.
    offenders = []
    for base in (ROOT / "src", ROOT / "scripts"):
        for f in base.rglob("*.py"):
            if f.name == "cloud.py":
                continue
            if 'shutil.which("claude")' in f.read_text(encoding="utf-8"):
                offenders.append(str(f.relative_to(ROOT)))
    assert offenders == []


# «Каждый известный выход зовёт резолвер» проверяет AST-страж в
# test_cloud_call_sites.py (позитивная сторона test_no_other_place_starts_claude,
# идея из #407): он привязан к реестру NETWORK_EXITS и не дублирует список
# точек здесь подстрочным счётом.
