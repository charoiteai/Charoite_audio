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


def test_foreign_alert_is_not_shown(tmp_path, monkeypatch):
    graph = tmp_path / "Работа"
    graph = _setup(tmp_path, monkeypatch, _alert(tmp_path / "Другой", profile="live"))
    assert "бенч памяти" not in morning_brief.build_brief(graph)


def test_stale_alert_stays_with_the_time_of_the_last_measurement(tmp_path, monkeypatch):
    """Взведённая тревога не стареет молча: ночи, оборванные до сравнения, `ts` не
    двигают, а просадку никто не опроверг (Opus I1)."""
    graph = tmp_path / "Работа"
    alert = _alert(graph, hours_ago=48)
    alert["answer"].update(mode="stats", state="alert", run="r1d")
    made = dt.datetime.fromisoformat(alert["answer"]["ts"])
    graph = _setup(tmp_path, monkeypatch, alert)
    line = next(ln for ln in morning_brief.build_brief(graph).splitlines() if "бенч памяти" in ln)
    assert line.startswith("- ⚠️ бенч памяти (answer/stats): было 30, стало 27")
    assert line.endswith(f"; последний замер {made:%d.%m %H:%M} — 48 ч назад; итог r1d")


def test_fresh_alert_has_no_age_tail(tmp_path, monkeypatch):
    graph = tmp_path / "Работа"
    alert = _alert(graph, hours_ago=2)
    alert["answer"].update(state="alert", run="r1d")
    graph = _setup(tmp_path, monkeypatch, alert)
    line = next(ln for ln in morning_brief.build_brief(graph).splitlines() if "бенч памяти" in ln)
    assert "последний замер" not in line and line.endswith("граф изменился: нет; итог r1d")


def test_alert_raised_on_uncommitted_code_says_so(tmp_path, monkeypatch):
    graph = tmp_path / "Работа"
    alert = _alert(graph)
    alert["answer"].update(state="alert", dirty=True)
    graph = _setup(tmp_path, monkeypatch, alert)
    line = next(ln for ln in morning_brief.build_brief(graph).splitlines() if "бенч памяти" in ln)
    assert line.endswith("граф изменился: нет; прогон на незакоммиченном коде")


def test_stale_unarmed_watch_is_not_shown(tmp_path, monkeypatch):
    graph = tmp_path / "Работа"
    entry = {"ts": (dt.datetime.now() - dt.timedelta(hours=48)).isoformat(timespec="seconds"),
             "profile": "answer", "mode": "stats", "graph_dir": str(graph), "state": "unmeasured",
             "why": "база не принята"}
    graph = _setup(tmp_path, monkeypatch, {"k": entry})
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


def test_unarmed_watch_is_a_line_not_silence(tmp_path, monkeypatch):
    """База не принята — бриф говорит, что сторож не взведён (DS I3 r1)."""
    graph = tmp_path / "Работа"
    entry = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "profile": "answer", "mode": "stats",
             "graph_dir": str(graph), "state": "unmeasured",
             "why": "база не принята (--accept --run 0a1b2c3d4e5f --profile answer --stats)"}
    graph = _setup(tmp_path, monkeypatch, {"k": entry})
    line = next(ln for ln in morning_brief.build_brief(graph).splitlines() if "бенч памяти" in ln)
    assert line == ("- ⚠️ бенч памяти (answer/stats): сторож не взведён — "
                    "база не принята (--accept --run 0a1b2c3d4e5f --profile answer --stats)")


def test_alert_kept_by_an_uncomparable_run_says_so(tmp_path, monkeypatch):
    graph = tmp_path / "Работа"
    alert = _alert(graph)
    alert["answer"].update(mode="stats", state="alert", head_changed=None,
                           unmeasured="сравнимо 0 из 37 вопросов (sem_used ≠ базы)")
    graph = _setup(tmp_path, monkeypatch, alert)
    line = next(ln for ln in morning_brief.build_brief(graph).splitlines() if "бенч памяти" in ln)
    assert line.startswith("- ⚠️ бенч памяти (answer/stats): было 30, стало 27")
    assert "HEAD изменился: неизвестно" in line
    assert line.endswith("; последний прогон не сравним: сравнимо 0 из 37 вопросов (sem_used ≠ базы)")
