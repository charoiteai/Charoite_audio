"""Дедуп тезисов демона: отказ NLI-судьи — «не дубль», а не потерянный хвост тика.

`is_dup_thesis` — замыкание внутри main(), напрямую его не вызвать. Берём его
исходник из daemon.py по AST и исполняем с подменённым `nli`: проверяется
поведение той самой функции, а не слово в тексте.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys
import types
from collections import deque

import pytest

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
        "thesis_dedup_off": [],
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


def test_any_judge_error_keeps_the_thesis_and_says_why(monkeypatch, capsys):
    # ORT падает не только SeamTransportError: RuntimeError из run/encode ронял хвост тика
    def boom(*a, **k):
        raise RuntimeError("ORT: invalid input")

    fake = types.SimpleNamespace(is_available=lambda: True, is_duplicate=boom)
    is_dup, recent = _is_dup_thesis(fake, monkeypatch)
    recent.append("бюджет проекта согласован на два миллиона")
    assert is_dup("бюджет проекта согласован на два миллиона рублей") is False
    assert "судья отказал (RuntimeError: ORT: invalid input) — тезис оставлен" in capsys.readouterr().out


@pytest.fixture
def real_nli(tmp_path, monkeypatch):
    import nli
    (tmp_path / "model.onnx").touch()
    (tmp_path / "tokenizer.json").write_text("{}")
    monkeypatch.setattr(nli, "_dir", lambda: tmp_path)
    monkeypatch.setattr(nli, "_failed", False)
    monkeypatch.setattr(nli, "_cfg_cache", None)
    return nli, tmp_path


@pytest.mark.parametrize("config, reason", [
    (None, "нет config.json"),
    ({"id2label": {"0": "LABEL_0", "1": "LABEL_1", "2": "LABEL_2"}}, "LABEL_0"),
])
def test_switched_off_dedup_names_its_reason_once(real_nli, monkeypatch, capsys, config, reason):
    nli, d = real_nli
    if config is not None:
        (d / "config.json").write_text(json.dumps(config))
    is_dup, recent = _is_dup_thesis(nli, monkeypatch)
    recent.append("бюджет проекта согласован на два миллиона")
    for _ in range(3):
        assert is_dup("бюджет проекта согласован на два миллиона рублей") is False
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "тезис-дедуп выключен" in ln]
    assert len(lines) == 1 and reason in lines[0]


def test_duplicate_is_still_dropped(monkeypatch):
    fake = types.SimpleNamespace(is_available=lambda: True, is_duplicate=lambda *a, **k: True)
    is_dup, recent = _is_dup_thesis(fake, monkeypatch)
    recent.append("бюджет проекта согласован на два миллиона")
    assert is_dup("бюджет проекта согласован на два миллиона рублей") is True
