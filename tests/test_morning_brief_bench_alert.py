"""Утренний бриф показывает тревогу ночного бенча памяти (№629 ч. 2, P6) строкой
«⚠️» рядом с предупреждениями доктора — только свежую и только своего графа;
битый файл тревоги бриф не роняет."""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import morning_brief  # noqa: E402


def _setup(tmp_path, monkeypatch, alert):
    monkeypatch.setattr(morning_brief, "resolve_root", lambda _f: tmp_path / "root")
    graph = tmp_path / "Работа"
    d = graph / "Встречи-архив" / "2026-08-10 10-00 — Планёрка"
    d.mkdir(parents=True)
    (d / "Саммари.md").write_text("# Саммари\n", encoding="utf-8")
    logs = tmp_path / "root" / "logs"
    logs.mkdir(parents=True)
    if alert is not None:
        (logs / "memory_bench_alert.json").write_text(
            alert if isinstance(alert, str) else json.dumps(alert, ensure_ascii=False), encoding="utf-8")
    return graph


def _alert(graph, *, hours_ago=1, profile="answer"):
    return {profile: {"ts": (dt.datetime.now() - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds"),
                      "profile": profile, "graph_dir": str(graph), "was": 30, "now": 27,
                      "regressed": [{"n": 4, "cat": "latest", "why": "срезано бюджетом"},
                                    {"n": 9, "cat": "fact", "why": "не выдано"}],
                      "head_changed": True, "graph_changed": False}}


def test_fresh_alert_of_this_graph_is_in_the_health_section(tmp_path, monkeypatch):
    graph = tmp_path / "Работа"
    graph = _setup(tmp_path, monkeypatch, _alert(graph))
    text = morning_brief.build_brief(graph)
    assert "## Здоровье графа" in text
    line = next(ln for ln in text.splitlines() if "бенч памяти" in ln)
    assert line.startswith("- ⚠️ бенч памяти (answer): было 30, стало 27")
    assert "№4 latest (срезано бюджетом)" in line and "№9 fact" in line
    assert "HEAD изменился: да, граф изменился: нет" in line


def test_stale_or_foreign_alert_is_not_shown(tmp_path, monkeypatch):
    graph = tmp_path / "Работа"
    alert = {**_alert(graph, hours_ago=48), **_alert(tmp_path / "Другой", profile="live")}
    graph = _setup(tmp_path, monkeypatch, alert)
    assert "бенч памяти" not in morning_brief.build_brief(graph)


def test_broken_alert_file_does_not_break_the_brief(tmp_path, monkeypatch):
    for bad in ("{не json", "[1, 2]", json.dumps({"answer": {"ts": "вчера"}})):
        root = tmp_path / bad.__hash__().__str__()
        root.mkdir()
        graph = _setup(root, monkeypatch, bad)
        text = morning_brief.build_brief(graph)
        assert text is not None and "бенч памяти" not in text


def test_no_alert_file_no_line(tmp_path, monkeypatch):
    graph = _setup(tmp_path, monkeypatch, None)
    assert "бенч памяти" not in morning_brief.build_brief(graph)
