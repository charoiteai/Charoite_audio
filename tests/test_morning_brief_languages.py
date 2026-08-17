"""Утренний бриф читает Саммари на любом языке архива.

Саммари пишется на языке конфига (ru/en/zh), а бриф разбирал только русские
заголовки: при `sufler.language: en` — и для старых en/zh встреч после смены
языка — в _Сегодня.md не было ни сути, ни «Решили/Поручения/Открыто»
(аудит DeepSeek + GLM 17.08).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import morning_brief  # noqa: E402


def _meeting(graph: pathlib.Path, folder: str, summary: str) -> None:
    d = graph / "Встречи-архив" / folder
    d.mkdir(parents=True)
    (d / "Саммари.md").write_text(summary, encoding="utf-8")


def test_english_summary_is_parsed(tmp_path):
    graph = tmp_path / "Работа"
    _meeting(graph, "2026-08-10 10-00 — Sprint review",
             "# Summary\n\n**Bottom line:** the release slips a week\n\n"
             "## Decisions\n- ship on the 15th\n\n## Action items\n- Anna: update the plan\n\n"
             "## Open questions\n- budget for QA?\n")
    text = morning_brief.build_brief(graph)
    assert text is not None
    assert "the release slips a week" in text, "суть по-английски потеряна"
    assert "ship on the 15th" in text and "update the plan" in text and "budget for QA?" in text


def test_russian_and_legacy_headings_still_work(tmp_path):
    graph = tmp_path / "Работа"
    _meeting(graph, "2026-08-10 10-00 — Планёрка",
             "# Саммари\n\n**Суть одной строкой:** договорились о релизе\n\n"
             "## Решения\n- релиз 15-го\n\n## Поручения\n- Анна: план\n\n## Открытые вопросы\n- бюджет?\n")
    text = morning_brief.build_brief(graph)
    assert "договорились о релизе" in text
    assert "релиз 15-го" in text and "Анна: план" in text and "бюджет?" in text
