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
    monkeypatch.setattr(cloud.shutil, "which", lambda name: "/x/bin/claude")
    assert cloud.claude_bin() == "/x/bin/claude"


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


def test_call_sites_use_the_resolver():
    # Счёт вхождений, не факт присутствия: в daemon точек ДВЕ, и регрессия
    # ровно одной оставила бы подстроку в файле (круг-1 по #406, DS).
    users = {
        "src/daemon.py": 2,
        "scripts/cloud_review.py": 1,
        "scripts/nightly_claude_cores.py": 1,
        "scripts/nightly_dossier_review.py": 1,
    }
    short = {f: n for f, n in users.items()
             if (ROOT / f).read_text(encoding="utf-8").count("cloud.claude_bin()") < n}
    assert short == {}
