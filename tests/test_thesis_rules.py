"""Автотезисы: 📌 и 💭, без 💎 — и фильтр, который это гарантирует.

💎 «ценная информация» убран 03.08: факты разговора ведёт нить, третий
поток тех же фактов в ленте тезисов был дублированием. Правила вынесены из
daemon.py, чтобы тестироваться без sounddevice.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import thesis_rules  # noqa: E402


def test_prompt_has_no_gem_and_keeps_both_live_marks():
    assert "💎" not in thesis_rules.THINK_SYSTEM
    assert "📌" in thesis_rules.THINK_SYSTEM
    assert "💭" in thesis_rules.THINK_SYSTEM


def test_parse_keeps_only_live_prefixes():
    out = ("Вот что важно:\n"
           "📌 решили катить волнами, срок пятница\n"
           "💎 бюджет 2 млн\n"
           "💭 не спросили про откат\n"
           "NONE")
    assert thesis_rules.parse(out) == [
        "📌 решили катить волнами, срок пятница",
        "💭 не спросили про откат",
    ]


def test_parse_none_only_gives_empty():
    assert thesis_rules.parse("NONE") == []
    assert thesis_rules.parse("  none\n") == []
    assert thesis_rules.parse("") == []


def test_strip_mark_knows_legacy_gem():
    # 💎 остался в старых записях — дедупу его надо уметь раздеть
    assert thesis_rules.strip_mark("💎 бюджет 2 млн") == "бюджет 2 млн"
    assert thesis_rules.strip_mark("📌 срок пятница") == "срок пятница"
