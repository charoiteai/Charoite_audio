"""Второй проход разбора встречи кусками помельче (№821).

Модель на одной нарезке пропускает около трети решений, а другая нарезка той же
стенограммы ловит другое: на замере 27.10 (17 встреч) 12000 ∪ 6000 дали 77–82 %
покрытия против 60–65 % у одного прохода. Тесты держат то, что сломало бы граф
владельца молча: порядок решений (brain берёт первые шесть), канал второго
прохода (только решения), склейку, которая не теряет разные решения, и то, что
сбой добавки не отнимает готовый первый проход.
"""
import pathlib
import sys
import types

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graph_updater as g  # noqa: E402

ON = {"sufler": {"graph_second_pass": True}}
OFF = {"sufler": {"graph_second_pass": False}}


@pytest.fixture
def model(monkeypatch):
    """Подставная модель: первые `n1` вызовов — первый проход, дальше — второй."""
    m = types.SimpleNamespace(calls=[], events=[], answers={}, reports=[], n1=3)

    def fake_extract(cfg, text, rule=""):
        m.calls.append(len(text))
        m.events.append("extract")
        got = m.answers.get(1 if len(m.calls) <= m.n1 else 2)
        return got(len(m.calls)) if callable(got) else got

    monkeypatch.setattr(g, "_extract", fake_extract)
    monkeypatch.setattr(g.llm_health, "ensure_alive", lambda cfg, log: True)
    monkeypatch.setattr(g, "_yield_to_live", lambda: m.events.append("yield"))
    monkeypatch.setattr(g, "_report", lambda n, total: m.reports.append((n, total)))
    return m


def _first(**extra):
    base = {"название": "Встреча", "люди": [{"имя": "Ольга"}], "сущности": [{"имя": "Сервис отчётов"}],
            "решения": ["Перенести релиз сервиса отчётов на пятницу", "Ольга готовит план"]}
    base.update(extra)
    return base


LONG = "реплика. " * 3000          # 27 000 знаков: 3 куска по 12000 и 6 кусков по 6000


def test_flag_off_is_the_old_extract(model):
    """Флаг выключен — ни одного лишнего вызова модели, результат прежний."""
    model.answers[1] = lambda n: _first()
    data = g.extract(OFF, LONG)
    assert len(model.calls) == len(g._split(LONG, g.CHUNK_CHARS, g.CHUNK_OVERLAP)) == 3
    assert data["решения"] == _first()["решения"]
    assert model.reports == [(1, 3), (2, 3), (3, 3)]


def test_default_is_off(model):
    """Умолчание — выключено: восьмигигабайтной машине вторая минута модели на встречу не по карману."""
    model.answers[1] = lambda n: _first()
    g.extract({}, LONG)
    assert len(model.calls) == 3


def test_second_pass_appends_only_new_decisions_to_the_tail(model):
    """Решения первого — первыми: brain уносит первые шесть, второй проход их не подменяет."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": ["Релиз сервиса отчётов перенести на пятницу после проверки",
                                              "Заказать стенд нагрузочного тестирования"]}
    data = g.extract(ON, LONG)
    assert data["решения"] == ["Перенести релиз сервиса отчётов на пятницу", "Ольга готовит план",
                               "Заказать стенд нагрузочного тестирования"]


def test_second_pass_gives_decisions_only(model):
    """Люди, сущности и название — из первого прохода: второй канал с ними не сверен."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"название": "Другое", "люди": [{"имя": "Пётр"}],
                                  "сущности": [{"имя": "Новая система"}], "решения": ["Новое решение по бюджету"]}
    data = g.extract(ON, LONG)
    assert data["название"] == "Встреча"
    assert [p["имя"] for p in data["люди"]] == ["Ольга"]
    assert [e["имя"] for e in data["сущности"]] == ["Сервис отчётов"]
    assert "Новое решение по бюджету" in data["решения"]


@pytest.mark.parametrize("first, second", [
    # Три пары из разбора промахов №820: склейка по общим словам на прежнем счёте
    # приняла их за одно решение, а это разные дела.
    ("Проверить отсутствие непрогруженных данных перед переключением",
     "Переключиться на новый контур без перелива данных"),
    ("Настроить кэш справочника и время жизни записей",
     "Читать значения из справочника напрямую вместо кэша"),
    ("Передать отчёт по тестам коллеге из соседней команды",
     "Уточнить у партнёров детали пилота"),
])
def test_merge_keeps_different_decisions(first, second):
    assert g._second_pass_decisions([first], [second]) == [second]


def test_merge_drops_the_same_decision_reworded():
    assert g._second_pass_decisions(["Перенести релиз сервиса отчётов на пятницу"],
                                    ["Релиз сервиса отчётов перенести на пятницу после проверки"]) == []


def test_merge_drops_literal_repeat_and_junk():
    assert g._second_pass_decisions(["Да"], ["да", "", "  ", None, {"текст": "x"}]) == []


def test_merge_is_asymmetric_inside_the_second_pass():
    """Внутри второго прохода похожие решения не склеиваются друг с другом: там действует только _dedup."""
    second = ["Перенести релиз сервиса отчётов на пятницу", "Релиз сервиса отчётов перенести на пятницу утром"]
    assert g._second_pass_decisions(["Совсем другое дело"], second) == second


def test_second_pass_failure_keeps_first(model):
    """Сбой добавки — разбор первого прохода, а не «модель недоступна» для всей встречи."""
    model.answers[1] = lambda n: _first()

    def boom(n):
        raise requests.ConnectionError("модель встала")
    model.answers[2] = boom
    data = g.extract(ON, LONG)
    assert data == _first()


def test_second_pass_empty_parts_keep_first(model):
    model.answers[1] = lambda n: _first()
    model.answers[2] = None
    assert g.extract(ON, LONG) == _first()


def test_failed_first_pass_skips_second(model):
    """Первый проход не дал ничего — второй не тратит модель: граф всё равно не обновится."""
    model.answers[1] = None
    model.answers[2] = lambda n: {"решения": ["x"]}
    assert g.extract(ON, LONG) is None
    assert len(model.calls) == 3


def test_short_meeting_has_no_second_pass(model):
    """Стенограмма не длиннее куска второго прохода — та же нарезка, лишний вызов ни к чему."""
    model.answers[1] = lambda n: _first()
    g.extract(ON, "реплика. " * 600)          # 5400 знаков
    assert len(model.calls) == 1


def test_medium_meeting_gets_second_pass(model):
    """Встреча между 6000 и 12000: первый проход одним куском, второй — двумя."""
    model.n1 = 1
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету"]}
    text = "реплика. " * 1000                  # 9000 знаков
    data = g.extract(ON, text)
    assert len(model.calls) == 1 + len(g._split(text, g.CHUNK_CHARS_2, g.CHUNK_OVERLAP_2)) == 3
    assert data["решения"][-1] == "Новое решение по бюджету"
    assert model.reports == [(1, 3), (2, 3), (3, 3)]


def test_one_progress_counter_and_yield_before_every_later_chunk(model):
    """Счёт частей в статусе — общий на оба прохода; живой встрече уступаем перед каждым куском после первого."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": []}
    g.extract(ON, LONG)
    n1 = len(g._split(LONG, g.CHUNK_CHARS, g.CHUNK_OVERLAP))
    n2 = len(g._split(LONG, g.CHUNK_CHARS_2, g.CHUNK_OVERLAP_2))
    assert model.reports == [(n, n1 + n2) for n in range(1, n1 + n2 + 1)]
    # Перед разбором — одна уступка, дальше — перед каждым куском, кроме самого первого.
    assert model.events.count("yield") == n1 + n2
    first_extract = model.events.index("extract")
    assert all(model.events[i - 1] == "yield" for i, e in enumerate(model.events) if e == "extract" and i > first_extract)
