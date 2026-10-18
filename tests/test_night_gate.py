"""Гейт слотов ночи: какая ночь уже сделана и какой слот можно отложить.

Решение — таблицей: ровно эти строки спорились пять входных кругов №630.
"""
import datetime as dt
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import night_gate  # noqa: E402

AC = (False, 100)


def _at(day: int, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, day, hh, mm)


def _st(state: str, finished: str) -> dict:
    return {"state": state, "finished": finished}


@pytest.mark.parametrize("now, status, power, want", [
    # ok этой ночи: вечером после 19:00 сегодня, утром после 19:00 вчера
    (_at(18, 20), _st("ok", "2026-10-18 19:40:00"), AC, "skip"),
    (_at(18, 19), _st("ok", "2026-10-18 19:00:00"), AC, "skip"),
    # утренний слот после вечерней ночи — только бриф дня
    (_at(19, 4, 15), _st("ok", "2026-10-18 19:40:00"), AC, "brief"),
    (_at(19, 11, 59), _st("ok", "2026-10-18 19:40:00"), AC, "brief"),
    # слот, склеенный launchd после сна, днём: ночь ещё вчерашняя — бриф дня
    (_at(19, 12, 0), _st("ok", "2026-10-18 19:40:00"), AC, "brief"),
    (_at(19, 12, 30), _st("ok", "2026-10-18 19:40:00"), AC, "brief"),
    (_at(19, 18, 59), _st("ok", "2026-10-19 05:10:00"), AC, "brief"),
    # с 19:00 — новая ночь: утренний ok прошлой — повтор, вечерний этой — skip
    (_at(19, 19), _st("ok", "2026-10-19 05:10:00"), AC, "go"),
    # граница <=: закончилась ровно сейчас — сделана
    (_at(18, 20), _st("ok", "2026-10-18 20:00:00"), AC, "skip"),
    # finished в будущем (часы отмотали назад) — статусу не верим
    (_at(18, 20), _st("ok", "2026-10-18 21:30:00"), AC, "go"),
    # ok прошлой ночи — эта ночь ещё не сделана
    (_at(18, 19), _st("ok", "2026-10-18 05:10:00"), AC, "go"),
    (_at(19, 4, 15), _st("ok", "2026-10-18 05:10:00"), AC, "go"),
    # slept, failed, interrupted, running — ночь не сделана, повтор
    (_at(18, 20), _st("slept", "2026-10-18 19:50:00"), AC, "go"),
    (_at(18, 20), _st("failed", "2026-10-18 19:50:00"), AC, "go"),
    (_at(18, 20), _st("interrupted", "2026-10-18 19:50:00"), AC, "go"),
    (_at(18, 20), _st("running", "2026-10-18 19:50:00"), AC, "go"),
    # батарея: вечерний слот откладывается, утренний — последний — нет
    (_at(18, 20), None, (True, 20), "defer battery 20%"),
    (_at(19, 4, 15), None, (True, 20), "go"),
    (_at(18, 20), None, (True, 30), "go"),
    (_at(18, 20), None, (False, 20), "go"),
    # сбои чтения — go
    (_at(18, 20), None, None, "go"),
    (_at(18, 20), _st("ok", "мусор"), AC, "go"),
    (_at(18, 20), {"state": "ok"}, AC, "go"),
])
def test_decide(now, status, power, want):
    assert night_gate.decide(now, status, power) == want


@pytest.mark.parametrize("text, want", [
    ("Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t23%; discharging;\n", (True, 23)),
    ("Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged;\n", (False, 100)),
    ("Now drawing from 'AC Power'\n", (False, 100)),          # мак без батареи
    ("Now drawing from 'Battery Power'\n", None),
    ("", None),
])
def test_parse_power(text, want):
    assert night_gate.parse_power(text) == want


def test_unreadable_status_is_none(tmp_path):
    assert night_gate.read_status(tmp_path) is None
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "nightly.json").write_text("{битый")
    assert night_gate.read_status(tmp_path) is None
    (tmp_path / "logs" / "nightly.json").write_text("[1]")
    assert night_gate.read_status(tmp_path) is None


def test_cli_prints_one_decision(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(night_gate, "read_power", lambda: AC)
    assert night_gate.main(["decide", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "go\n"
