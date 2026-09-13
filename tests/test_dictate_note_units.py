"""Диктовка: ссылка на встречу — по ключу графа, момент заметки — из --moment,
имя заметки не затирает соседку (аудит 13.09, зона 5)."""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import dictate_note as dn  # noqa: E402


def test_last_meeting_today_links_the_graph_key_not_the_file_stem(tmp_path, monkeypatch):
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    today = f"{dt.datetime.now():%Y-%m-%d}"
    (tdir / f"{today}_1000_Итоги_квартала.md").write_text(
        f"# Встреча {today}_1000 — Итоги квартала\nтело\n", encoding="utf-8")
    monkeypatch.setenv("SUFLER_TRANSCRIPTS_DIR", str(tdir))
    stamp, topic = dn.last_meeting_today()
    assert stamp == f"{today}_1000", "ссылка [[Встречи/<стем с темой>]] висела в пустоте"
    assert topic == "Итоги квартала"


def test_moment_comes_from_the_flag_and_falls_back_to_now(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["dictate_note.py", "--text", "--moment", "2026-09-12 18:00"])
    assert dn._moment() == dt.datetime(2026, 9, 12, 18, 0)
    monkeypatch.setattr(sys, "argv", ["dictate_note.py", "--text", "--moment", "вчера"])
    assert abs((dn._moment() - dt.datetime.now()).total_seconds()) < 5
    monkeypatch.setattr(sys, "argv", ["dictate_note.py", "--text"])
    assert abs((dn._moment() - dt.datetime.now()).total_seconds()) < 5
