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


def test_демо_флаг_называет_язык_своего_графа():
    """Русский демо-граф спрашивается по-русски, даже если в конфиге стоит en:
    язык графа задаёт флаг, а не настройка чужого vault."""
    cfg = {"sufler": {"language": "en"}}
    assert memory_bench.resolve_lang(cfg, demo_zh=True, demo_en=False, demo=True) == "zh"
    assert memory_bench.resolve_lang(cfg, demo_zh=False, demo_en=True, demo=True) == "en"
    assert memory_bench.resolve_lang(cfg, demo_zh=False, demo_en=False, demo=True) == "ru"


def test_обычный_прогон_берёт_язык_из_конфига():
    for value, expect in [("en", "en"), ("ZH", "zh"), (" ru ", "ru")]:
        cfg = {"sufler": {"language": value}}
        assert memory_bench.resolve_lang(cfg, demo_zh=False, demo_en=False, demo=False) == expect


def test_мусор_и_пустой_конфиг_дают_русский():
    """Пустой config.yaml проходит yaml.safe_load как None — и падал бы
    AttributeError вместо честной работы по умолчанию."""
    for cfg in [None, {}, {"sufler": None}, {"sufler": {}},
                {"sufler": {"language": "клингонский"}},
                {"sufler": {"language": 42}}]:
        assert memory_bench.resolve_lang(cfg, demo_zh=False, demo_en=False, demo=False) == "ru", cfg


def test_иглы_не_дублируются():
    """«服务服务商» даёт «服务» дважды: без дедупа игла весила вдвое, и файл с
    одной частой биграммой обгонял релевантный (ревью 19.08, DeepSeek)."""
    words, grams = memory_bench.needles("服务服务商", set())
    assert grams == ["服务", "务服", "务商"]
    assert words == []


def test_слова_тоже_дедуплицируются_и_стоп_слова_режутся():
    words, grams = memory_bench.needles("бюджет бюджет проекта это", {"это"})
    assert words == ["бюджет", "проекта"]
    assert grams == []


def test_полноширинные_цифры_совпадают_с_обычными():
    """Китайские модели пишут «９月１日» наравне с «9月1日»."""
    assert memory_bench.contains("9月", "９月１日上线")
    assert memory_bench.contains("YuPay", "选了 ＹｕＰａｙ")


def test_сжатие_пробелов_не_склеивает_латиницу():
    """Один случайный иероглиф рядом не должен менять вердикт по латинскому
    факту: «YuPay» и «Yu Pay» — разные строки (ревью 19.08, второй круг)."""
    assert not memory_bench.contains("YuPay 支付", "Yu Pay 支付")
    assert memory_bench.contains("YuPay 支付", "YuPay 支 付")


def test_редкие_расширения_и_дополнение_совместимости():
    assert memory_bench.cjk_grams("\U0002EBF0") == ["\U0002EBF0"]
    assert memory_bench.cjk_grams("\U0002F800") == ["\U0002F800"]


def test_идеографический_пробел_схлопывается():
    """U+3000 — обычный пробел китайского текста, модели его ставят."""
    assert memory_bench.contains("9月", "9　月　1　日上线")


def test_перенос_строки_не_склеивает_соседние_абзацы():
    """«…решили 9\\n月报告…» — это конец одной мысли и начало другой,
    а не дата (ревью 20.08, локальная голова)."""
    assert not memory_bench.contains("9月", "срок 9\n月报告 отдельно")
    assert memory_bench.contains("9月", "срок 9 月 1 日")


def test_расширения_G_и_H_тоже_режутся():
    """G и H лежат отдельным островом выше 0x30000: диапазон «B–I» их не
    покрывал, а лейбл утверждал обратное (ревью 20.08, DeepSeek)."""
    assert memory_bench.cjk_grams("\U00030000") == ["\U00030000"]
    assert memory_bench.cjk_grams("\U000323AF") == ["\U000323AF"]


def test_полноширинный_запрос_находит_обычный_текст(tmp_path):
    """Гейт файла и скоринг смотрят на текст одинаково: раньше rx искал по
    сырому тексту и выбрасывал файл, который скоринг бы засчитал."""
    graph = tmp_path / "g"
    graph.mkdir()
    (graph / "a.md").write_text("выбрали YuPay\n", encoding="utf-8")

    assert "YuPay" in memory_bench.search(graph, "ＹｕＰａｙ")


def test_ранжирование_сквозняком(tmp_path):
    """Сквозная проверка порядка выдачи, а не только состава игл: файл с
    точным совпадением обязан быть выше файла с частой биграммой."""
    graph = tmp_path / "g"
    graph.mkdir()
    (graph / "частая.md").write_text("服务服务服务服务\n", encoding="utf-8")
    (graph / "точная.md").write_text("服务商选型：YuPay\n", encoding="utf-8")

    out = memory_bench.search(graph, "服务商选型")

    assert out.index("точная.md") < out.index("частая.md")


def test_щели_между_блоками_не_считаются_иероглифами():
    """Границы снаружи диапазонов: «не ловят лишнего» должно быть проверено,
    а не заявлено (ревью 20.08, четвёртый круг)."""
    assert memory_bench.cjk_grams("\U0002EE60") == []   # щель после I
    assert memory_bench.cjk_grams("\U0002FA20") == []   # щель после дополнения
    assert memory_bench.cjk_grams("\U000323B0") == []   # сразу после H


def test_полноширинный_текст_находится_обычным_запросом(tmp_path):
    """Основной путь гейта — по СЛОВАМ, а не через фолбэк «вся фраза»:
    смешанный запрос против файла с полноширинными буквами."""
    graph = tmp_path / "g"
    graph.mkdir()
    (graph / "a.md").write_text("выбрали ＹｕＰａｙ для оплаты\n", encoding="utf-8")

    out = memory_bench.search(graph, "YuPay 支付")

    assert "a.md" in out
