"""Замер гейта: цифры, по которым решат, включать ли его по-настоящему.

Если «снято» и «потеряно» считаются неверно, гейт включат по ошибочному
замеру — и потерянные вопросы на встрече никто не заметит.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gate_bench", ROOT / "scripts" / "gate_bench.py")
gb = importlib.util.module_from_spec(_spec)
# dataclass ищет свой модуль в sys.modules — без записи сбор падает
sys.modules["gate_bench"] = gb
_spec.loader.exec_module(gb)
dg = gb.dg


def R(gold, label, p, ms=0.0):
    return gb.Row(gold, label, p, ms)


def test_summary_counts_kept_questions_and_caught_junk():
    rows = [R("ask", "ask", 0.9), R("ask", "skip", 0.6), R("skip", "skip", 0.8), R("skip", "skip", 0.7)]
    s = gb.summary(rows)
    assert s["n"] == 4
    assert s["accuracy"] == pytest.approx(0.75)
    assert s["ask_kept"] == pytest.approx(0.5)
    assert s["skip_caught"] == pytest.approx(1.0)


def test_summary_says_nothing_for_an_absent_class():
    s = gb.summary([R("ask", "ask", 0.9)])
    assert s["skip_caught"] is None and s["p50_ms"] is None


def test_sweep_cuts_only_confident_skips():
    rows = [
        R("skip", "skip", 0.95),   # снято при τ ≤ 0.95
        R("skip", "skip", 0.75),   # снято при τ ≤ 0.75
        R("ask", "skip", 0.9),     # потерян вопрос при τ ≤ 0.9
        R("ask", "ask", 0.99),     # решено самим, но не отсечено
    ]
    by_tau = {p["tau"]: p for p in gb.sweep(rows, (0.7, 0.9, 0.95))}
    assert by_tau[0.7]["saved"] == pytest.approx(1.0)
    assert by_tau[0.7]["lost"] == pytest.approx(0.5)
    assert by_tau[0.9]["saved"] == pytest.approx(0.5)
    assert by_tau[0.9]["lost"] == pytest.approx(0.5)      # ровно на пороге — отсечён
    assert by_tau[0.95]["saved"] == pytest.approx(0.5)
    assert by_tau[0.95]["lost"] == pytest.approx(0.0)
    assert by_tau[0.95]["decided"] == pytest.approx(0.5)  # 0.95 и 0.99
    assert by_tau[0.95]["decided_acc"] == pytest.approx(1.0)


def test_ece_is_zero_when_confidence_tells_the_truth():
    rows = [R("ask", "ask", 0.5), R("skip", "ask", 0.5)]    # 50% уверен, прав в половине
    assert gb.ece(rows) == pytest.approx(0.0)


def test_ece_measures_overconfidence():
    rows = [R("skip", "ask", 0.9), R("ask", "ask", 0.9)]    # 90% уверен, прав в половине
    assert gb.ece(rows) == pytest.approx(0.4)


def test_shadow_rows_take_model_refusal_as_skip_and_drop_failures():
    line = lambda label, p, outcome, backend="nli": dg.shadow_line(  # noqa: E731
        dg.Verdict(label, p, {}, backend, 100.0), outcome, "q1")
    lines = [
        "hint-pulse: on=True",
        line("skip", 0.9, "refusal"),
        line("skip", 0.8, "answered"),
        line("ask", 0.7, "failed"),
        line("ask", 0.6, "answered", backend="head:question_gate"),
        dg.shadow_line(None, "answered", "q2", "late"),
    ]
    by_backend, missing = gb.shadow_rows(lines)
    assert missing == 1
    assert by_backend["nli"] == [R("skip", "skip", 0.9, 100.0), R("ask", "skip", 0.8, 100.0)]
    assert by_backend["head:question_gate"] == [R("ask", "ask", 0.6, 100.0)]


def test_read_labeled_skips_rows_without_a_verdict(tmp_path):
    data = tmp_path / "gate.jsonl"
    data.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"text": "Когда релиз?", "label": "ask"},
        {"text": "Что?", "label": ""},
        {"text": "", "label": "skip"},
        {"text": "С какого бы?", "label": "skip"},
    ]) + "\n", encoding="utf-8")
    rows, skipped = gb.read_labeled(data)
    assert [r["text"] for r in rows] == ["Когда релиз?", "С какого бы?"]
    assert skipped == 2


TRANSCRIPT = """# Встреча 27.09

**Собеседник 1** [10:15:02]:
Коллеги, привет. Когда переносим витрину продаж? Что? Когда переносим витрину продаж?

**Мира** [10:15:20]:
Переносим в субботу. А что с деплоем?

"""


def test_harvest_takes_question_candidates_from_main_transcripts_only(tmp_path):
    (tmp_path / "2026-09-27_101500.md").write_text(TRANSCRIPT, encoding="utf-8")
    (tmp_path / "2026-09-27_101500_hints.md").write_text(TRANSCRIPT, encoding="utf-8")
    rows = gb.harvest(tmp_path)
    assert [r["text"] for r in rows] == [
        "Когда переносим витрину продаж?", "Что?", "А что с деплоем?"]   # повтор снят
    assert {r["source"] for r in rows} == {"2026-09-27_101500"}
    assert [r["structural"] for r in rows] == ["ask", "skip", "ask"]
    assert rows[2]["speaker"] == "Мира" and rows[0]["label"] == ""


def test_eval_structural_runs_end_to_end(tmp_path, capsys):
    data = tmp_path / "gate.jsonl"
    data.write_text(json.dumps({"text": "Что?", "label": "skip"}, ensure_ascii=False) + "\n"
                    + json.dumps({"text": "Когда релиз?", "label": "ask"}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    assert gb.main(["eval", str(data), "--backend", "structural"]) == 0
    out = capsys.readouterr().out
    assert "structural" in out and "точность 100.0%" in out


def test_eval_refuses_unknown_backend_and_bad_threshold(tmp_path):
    data = tmp_path / "gate.jsonl"
    data.write_text(json.dumps({"text": "Что?", "label": "skip"}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    assert gb.main(["eval", str(data), "--backend", "gpt"]) == 2
    with pytest.raises(ValueError):
        gb.main(["eval", str(data), "--backend", "structural", "--thresholds", "0.5"])
