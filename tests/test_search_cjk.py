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


def test_редкие_блоки_тоже_режутся():
    """Расширения B+ и совместимость: иначе редкий иероглиф проваливал весь
    запрос в фолбэк «искать всю фразу целиком» (ревью 19.08, DeepSeek)."""
    assert memory_bench.cjk_grams("𠀀𠀁") == ["𠀀𠀁"]
    assert memory_bench.cjk_grams("City") == []


def test_повтор_иглы_не_удваивает_счёт(tmp_path):
    """«服务服务商» давал биграмму «服务» дважды, и файл с ней обгонял
    релевантный (ревью 19.08, DeepSeek)."""
    graph = tmp_path / "g"
    graph.mkdir()
    (graph / "a.md").write_text("服务服务服务\n", encoding="utf-8")
    (graph / "b.md").write_text("服务商选型：YuPay\n", encoding="utf-8")

    out = memory_bench.search(graph, "服务商")

    assert out.index("b.md") < out.index("a.md"), "точное совпадение должно быть выше"


def test_язык_синтеза_задан_для_всех_трёх():
    """Обычный прогон бенча спрашивает на языке vault (sufler.language),
    а не всегда по-русски."""
    assert set(memory_bench.SYNTH) == {"ru", "en", "zh"}
    for lang, (prompt, system) in memory_bench.SYNTH.items():
        assert "{q}" in prompt and "{found}" in prompt, lang
        assert system.strip(), lang


def test_пробелы_между_иероглифами_не_ломают_сверку():
    """Модель пишет «9 月 1 日» и «9月1日» вперемешку — в китайском пробел
    не значим, и строгая сверка давала ложный провал на верном ответе."""
    assert memory_bench.contains("9月", "网店计划于 **9 月 1 日** 上线")
    assert memory_bench.contains("9月", "9月1日上线")
    assert not memory_bench.contains("9月", "十月一日上线")


def test_у_латиницы_сверка_осталась_строгой():
    """Сжатие включается только для иероглифов: «pay ment» — не «payment»."""
    assert memory_bench.contains("YuPay", "we take YuPay")
    assert not memory_bench.contains("YuPay", "we take Yu Pay")
