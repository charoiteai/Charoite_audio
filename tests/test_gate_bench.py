"""Замер гейта: цифры, по которым решат, включать ли его по-настоящему.

Если «снято» и «потеряно» считаются неверно, гейт включат по ошибочному
замеру — и потерянные вопросы на встрече никто не заметит.
"""

from __future__ import annotations

import dataclasses
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
    rows = [R("ask", "ask", 0.9), R("ask", "ask", 0.8), R("ask", "skip", 0.6),
            R("skip", "skip", 0.8), R("skip", "skip", 0.9), R("skip", "ask", 0.7),
            R("skip", "skip", 0.6), R("skip", "skip", 0.7)]
    s = gb.summary(rows)
    assert s["n"] == 8
    assert s["accuracy"] == pytest.approx(0.75)
    assert s["ask_kept"] == pytest.approx(2 / 3)
    assert s["skip_caught"] == pytest.approx(0.8)


def test_summary_p95_is_nearest_rank():
    rows = [R("ask", "ask", 0.9, float(ms)) for ms in range(1, 21)]
    s = gb.summary(rows)
    assert s["p95_ms"] == 19.0 and s["p50_ms"] == 10.5
    assert gb.summary([R("ask", "ask", 0.9, 7.0)])["p95_ms"] == 7.0


def test_summary_says_nothing_for_an_absent_class():
    s = gb.summary([R("ask", "ask", 0.9)])
    assert s["skip_caught"] is None and s["p50_ms"] is None


def test_sweep_cuts_only_confident_skips():
    rows = [
        R("skip", "skip", 0.95),   # снято при τ ≤ 0.95
        R("skip", "skip", 0.75),   # снято при τ ≤ 0.75
        R("ask", "skip", 0.9),     # потерян вопрос при τ ≤ 0.9
        R("ask", "ask", 0.99),     # решено самим, но не отсечено
        R("ask", "ask", 0.6),      # ниже любого порога — модели, как сегодня
    ]
    by_tau = {p["tau"]: p for p in gb.sweep(rows, (0.7, 0.9, 0.95))}
    assert by_tau[0.7]["saved"] == pytest.approx(1.0)
    assert by_tau[0.7]["lost"] == pytest.approx(1 / 3)
    assert by_tau[0.9]["saved"] == pytest.approx(0.5)
    assert by_tau[0.9]["lost"] == pytest.approx(1 / 3)    # ровно на пороге — отсечён
    assert by_tau[0.95]["saved"] == pytest.approx(0.5)
    assert by_tau[0.95]["lost"] == pytest.approx(0.0)
    # уверенный «ask» (0.99) — тот же вызов модели, что эскалация: в отсечённое не входит
    assert by_tau[0.95]["cut"] == pytest.approx(0.2)            # одна реплика из пяти
    assert by_tau[0.95]["cut_precision"] == pytest.approx(1.0)
    assert by_tau[0.7]["cut"] == pytest.approx(0.6)             # две пустых и один вопрос
    assert by_tau[0.7]["cut_precision"] == pytest.approx(2 / 3)


def test_ece_is_zero_when_confidence_tells_the_truth():
    rows = [R("ask", "ask", 0.5), R("skip", "ask", 0.5)]    # 50% уверен, прав в половине
    assert gb.ece(rows) == pytest.approx(0.0)


def test_ece_measures_overconfidence():
    rows = [R("skip", "ask", 0.9), R("ask", "ask", 0.9)]    # 90% уверен, прав в половине
    assert gb.ece(rows) == pytest.approx(0.4)


def test_ece_judges_each_confidence_bucket_separately():
    # общая корзина дала бы |0.5 - 0.75| = 0.25; по корзинам — 0.05/2 + 0.55/2
    rows = [R("ask", "ask", 0.95), R("skip", "ask", 0.55)]
    assert gb.ece(rows) == pytest.approx(0.3)


def test_ece_puts_full_confidence_into_the_top_bucket():
    # 1.0 делит корзину с 0.9: отдельная корзина дала бы 0.5 + 0.05
    rows = [R("skip", "ask", 1.0), R("ask", "ask", 0.9)]
    assert gb.ece(rows) == pytest.approx(0.45)


def test_ece_measures_underconfidence_per_bucket():
    rows = [R("ask", "ask", 0.75), R("skip", "skip", 0.75),  # прав всегда, а уверен на 75%
            R("ask", "ask", 1.0)]                            # 1.0 — последняя корзина, не мимо
    assert gb.ece(rows) == pytest.approx(2 / 3 * 0.25)


def test_shadow_rows_take_model_refusal_as_skip_and_drop_failures():
    line = lambda label, p, outcome, backend="nli": dg.shadow_line(  # noqa: E731
        dg.Verdict(label, p, {}, backend, 100.0), outcome)
    lines = [
        "hint-pulse: on=True",
        line("skip", 0.9, "refusal"),
        line("skip", 0.8, "answered"),
        line("ask", 0.7, "failed"),
        line("ask", 0.6, "answered", backend="head:question_gate"),
        dg.shadow_line(None, "answered", "busy"),
        dg.shadow_line(None, "refusal", "busy"),
        dg.shadow_line(None, "answered", "error:FileNotFoundError"),
    ]
    by_backend, missing = gb.shadow_rows(lines)
    # пропуски — по причине: «модель занята» и «голова не собралась» — разные беды
    assert missing == {"busy": 2, "error:FileNotFoundError": 1}
    assert by_backend["nli"] == [R("skip", "skip", 0.9, 100.0), R("ask", "skip", 0.8, 100.0)]
    assert by_backend["head:question_gate"] == [R("ask", "ask", 0.6, 100.0)]


def test_read_labeled_skips_rows_without_a_verdict(tmp_path):
    data = tmp_path / "gate.jsonl"
    data.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"text": "Когда релиз?", "label": "ask"},
        {"text": "Что?", "label": ""},
        {"text": "", "label": "skip"},
        {"text": "С какого бы?", "label": "skip"},
    ]) + "\n" + '{"text": "Сломано", "label": ask}\n["не объект"]\n', encoding="utf-8")
    rows, skipped = gb.read_labeled(data)
    assert [r["text"] for r in rows] == ["Когда релиз?", "С какого бы?"]
    # правленный руками файл: битая строка и не-объект — пропуск, а не трассировка (DS M5)
    assert skipped == 4


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


def test_harvest_skips_the_owners_lines_like_the_daemon(tmp_path):
    """⚡ не отвечает на реплики владельца — и выборка их не берёт: метка «Я» и
    имя владельца из `sufler.user_name` — тем же предикатом, что у демона
    (выходной круг 1 по #651, DS M6)."""
    text = TRANSCRIPT + "**Я** [10:16:00]:\nА бюджет утвердили?\n\n"
    (tmp_path / "2026-09-27_101500.md").write_text(text, encoding="utf-8")
    # имени нет — канал микрофона подписан «Я», это владелец
    assert "А бюджет утвердили?" not in [r["text"] for r in gb.harvest(tmp_path)]
    # имя задано — владелец подписан им, его вопрос в выборку не идёт
    rows = gb.harvest(tmp_path, owner="Мира")
    assert "А что с деплоем?" not in [r["text"] for r in rows]
    assert rows[:2] == gb.harvest(tmp_path)[:2]


def test_eval_structural_runs_end_to_end(tmp_path, capsys):
    data = tmp_path / "gate.jsonl"
    data.write_text(json.dumps({"text": "Что?", "label": "skip"}, ensure_ascii=False) + "\n"
                    + json.dumps({"text": "Когда релиз?", "label": "ask"}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    assert gb.main(["eval", str(data), "--backend", "structural"]) == 0
    out = capsys.readouterr().out
    # фильтр детерминирован: уверенность 1.0 и при 100% точности ECE ноль
    assert "structural" in out and "точность 100.0%" in out and "ECE 0.000" in out


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
    assert "точность 100.0%" in out and "ECE 0.075" in out
    assert "p50 200 мс" in out and "p95 300 мс" in out
    # уверенный «ask» не входит в отсечённое: отсечена одна реплика из двух, и она верна
    assert "  0.90 |    50.0% |         100.0% |        100.0% |              0.0%" in out


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
    # ни один бэкенд не отработал — замера нет, код 2, а не 0 (DS M4)
    assert gb.main(["eval", str(_labeled(tmp_path)), "--backend", "nli"]) == 2
    captured = capsys.readouterr()
    assert "nli: нет модели" in captured.err and "##" not in captured.out
    assert "мерить было нечем" in captured.err


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
                    "--head-dir", str(tmp_path / "нет")]) == 2
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
        dg.shadow_line(dg.Verdict("skip", 0.95, {}, "nli-zero-shot", 400.0), "refusal"),
        dg.shadow_line(dg.Verdict("ask", 0.7, {}, "nli-zero-shot", 380.0), "answered"),
        dg.shadow_line(None, "answered", "busy"),
    ]) + "\n", encoding="utf-8")
    assert gb.main(["shadow", str(log), "--thresholds", "0.9"]) == 0
    out = capsys.readouterr().out
    assert "вердиктов нет: 1 — busy 1" in out
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
    raw = out.read_text(encoding="utf-8")
    assert "Когда переносим витрину продаж?" in raw     # кириллица, а не \\u-экранирование
    rows = [json.loads(x) for x in raw.splitlines()]
    assert [r["text"] for r in rows] == ["Когда переносим витрину продаж?", "Что?"]
    assert "2 кандидатов" in capsys.readouterr().out


def test_cli_demands_a_command_and_an_output_file(tmp_path):
    with pytest.raises(SystemExit):
        gb.main([])
    with pytest.raises(SystemExit):
        gb.main(["harvest", str(tmp_path)])


def test_bench_row_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        R("ask", "ask", 0.9).label = "skip"
