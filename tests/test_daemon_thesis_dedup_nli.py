"""Дедуп тезисов демона: отказ NLI-судьи — «не дубль», а не потерянный хвост тика.

`is_dup_thesis` — замыкание внутри main(), напрямую его не вызвать. Берём его
исходник из daemon.py по AST и исполняем с подменённым `nli`: проверяется
поведение той самой функции, а не слово в тексте.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import types
from collections import deque

from charoite_graph.model_seam import SeamTransportError

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "daemon.py"


def _is_dup_thesis(fake_nli, monkeypatch):
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "is_dup_thesis")
    module = ast.Module(body=[fn], type_ignores=[])
    ns = {
        "recent_theses": deque(maxlen=16),
        "thesis_rules": types.SimpleNamespace(strip_mark=lambda s: s),
        "SeamTransportError": SeamTransportError,
    }
    monkeypatch.setitem(sys.modules, "nli", fake_nli)
    exec(compile(module, str(SRC), "exec"), ns)  # noqa: S102 — исходник нашего демона
    return ns["is_dup_thesis"], ns["recent_theses"]


def test_judge_refusal_is_not_a_duplicate_and_does_not_raise(monkeypatch):
    def refuse(*a, **k):
        raise SeamTransportError("NLI: выход не соответствует контракту трёх классов")

    fake = types.SimpleNamespace(is_available=lambda: True, is_duplicate=refuse)
    is_dup, recent = _is_dup_thesis(fake, monkeypatch)
    recent.append("бюджет проекта согласован на два миллиона")
    assert is_dup("бюджет проекта согласован на два миллиона рублей") is False
    assert is_dup("бюджет проекта согласован на два миллиона рублей") is False


def test_duplicate_is_still_dropped(monkeypatch):
    fake = types.SimpleNamespace(is_available=lambda: True, is_duplicate=lambda *a, **k: True)
    is_dup, recent = _is_dup_thesis(fake, monkeypatch)
    recent.append("бюджет проекта согласован на два миллиона")
    assert is_dup("бюджет проекта согласован на два миллиона рублей") is True
