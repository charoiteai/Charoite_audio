"""Поиск по архиву на языках без пробелов (китайский, японский, корейский).

Токенизатор запроса резал только кириллицу и латиницу: у иероглифического
вопроса список слов выходил ПУСТЫМ, поиск скатывался на «найди всю фразу
целиком» и не находил ничего. Замер 19.08 на демо-графах: китайский 0/3
против английского 2/3 — при том, что факты в графе лежали.

Лечится биграммами — стандартный приём для языков без пробелов.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import memory_bench  # noqa: E402


def test_иероглифы_дают_биграммы():
    assert memory_bench.cjk_grams("支付服务商") == ["支付", "付服", "服务", "务商"]


def test_одиночный_иероглиф_не_теряется():
    assert memory_bench.cjk_grams("九") == ["九"]


def test_куски_запроса_разделяются_знаками_препинания():
    grams = memory_bench.cjk_grams("现在有哪些阻碍？")
    assert "阻碍" in grams and "？" not in "".join(grams)


def test_латиница_и_кириллица_сюда_не_попадают():
    assert memory_bench.cjk_grams("YuPay contract 2026") == []
    assert memory_bench.cjk_grams("платёжный провайдер") == []


def test_японский_и_корейский_тоже_режутся():
    assert memory_bench.cjk_grams("会議メモ")[0] == "会議"
    assert memory_bench.cjk_grams("결제") == ["결제"]


def test_поиск_находит_файл_по_иероглифам(tmp_path):
    """Сквозная проверка: без биграмм эта выдача была пустой."""
    graph = tmp_path / "graph"
    (graph / "会议").mkdir(parents=True)
    (graph / "会议" / "2026-07-17_1400.md").write_text(
        "# 会议 — 支付服务商选型\n\n决定：选 YuPay，费率 2.8%。\n", encoding="utf-8")

    found = memory_bench.search(graph, "支付服务商最后定了哪一家？")

    assert "YuPay" in found, "поиск обязан найти файл по иероглифам запроса"
