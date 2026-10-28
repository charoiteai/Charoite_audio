"""Второй проход разбора встречи кусками помельче (№821).

Модель на одной нарезке пропускает около трети решений, а другая нарезка той же
стенограммы ловит другое: на замере 27.10 (17 встреч) 12000 ∪ 6000 дали 77–82 %
покрытия против 57–65 % у одного прохода. Тесты держат то, что сломало бы граф
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
    m = types.SimpleNamespace(calls=[], events=[], answers={}, reports=[], n1=3, live=False)

    def fake_extract(cfg, text, rule="", raise_http=False):
        m.calls.append(len(text))
        m.events.append("extract")
        got = m.answers.get(1 if len(m.calls) <= m.n1 else 2)
        return got(len(m.calls)) if callable(got) else got

    monkeypatch.setattr(g, "_extract", fake_extract)
    monkeypatch.setattr(g.llm_health, "ensure_alive", lambda cfg, log: True)
    monkeypatch.setattr(g, "_yield_to_live", lambda: m.events.append("yield"))
    monkeypatch.setattr(g, "_report", lambda n, total: m.reports.append((n, total)))
    # Живая встреча — из теста, а не из лока на машине, где идут тесты. Подменяем
    # источник в live_gate, а не `_live_now`: иначе сама проверка не исполнялась бы.
    def alive(root):
        m.events.append("live?")
        return m.live() if callable(m.live) else m.live
    monkeypatch.setattr(g.live_gate, "daemon_alive", alive)
    return m


def _first(**extra):
    base = {"название": "Встреча", "люди": [{"имя": "Ольга"}], "сущности": [{"имя": "Сервис отчётов"}],
            "решения": ["Перенести релиз сервиса отчётов на пятницу", "Ольга готовит план"]}
    base.update(extra)
    return base


LONG = "реплика. " * 3000          # 27 000 знаков: 3 куска по 12000 и 5 кусков по 6000


def test_flag_off_is_the_old_extract(model):
    """Флаг выключен — ни одного лишнего вызова модели, результат прежний."""
    model.answers[1] = lambda n: _first()
    data = g.extract(OFF, LONG)
    assert len(model.calls) == len(g._split(LONG, g.CHUNK_CHARS, g.CHUNK_OVERLAP)) == 3
    assert data["решения"] == _first()["решения"]
    assert model.reports == [(1, 3), (2, 3), (3, 3)]


def test_default_is_off(model):
    """Умолчание — выключено: +2–2,5 минуты модели на каждую встречу решает владелец, а не установка."""
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
    assert model.reports == [(1, 3), (2, 3), (3, 3), (3, 3)]


def test_one_progress_counter_and_live_check_before_every_later_chunk(model):
    """Счёт частей в статусе — общий на оба прохода и доходит до конца. Первый проход
    уступает живой встрече, второй — проверяет её без ожидания перед каждым куском."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": []}
    g.extract(ON, LONG)
    n1 = len(g._split(LONG, g.CHUNK_CHARS, g.CHUNK_OVERLAP))
    n2 = len(g._split(LONG, g.CHUNK_CHARS_2, g.CHUNK_OVERLAP_2, short_tail=False))
    total = n1 + n2
    assert model.reports == [(n, total) for n in range(1, total + 1)] + [(total, total)]
    # Перед разбором — одна уступка, дальше — перед каждым куском первого прохода, кроме самого первого.
    assert model.events.count("yield") == n1
    extracts = [i for i, e in enumerate(model.events) if e == "extract"]
    assert all(model.events[i - 1] == "yield" for i in extracts[1:n1])
    assert all(model.events[i - 1] == "live?" for i in extracts[n1:])


def test_chunk_numbers_are_pinned():
    """Нарезка — часть замера: другие числа кусков значили бы другую цену и другое покрытие."""
    assert len(LONG) == 27_000
    first = g._split(LONG, g.CHUNK_CHARS, g.CHUNK_OVERLAP)
    second = g._split(LONG, g.CHUNK_CHARS_2, g.CHUNK_OVERLAP_2, short_tail=False)
    assert (g.CHUNK_CHARS_2, g.CHUNK_OVERLAP_2) == (6_000, 500)
    assert g.SECOND_PASS_MIN_SHARED >= 1               # порог склейки не пускает пустое пересечение
    assert [len(p) for p in first] == [12_000, 12_000, 5_000]
    assert [len(p) for p in second] == [6_000, 6_000, 6_000, 6_000, 5_000]
    for parts, overlap in ((first, g.CHUNK_OVERLAP), (second, g.CHUNK_OVERLAP_2)):
        for a, b in zip(parts, parts[1:]):
            assert a[-overlap:] == b[:overlap]


@pytest.mark.parametrize("length, parts", [(11_400, 2), (11_500, 2), (11_501, 3)])
def test_second_pass_drops_tail_inside_the_overlap(length, parts):
    """Хвост не длиннее нахлёста целиком лежит в прошлом куске — лишнего вызова модели нет."""
    text = "x" * length
    got = g._split(text, g.CHUNK_CHARS_2, g.CHUNK_OVERLAP_2, short_tail=False)
    assert len(got) == parts
    assert got[-1].endswith(text[-g.CHUNK_OVERLAP_2:])        # конец стенограммы покрыт


def test_first_pass_keeps_its_old_tail():
    """Первый проход режет как прежде, даже короткий хвост: флаг выключен — ни одного изменения."""
    assert len(g._split("x" * 22_500, g.CHUNK_CHARS, g.CHUNK_OVERLAP)) == 3


def test_short_tail_costs_no_model_call(model):
    model.n1 = 1
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": []}
    g.extract(ON, "x" * 11_400)
    assert len(model.calls) == 1 + 2


@pytest.mark.parametrize("lang", ["en", "zh", "EN", "de", "kk", "en-US", "rus"])
def test_no_second_pass_outside_russian(model, lang):
    """Порог склейки мерен на русском: в zh основ нет, в en похожими делают служебные слова;
    любой другой язык тоже без замера — белый список, а не «кроме en/zh» (n822-in1, DS C1)."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету"]}
    data = g.extract({"sufler": {"graph_second_pass": True, "language": lang}}, LONG)
    assert len(model.calls) == 3
    assert data == _first()


@pytest.mark.parametrize("lang", ["RU", "ru-RU", "ru_RU", " ru "])
def test_russian_named_explicitly_keeps_second_pass(model, lang):
    """Регион к тегу языка — тот же русский промпт, и второй проход с ним (n822-out1)."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету"]}
    data = g.extract({"sufler": {"graph_second_pass": True, "language": lang}}, LONG)
    assert len(model.calls) == 3 + 5
    assert data["решения"][-1] == "Новое решение по бюджету"


def test_second_pass_string_instead_of_list_adds_nothing(model):
    """Строка вместо списка дописала бы решения по букве."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": "Новое решение по бюджету"}
    assert g.extract(ON, LONG) == _first()


def test_junk_with_the_same_text_does_not_eat_the_decision(model):
    """Словарь с текстом решения перед самим решением: без фильтра не-строк `_dedup` счёл бы
    решение повтором словаря и выбросил (выход n821-out2, DS M2)."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": [{"текст": "Новое решение по бюджету"}, "Новое решение по бюджету"]}
    assert g.extract(ON, LONG)["решения"][2:] == ["Новое решение по бюджету"]


def test_second_pass_skips_junk_items_but_keeps_strings(model):
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": [None, {"текст": "x"}, "  ", 5, "Новое решение по бюджету"]}
    assert g.extract(ON, LONG)["решения"][2:] == ["Новое решение по бюджету"]


def test_first_pass_string_is_not_split_into_letters(model):
    model.answers[1] = lambda n: _first(решения="Перенести релиз сервиса отчётов на пятницу")
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету",
                                              "Релиз сервиса отчётов перенести на пятницу утром"]}
    assert g.extract(ON, LONG)["решения"] == ["Перенести релиз сервиса отчётов на пятницу",
                                             "Новое решение по бюджету"]


def test_live_meeting_before_second_pass_keeps_first_without_waiting(model):
    """Первый проход готов, началась встреча — граф пишется сейчас, добавка не ждёт часами."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету"]}
    model.live = True
    data = g.extract(ON, LONG)
    assert len(model.calls) == 3
    assert data == _first()
    assert model.events.count("yield") == 3          # уступки только первого прохода
    assert model.reports[-1] == (8, 8)


def test_live_meeting_mid_second_pass_keeps_what_was_found(model):
    """Встреча началась перед третьим куском второго прохода — проход обрывается без ожидания,
    решения первых двух кусков остаются (выход n821-out2, Sonnet I1)."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": [f"Отдельное дело номер {n} по закупке"]}
    model.live = lambda: len(model.calls) >= 3 + 2
    data = g.extract(ON, LONG)
    assert len(model.calls) == 3 + 2
    assert data["решения"][2:] == [f"Отдельное дело номер {n} по закупке" for n in (4, 5)]
    assert "yield" not in model.events[model.events.index("live?"):]
    assert model.reports[-1] == (8, 8)


def test_failure_of_a_late_chunk_keeps_what_earlier_chunks_found(model):
    """Сбой разбора куска теряет только этот кусок; сетевой сбой обрывает проход — следующий
    кусок ждал бы тот же таймаут. Найденное до сбоя остаётся (выходы n821-out1 и out2)."""
    model.answers[1] = lambda n: _first()

    def second(n):
        if n == 3 + 4:
            raise requests.ReadTimeout("кусок не успел")
        if n == 3 + 2:
            raise ValueError("ответ не разобрался")
        return {"решения": [f"Отдельное дело номер {n} по закупке"]}
    model.answers[2] = second
    data = g.extract(ON, LONG)
    assert len(model.calls) == 3 + 4                  # пятого куска после сетевого сбоя нет
    assert data["решения"][2:] == [f"Отдельное дело номер {n} по закупке" for n in (4, 6)]
    assert model.reports[-1] == (8, 8)


@pytest.mark.parametrize("a, b, same", [
    ({"a", "b"}, {"a", "b", "c"}, False),                              # доля 1,0, но общих 2
    ({"a", "b", "c", "d", "e"}, {"a", "b", "c", "x", "y"}, False),     # общих 3, доля 0,6
    ({"a", "b", "c"}, {"a", "b", "c", "x", "y", "z"}, True),           # общих 3, доля 1,0
    (set("abcdefghij"), set("abcdefgxyz"), True),                      # общих 7, доля ровно 0,7
    (set("abcdefghij"), set("abcdefxyzw"), False),                     # общих 6, доля 0,6
])
def test_merge_threshold_edges(a, b, same):
    assert g._same_decision(a, b) is same


def test_first_pass_string_is_written_as_list_even_without_additions(model):
    """Нормализованная форма пишется всегда: строка дальше итерировалась бы по буквам (выход n821-out2, DS I1)."""
    model.answers[1] = lambda n: _first(решения="Перенести релиз сервиса отчётов на пятницу")
    model.answers[2] = lambda n: {"решения": []}
    assert g.extract(ON, LONG)["решения"] == ["Перенести релиз сервиса отчётов на пятницу"]


@pytest.mark.parametrize("odd", [{"текст": "решение"}, 5])
def test_first_pass_odd_shape_is_left_alone(model, odd):
    """Словарь или число вместо списка — добавка их не затирает и модель зря не зовёт."""
    model.answers[1] = lambda n: _first(решения=odd)
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету"]}
    data = g.extract(ON, LONG)
    assert data["решения"] == odd
    assert len(model.calls) == 3
    assert model.reports[-1] == (8, 8)


@pytest.mark.parametrize("cfg, lang", [
    ({}, "ru"), ({"sufler": None}, "ru"), ({"sufler": {}}, "ru"), ({"sufler": {"language": None}}, "ru"),
    ({"sufler": {"language": " ru "}}, "ru"), ({"sufler": {"language": " EN "}}, "en"),
    ({"sufler": {"language": "zh"}}, "zh"),
])
def test_language_is_read_like_the_prompt(cfg, lang):
    assert g._language(cfg) == lang


@pytest.mark.parametrize("lang", [" ru ", None, ""])
def test_russian_by_default_keeps_second_pass(model, lang):
    """Пустой или неаккуратный ключ языка — промпт русский, значит и второй проход есть."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету"]}
    data = g.extract({"sufler": {"graph_second_pass": True, "language": lang}}, LONG)
    assert len(model.calls) == 3 + 5
    assert data["решения"][-1] == "Новое решение по бюджету"


def test_first_pass_split_unchanged_with_flag_on(model):
    """Сквозь `extract`: короткий хвост первого прохода (≤ 1000) по-прежнему идёт своим куском —
    флаг меняет только второй проход."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": []}
    g.extract(ON, "x" * 22_500)
    assert model.calls[:3] == [12_000, 11_500, 500]
    assert len(model.calls) == 3 + 4                  # второй: 0, 5500, 11000, 16500; хвост 500 — в нахлёсте


@pytest.mark.parametrize("junk", [{"текст": "Заказать стенд нагрузочного тестирования"}, 5, None])
def test_merge_ignores_non_strings_among_first_pass_decisions(junk):
    """Не-строка среди решений первого — не решение: не глушит решение второго и не роняет склейку."""
    x = "Заказать стенд нагрузочного тестирования"
    assert g._second_pass_decisions([junk], [x]) == [x]


@pytest.mark.parametrize("drop", [True, False])
def test_first_pass_without_decisions_takes_the_second(model, drop):
    """У первого прохода решений нет (ключа нет или список пуст) — добавка становится списком решений."""
    first = _first(решения=[])
    if drop:
        del first["решения"]
    model.answers[1] = lambda n: dict(first)
    model.answers[2] = lambda n: {"решения": ["Новое решение по бюджету"]}
    assert g.extract(ON, LONG)["решения"] == ["Новое решение по бюджету"]


def test_stems_are_pinned():
    """Основы — часть порога замера: префикс пять букв, слова от четырёх букв, ё = е, числа целиком."""
    assert g._stems("Отчёт по релизу сервиса, ещё раз 15") == {"отчет", "релиз", "серви", "15"}
    assert g._stems("план") == {"план"}                 # ровно четыре буквы — уже слово
    assert g._stems("Да") == set()                      # короткое слово основы не даёт
    assert g._stems("15 20 30") == {"15", "20", "30"}   # числа — основы при любой длине


_NO_STEMS = g._stems("Да")


@pytest.mark.parametrize("a, b", [(_NO_STEMS, {"а1", "б2", "в3"}), ({"а1", "б2", "в3"}, _NO_STEMS),
                                  (_NO_STEMS, _NO_STEMS)])
def test_empty_stems_are_never_the_same_decision(a, b):
    """Решение без основ (короткие слова без основ, «Да») ни с чем не склеивается и не делит на ноль."""
    assert a == set() or b == set()
    assert g._same_decision(a, b) is False


def test_empty_stems_do_not_divide_even_without_the_share_floor(monkeypatch):
    """Делитель стережёт себя сам: и с порогом общих 0 пустое решение не склеивается и не делит на ноль."""
    monkeypatch.setattr(g, "SECOND_PASS_MIN_SHARED", 0)
    assert g._same_decision(set(), g._stems("бюджет релиза сервиса")) is False


def test_second_pass_failure_outside_chunks_keeps_the_first(model, monkeypatch, capsys):
    """Сбой добавки вне цикла кусков (склейка) не отнимает разбор первого прохода."""
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": ["Собрать стенд нагрузки к среде"]}

    def broken(first, found):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(g, "_second_pass_decisions", broken)
    data = g.extract(ON, LONG)
    assert data["решения"] == _first()["решения"]
    assert "второй проход упал (ZeroDivisionError" in capsys.readouterr().out


@pytest.mark.parametrize("extra, second", [(0, False), (1, True)])
def test_second_pass_starts_strictly_above_its_chunk(model, extra, second):
    """Ровно CHUNK_CHARS_2 знаков: первый проход — один вызов (≤ CHUNK_CHARS), второго нет — один
    кусок второго прохода ничего не добавил бы к разбору целиком. На знак больше: первый проход
    по-прежнему один вызов, второй — два куска (начала 0 и 5500, хвост 501 > CHUNK_OVERLAP_2)."""
    model.n1 = 1
    model.answers[1] = lambda n: _first()
    model.answers[2] = lambda n: {"решения": []}
    g.extract(ON, "x" * (g.CHUNK_CHARS_2 + extra))
    assert len(model.calls) == (1 + 2 if second else 1)


def test_merge_treats_yo_as_ye():
    """Пара склеивается только через ё → е: иначе общих основ две, а не три."""
    assert g._second_pass_decisions(["Утвердить отчёт сервиса"], ["Утвердить отчет сервиса завтра"]) == []


class _FakeLLM:
    """Подставной клиент модели: запоминает system и отвечает по очереди из `script`."""
    systems: list = []
    script: list = []

    def __init__(self, cfg):
        pass

    def complete(self, prompt, *, system=None, **kw):
        type(self).systems.append(system)
        step = type(self).script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def fake_llm(monkeypatch):
    _FakeLLM.systems, _FakeLLM.script = [], []
    monkeypatch.setattr(g, "LLM", _FakeLLM)
    monkeypatch.setattr(g.llm_health, "ensure_alive", lambda cfg, log: True)
    monkeypatch.setattr(g, "_yield_to_live", lambda: None)
    monkeypatch.setattr(g, "_report", lambda n, total: None)
    monkeypatch.setattr(g.live_gate, "daemon_alive", lambda root: False)
    return _FakeLLM


@pytest.mark.parametrize("lang, english", [("en", True), ("ru", False)])
def test_prompt_language_branch(fake_llm, lang, english):
    fake_llm.script = ['{"решения": []}']
    g._extract({"llm": {"model": "m"}, "sufler": {"language": lang}}, "реплика.")
    assert ("LANGUAGE: write every field VALUE in English" in fake_llm.systems[0]) is english


@pytest.mark.parametrize("error, stops", [
    (g.LLMHTTPError(503, "занята"), True),
    (g.LLMHTTPError(400, "плохой запрос"), False),
    (g.LLMHTTPError(200, "x", kind="broken"), False),
])
def test_model_refusal_in_the_second_pass(fake_llm, capsys, error, stops):
    """«Занята/недоступна» на первом куске второго прохода — проход останавливается, а не ждёт
    тот же срок на каждом следующем куске (финальный Opus, I1). Прочий отказ — про этот кусок:
    проход идёт дальше (проверка правок Opus, I1). Первый проход остаётся в обоих случаях."""
    fake_llm.script = ['{"решения": ["Перенести релиз сервиса отчётов на пятницу"]}'] * 3 + [error] + [
        '{"решения": ["Заказать стенд нагрузочного тестирования"]}'] * 4
    data = g.extract({"llm": {"model": "m"}, "sufler": {"graph_second_pass": True}}, LONG)
    out = capsys.readouterr().out
    if stops:
        assert len(fake_llm.systems) == 3 + 1
        assert data["решения"] == ["Перенести релиз сервиса отчётов на пятницу"]
        assert "второй проход прерван" in out
        assert f"дольше {g.BUSY_WAIT // 60:.0f} мин\n" in out      # причина есть, обещания повтора нет
        assert "повтор подберёт" not in out
    else:
        assert len(fake_llm.systems) == 3 + 5
        assert data["решения"] == ["Перенести релиз сервиса отчётов на пятницу",
                                   "Заказать стенд нагрузочного тестирования"]
        assert "второй проход прерван" not in out
        assert "второго прохода упала" in out


def test_busy_model_in_the_first_pass_is_unchanged(fake_llm, capsys):
    """Первый проход «модель занята» по-прежнему пропускает кусок и идёт дальше."""
    fake_llm.script = [g.LLMHTTPError(503, "занята"), '{"решения": ["Решение второго куска"]}',
                       '{"решения": []}']
    data = g.extract({"llm": {"model": "m"}}, LONG)
    assert len(fake_llm.systems) == 3
    assert data["решения"] == ["Решение второго куска"]
    out = capsys.readouterr().out
    assert f"дольше {g.BUSY_WAIT // 60:.0f} мин — часть пропущена; повтор подберёт незавершённую встречу" in out
