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


def test_report_prints_shares_latency_and_the_threshold_table(capsys):
    rows = [R("ask", "ask", 0.9, 100.0), R("skip", "skip", 0.95, 300.0)]
    gb.print_report("nli-zero-shot", rows, (0.9,))
    out = capsys.readouterr().out
    assert "## nli-zero-shot: 2 реплик" in out
    assert "точность 100.0%" in out and "ECE" in out
    assert "p50 200 мс" in out and "p95 300 мс" in out
    assert "  0.90 |     100.0% |             100.0% |        100.0% |              0.0%" in out


def test_structural_report_has_no_threshold_table(capsys):
    gb.print_report("structural (question_filter)", [R("ask", "ask", 1.0)], (0.9,))
    out = capsys.readouterr().out
    assert "τ" not in out and "p50 — мс" in out


def test_thresholds_parse_and_share_the_gate_check():
    assert gb._thresholds("0.7, 0.9,") == (0.7, 0.9)


class _Judge:
    refused = ""

    @staticmethod
    def entail(premise, hypothesis):
        # «вопрос» в гипотезе ask; реплика с «?» — вопрос
        return 0.9 if ("?" in premise) == ("вопрос" in hypothesis) else 0.1


def _labeled(tmp_path):
    data = tmp_path / "gate.jsonl"
    data.write_text(json.dumps({"text": "Что с деплоем?", "label": "ask"}, ensure_ascii=False) + "\n"
                    + json.dumps({"text": "ну вот так", "label": "skip"}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return data


def test_eval_nli_backend_judges_every_row(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(gb.nli, "judge", lambda: _Judge())
    assert gb.main(["eval", str(_labeled(tmp_path)), "--backend", "nli"]) == 0
    out = capsys.readouterr().out
    assert "## nli-zero-shot: 2 реплик" in out and "точность 100.0%" in out


def test_eval_skips_nli_backend_that_refused(tmp_path, capsys, monkeypatch):
    class Refused(_Judge):
        refused = "нет модели"

    monkeypatch.setattr(gb.nli, "judge", lambda: Refused())
    assert gb.main(["eval", str(_labeled(tmp_path)), "--backend", "nli"]) == 0
    captured = capsys.readouterr()
    assert "nli: нет модели" in captured.err and "##" not in captured.out


def test_eval_head_backend_loads_the_named_directory(tmp_path, capsys, monkeypatch):
    head = tmp_path / "question_gate"
    head.mkdir()
    for f in dg.HEAD_FILES:
        (head / f).write_text("{}", encoding="utf-8")
    loaded = []
    monkeypatch.setattr(gb.dg, "load_head", lambda d: loaded.append(d) or (
        lambda t: {"ask": 0.8, "skip": 0.2} if "?" in t else {"ask": 0.1, "skip": 0.9}))
    assert gb.main(["eval", str(_labeled(tmp_path)), "--backend", "head", "--head-dir", str(head)]) == 0
    assert loaded == [head]
    assert "## head:question_gate: 2 реплик" in capsys.readouterr().out


def test_eval_skips_head_backend_without_files(tmp_path, capsys):
    assert gb.main(["eval", str(_labeled(tmp_path)), "--backend", "head",
                    "--head-dir", str(tmp_path / "нет")]) == 0
    captured = capsys.readouterr()
    assert "head: в" in captured.err and "##" not in captured.out


def test_eval_without_labeled_rows_says_so(tmp_path, capsys):
    data = tmp_path / "gate.jsonl"
    data.write_text(json.dumps({"text": "Что?", "label": ""}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert gb.main(["eval", str(data)]) == 2
    assert "без метки ask/skip: 1" in capsys.readouterr().err


def test_shadow_command_reports_each_backend_and_missing_verdicts(tmp_path, capsys):
    log = tmp_path / "daemon.err.log"
    log.write_text("\n".join([
        "hint-pulse: on=True",
        dg.shadow_line(dg.Verdict("skip", 0.95, {}, "nli-zero-shot", 400.0), "refusal", "a"),
        dg.shadow_line(dg.Verdict("ask", 0.7, {}, "nli-zero-shot", 380.0), "answered", "b"),
        dg.shadow_line(None, "answered", "c", "late"),
    ]) + "\n", encoding="utf-8")
    assert gb.main(["shadow", str(log), "--thresholds", "0.9"]) == 0
    out = capsys.readouterr().out
    assert "вердиктов нет (решатель опоздал или упал): 1" in out
    assert "## nli-zero-shot (тень: отказ модели = skip): 2 реплик" in out


def test_shadow_command_without_records_asks_to_enable_the_shadow(tmp_path, capsys):
    log = tmp_path / "daemon.err.log"
    log.write_text("hint-pulse: on=True\n", encoding="utf-8")
    assert gb.main(["shadow", str(log)]) == 2
    assert "decision_gate_shadow" in capsys.readouterr().err


def test_harvest_command_writes_jsonl_ready_for_labeling(tmp_path, capsys):
    (tmp_path / "2026-09-27_101500.md").write_text(TRANSCRIPT, encoding="utf-8")
    out = tmp_path / "gate.jsonl"
    assert gb.main(["harvest", str(tmp_path), "--out", str(out), "--limit", "2"]) == 0
    rows = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [r["text"] for r in rows] == ["Когда переносим витрину продаж?", "Что?"]
    assert "2 кандидатов" in capsys.readouterr().out
