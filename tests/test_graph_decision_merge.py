"""Итоговая склейка решений разбора встречи (№824).

Части разбора идут с нахлёстом, минутки лежат в хвосте стенограммы и попадают в две
части сразу — модель пересказывает одно решение разными словами, и в заметке оно
стоит дважды-трижды. Тесты держат то, что сломало бы граф владельца молча: первое
вхождение остаётся на месте (brain берёт первые шесть), разные дела не склеиваются,
форма списка приводится на единственном выходе `extract`, выключатель и язык
слушаются, а сбой склейки не отнимает решения.
"""
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graph_updater as g  # noqa: E402
import install_profile  # noqa: E402

LONG = "реплика. " * 3000          # 27 000 знаков: 3 куска первого прохода

# Пересказ одного решения минуток двумя частями первого прохода.
MINUTES_A = "Подготовить два тестовых стенда к 12 декабря"
MINUTES_B = "К 12 декабря подготовить два тестовых стенда"


@pytest.fixture
def model(monkeypatch):
    """Подставная модель первого прохода: ответ части `n` — `answers[n]`."""
    m = types.SimpleNamespace(answers={})

    def fake_extract(cfg, text, rule="", raise_http=False):
        m.answers.setdefault("n", 0)
        m.answers["n"] += 1
        return m.answers.get(m.answers["n"])

    monkeypatch.setattr(g, "_extract", fake_extract)
    monkeypatch.setattr(g.llm_health, "ensure_alive", lambda cfg, log: True)
    monkeypatch.setattr(g, "_yield_to_live", lambda: None)
    monkeypatch.setattr(g, "_report", lambda n, total: None)
    monkeypatch.setattr(g.live_gate, "daemon_alive", lambda root: False)
    return m


def _cfg(**sufler):
    # Второй проход выключен: дубль первого прохода — фикстура без него (вход n824-in1, Haiku V1).
    return {"sufler": {"graph_second_pass": False, **sufler}}


def test_minutes_read_twice_by_the_first_pass_give_one_decision(model, capsys):
    """Две части первого прохода пересказали одно решение минуток — в заметку идёт одно, первое."""
    model.answers[1] = {"решения": [MINUTES_A, "Ольга готовит план"]}
    model.answers[2] = {"решения": [MINUTES_B]}
    model.answers[3] = {"решения": ["Заказать стенд нагрузочного тестирования"]}
    data = g.extract(_cfg(), LONG)
    assert data["решения"] == [MINUTES_A, "Ольга готовит план", "Заказать стенд нагрузочного тестирования"]
    assert f"граф: склеено решение: «{MINUTES_B}» ~ «{MINUTES_A}»" in capsys.readouterr().out


@pytest.mark.parametrize("sufler", [
    {"graph_decision_merge": False}, {"graph_decision_merge": "нет"}, {"graph_decision_merge": "fasle"},
    {"language": "en"}, {"language": "zh"}, {"language": "en", "graph_decision_merge": True},
])
def test_merge_off_by_key_or_language(model, sufler):
    """Ключ выключает склейку; порог мерен на русском — en и zh не клеят даже с явным `true`."""
    model.answers[1] = {"решения": [MINUTES_A]}
    model.answers[2] = {"решения": [MINUTES_B]}
    assert g.extract(_cfg(**sufler), LONG)["решения"] == [MINUTES_A, MINUTES_B]


@pytest.mark.parametrize("sufler, on", [
    ({}, True), ({"graph_decision_merge": None}, True), ({"graph_decision_merge": " "}, True),
    ({"graph_decision_merge": "да"}, True), ({"graph_decision_merge": "off"}, False),
    ({"graph_decision_merge": "fasle"}, False), ({"language": " RU "}, True), ({"language": "en-US"}, False),
    ({"language": "ru-RU"}, False), ({"language": "ru_RU"}, False),
])
def test_merge_key_is_read_like_the_second_pass(sufler, on):
    assert install_profile.graph_decision_merge_enabled({"sufler": sufler}) is on
    assert install_profile.graph_decision_merge_enabled({}) is True


@pytest.mark.parametrize("first, second", [
    # Пары из разбора промахов №820 — разные дела при общих словах.
    ("Проверить отсутствие непрогруженных данных перед переключением",
     "Переключиться на новый контур без перелива данных"),
    ("Настроить кэш справочника и время жизни записей",
     "Читать значения из справочника напрямую вместо кэша"),
    ("Передать отчёт по тестам коллеге из соседней команды",
     "Уточнить у партнёров детали пилота"),
])
def test_different_decisions_stay(first, second):
    assert g._merge_decisions([first, second]) == ([first, second], [])


def test_first_occurrence_stays_in_place():
    """Склеенное уходит из хвоста, порядок оставшихся прежний; дословный повтор без регистра — тоже дубль."""
    kept, pairs = g._merge_decisions(["Ольга готовит план", MINUTES_A, "Новое дело по бюджету",
                                      MINUTES_B, "ольга готовит план "])
    assert kept == ["Ольга готовит план", MINUTES_A, "Новое дело по бюджету"]
    assert pairs == [(MINUTES_B, MINUTES_A), ("ольга готовит план ", "Ольга готовит план")]


def test_decisions_without_stems_are_kept():
    """Решение без основ («Да») ни с чем не склеивается — только дословно."""
    assert g._merge_decisions(["Да", "Нет", "да", " Да "]) == (["Да", "Нет"], [("да", "Да"), (" Да ", "Да")])


@pytest.mark.parametrize("raw, out", [
    ("Перенести релиз на пятницу", ["Перенести релиз на пятницу"]),
    ("  ", []),
    (None, []),
    (["Перенести релиз на пятницу", "", "  ", None, 5, {"текст": "x"}], ["Перенести релиз на пятницу"]),
    ({"текст": "x"}, []),
    (7, []),
])
def test_shape_is_a_list_of_strings_on_the_way_out(raw, out):
    """Строка ушла бы потребителям по буквам, словарь и число — не решения (вход n824-in1, DS C2)."""
    data = g._final_decisions({}, {"название": "Встреча", "решения": raw})
    assert data["решения"] == out
    assert data["название"] == "Встреча"


@pytest.mark.parametrize("data", [None, {}, {"название": "Встреча"}])
def test_no_decisions_key_is_left_as_is(data):
    """Нет разбора — None; нет ключа решений — ключ не появляется."""
    assert g._final_decisions({}, data) == data


def test_shape_is_fixed_even_with_merge_off(capsys):
    """Выключатель выключает склейку, а не приведение формы."""
    data = g._final_decisions({"sufler": {"graph_decision_merge": False}},
                              {"решения": [MINUTES_A, None, MINUTES_B]})
    assert data["решения"] == [MINUTES_A, MINUTES_B]
    assert "отброшено не-строк и пустых: 1" in capsys.readouterr().out


@pytest.mark.parametrize("a, b, same", [
    ("Отдельное дело номер 4 по закупке", "Отдельное дело номер 6 по закупке", False),   # чужие числа
    ("Подготовить отчёт по стенду 1 к 15 декабря", "Подготовить отчёт по стенду 2 к 15 декабря", False),
    # Метки с цифрой внутри слова и даты — основы их теряют или режут (выход n824-out1, DS C1, Sonnet I1).
    ("Отключить старый бакет s3", "Отключить старый бакет s4", False),
    ("Перенести встречу на понедельник 15го для проверки бакетов",
     "Перенести встречу на понедельник 22го для проверки бакетов", False),
    ("Перенести релиз отчётов на 12.12", "Перенести релиз отчётов на 12.01", False),
    ("Закрыть отчёт по бюджету за Q3", "Закрыть отчёт по бюджету за Q4", False),
    ("Перенести встречу на завтра 11:00", "Перенести встречу на завтра 11:30", False),
    ("Перенести встречу на завтра 11:00", "Перенести встречу на завтра 11:00 для разбора", True),
    ("Перенести встречу на понедельник 16:00 для проверки бакетов",
     "Перенести встречу на понедельник для проверки бакетов", True),                     # число с одной стороны
    ("До 11 ноября решить вопрос с загрузкой инкрементов", "До 11 ноября решить загрузку инкрементов 2", True),
    ("Перенести релиз сервиса отчётов на пятницу", "Релиз сервиса отчётов перенести на пятницу", True),
])
def test_different_numbers_are_different_decisions(a, b, same):
    """Разные числа при общих словах — разные дела; число только у одной стороны — уточнение (№824)."""
    assert g._same_fingerprint(g._fingerprint(a), g._fingerprint(b)) is same
    assert g._same_fingerprint(g._fingerprint(b), g._fingerprint(a)) is same
    assert (len(g._merge_decisions([a, b])[0]) == 1) is same


def test_numbers_guard_also_holds_between_passes():
    """Склейка проходов читает тот же отпечаток: «за Q4» второго прохода не глушится «за Q3» первого."""
    assert g._second_pass_decisions(["Закрыть отчёт по бюджету за Q3"],
                                    ["Закрыть отчёт по бюджету за Q4"]) == ["Закрыть отчёт по бюджету за Q4"]


def test_richer_wording_takes_the_slot():
    """Позднее решение с подробностями занимает слот короткого: позиция прежняя, сказанное не теряется."""
    short, rich = "Перенести релиз сервиса отчётов", "Перенести релиз сервиса отчётов на пятницу из-за блокера по данным"
    assert g._merge_decisions(["Ольга готовит план", short, rich]) == (["Ольга готовит план", rich], [(short, rich)])
    # Обратный порядок — первое и так подробнее, остаётся оно.
    assert g._merge_decisions([rich, short]) == ([rich], [(short, rich)])
    # Подробнее по словам, но без метки короткого — слот не отдаётся: метка дороже слов.
    tagged, wordy = "Закрыть отчёт по бюджету за Q3", "Закрыть отчёт по бюджету подробно с разбивкой по отделам"
    assert g._merge_decisions([tagged, wordy]) == ([tagged], [(wordy, tagged)])


def test_literal_repeat_of_the_replaced_text_is_still_a_repeat():
    short, rich = "Перенести релиз сервиса отчётов", "Перенести релиз сервиса отчётов на пятницу из-за блокера по данным"
    assert g._merge_decisions([short, rich, short.upper()])[0] == [rich]


@pytest.mark.parametrize("first", [None, "Подготовить два тестовых стенда к 12 декабря", {"текст": "x"}])
def test_mixed_shapes_across_first_pass_chunks_do_not_crash(model, first):
    """Часть дала null, строку или словарь, следующая — список: разбор встречи не падает (выход
    n824-out1, DS C2, Haiku C1), строка сохраняется решением, прочее — нет."""
    model.answers[1] = {"название": "Встреча", "решения": first, "люди": first}
    model.answers[2] = {"решения": ["Заказать стенд нагрузочного тестирования"], "люди": [{"имя": "Ольга"}]}
    data = g.extract(_cfg(), LONG)
    want = [first] if isinstance(first, str) else []
    assert data["решения"] == want + ["Заказать стенд нагрузочного тестирования"]
    assert data["люди"][-1] == {"имя": "Ольга"}


@pytest.mark.parametrize("raw, out", [
    ("Перенести релиз на пятницу", ["Перенести релиз на пятницу"]),
    ([MINUTES_A, MINUTES_B, None], [MINUTES_A]),
])
def test_short_meeting_gets_the_same_exit(model, raw, out):
    """Встреча в один кусок идёт через тот же выход: форма и склейка (выход n824-out1, Sonnet M2, Haiku M2)."""
    model.answers[1] = {"решения": raw}
    assert g.extract(_cfg(), "реплика. " * 100)["решения"] == out
