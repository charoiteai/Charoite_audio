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
    # Метка — токен целиком: буква в ней различает дела (выход n824-out3, DS C1, Haiku C1).
    ("Перенести стенд A1 на новую площадку", "Перенести стенд B1 на новую площадку", False),
    ("Перенести базу на сервер app1 в понедельник", "Перенести базу на сервер db1 в понедельник", False),
    ("Перенести стенд К1 на новую площадку", "Перенести стенд М1 на новую площадку", False),
    ("Перенести стенд A1 на новую площадку", "перенести стенд a1 на новую площадку", True),  # регистр не важен
    ("Закрыть отчёт по бюджету за Q1", "Закрыть отчёт по бюджету за H1", False),
    ("Отдельное дело номер 12 по закупке", "Отдельное дело номер 21 по закупке", False),
    # Цена токена целиком — метка и то же число отдельно не склеиваются: недосклейка,
    # безопасная сторона (выход n824-out2, DS I1, Sonnet I4; долг №615).
    ("Закрыть отчёт по бюджету за Q3", "Закрыть отчёт по бюджету за 3 квартал", False),
    ("Увеличить тестовый стенд нагрузки до 2 ТБ", "Увеличить тестовый стенд нагрузки до 2ТБ", False),
    ("Перенести встречу на 15го декабря", "Перенести встречу на 15 декабря", False),
    ("Перенести встречу на 15-го декабря", "Перенести встречу на 15го декабря", False),
    ("Увеличить тестовый стенд нагрузки до 2ТБ", "Увеличить тестовый стенд нагрузки до 2", False),
    # Короткие слова — метки, отрицание — противоположное дело (финальный Opus №824, C1).
    ("Выдать доступ к CRM команде аналитики", "Выдать доступ к DWH команде аналитики", False),
    ("Выдать доступ к БД команде аналитики", "Выдать доступ к ИС команде аналитики", False),
    ("Оля готовит отчёт по релизу", "Яна готовит отчёт по релизу", False),
    ("Перенести релиз отчётов", "Не переносить релиз отчётов", False),
    ("Переключить контур без перелива данных", "Переключить контур с переливом данных", False),
    ("Выдать доступ команде аналитики", "Выдать доступ к CRM команде аналитики", True),   # уточнение
    ("Не переносить релиз отчётов", "Релиз отчётов не переносить", True),
    # «ё» в метке — та же метка (выход n824-out4, Sonnet M2, Haiku M2).
    ("Перенести стенд зелёный2 на новую площадку", "Перенести стенд зеленый2 на новую площадку", True),
    # Разделитель внутри числа держит его целым: каждый из «.», «:», «/» — своей парой.
    ("Созвон по отчётам в 11:30", "Созвон по отчётам 30-го в 11", False),
    ("Выгрузить отчёт по бюджету версии 2.5", "Выгрузить отчёт по бюджету версии 5 от 2 числа", False),
    ("Перенести релиз отчётов на 12/12", "Перенести релиз отчётов на 12/01", False),
    ("Перенести релиз отчётов на 12.12.2026", "Перенести релиз отчётов на 12.12.2027", False),
    # Дата с годом и без — разные числа: недосклейка, безопасная сторона (долг №615).
    ("Перенести релиз отчётов на 12.12.2026", "Перенести релиз отчётов на 12.12", False),
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


def test_negation_guard_also_holds_between_passes():
    """Второй проход не теряет противоположное решение: на main «Не переносить…» глушилось
    «Перенести…» первого прохода (финальный Opus №824, C1)."""
    assert g._second_pass_decisions(["Перенести релиз отчётов"],
                                    ["Не переносить релиз отчётов"]) == ["Не переносить релиз отчётов"]


def test_first_occurrence_keeps_the_slot_even_when_shorter():
    """Остаётся первое вхождение, даже если позднее подробнее: подмена слота поглощала осколки
    чужих дел и зависела от порядка (выход n824-out2, DS I2, Sonnet I2)."""
    short, rich = "Перенести релиз сервиса отчётов", "Перенести релиз сервиса отчётов на пятницу из-за блокера по данным"
    third = "Блокер по данным отчётов сервиса"
    assert g._merge_decisions([short, rich, third]) == ([short, third], [(rich, short)])


@pytest.mark.parametrize("first", [None, "Подготовить два тестовых стенда к 12 декабря", {"текст": "x"}, 5])
def test_mixed_shapes_across_first_pass_chunks_do_not_crash(model, first):
    """Часть дала null, строку, словарь или число, следующая — список: разбор встречи не падает
    (выход n824-out1, DS C2, Haiku C1), строка сохраняется решением, прочее — нет. Ключ вне
    схемы — тоже без падения."""
    model.answers[1] = {"название": "Встреча", "решения": first, "люди": first, "прочее": first}
    model.answers[2] = {"решения": ["Заказать стенд нагрузочного тестирования"], "люди": [{"имя": "Ольга"}],
                        "прочее": ["x"]}
    data = g.extract(_cfg(), LONG)
    want = [first] if isinstance(first, str) else []
    assert data["решения"] == want + ["Заказать стенд нагрузочного тестирования"]
    assert data["люди"] == want + [{"имя": "Ольга"}]
    assert data["прочее"] == ["x"]
    assert data["название"] == "Встреча"


@pytest.mark.parametrize("parts", [
    [{"название": "Встреча"}, {"название": ["Встреча"], "проект": {"x": 1}}],
    [{"название": None}, {"название": ["Встреча"]}],
    [{"название": ["Встреча"], "проект": ["Отчёты"]}],
])
def test_text_fields_stay_text(model, parts, capsys):
    """Название и проект — строки: список или словарь отбрасывается на шве ответа, `main` делает
    `.strip()` (выход n824-out2, DS C1, Haiku C1)."""
    for n, part in enumerate(parts, 1):
        model.answers[n] = dict(part, решения=[])
    text = LONG if len(parts) > 1 else "реплика. " * 100
    data = g.extract(_cfg(), text)
    assert isinstance(data.get("название") or "", str)
    assert isinstance(data.get("проект") or "", str)
    assert "а не строка — отброшено" in capsys.readouterr().out


def test_two_string_decisions_from_two_parts_are_both_kept(model):
    """Строка вместо списка в двух частях — оба решения, а не первое (выход n824-out2, DS C1)."""
    model.answers[1] = {"решения": "Заказать стенд нагрузочного тестирования"}
    model.answers[2] = {"решения": "Отдельное дело по закупке"}
    assert g.extract(_cfg(), LONG)["решения"] == ["Заказать стенд нагрузочного тестирования",
                                                  "Отдельное дело по закупке"]


@pytest.mark.parametrize("raw, out", [
    ("Перенести релиз на пятницу", ["Перенести релиз на пятницу"]),
    ([MINUTES_A, MINUTES_B, None], [MINUTES_A]),
])
def test_short_meeting_gets_the_same_exit(model, raw, out):
    """Встреча в один кусок идёт через тот же выход: форма и склейка (выход n824-out1, Sonnet M2, Haiku M2)."""
    model.answers[1] = {"решения": raw}
    assert g.extract(_cfg(), "реплика. " * 100)["решения"] == out


def test_shaped_fixes_schema_fields_only(capsys):
    """Шов формы ответа: текст — строка или нет поля, список — список; прочее как пришло."""
    got = g._shaped({"название": ["Встреча"], "проект": None, "люди": "  ", "сущности": "Отчёты",
                     "связи": {"от": "a"}, "темы": 5, "ядра": None, "решения": ["A"], "прочее": "x"})
    assert got == {"люди": [], "сущности": ["Отчёты"], "связи": [], "темы": [], "ядра": [],
                   "решения": ["A"], "прочее": "x"}
    out = capsys.readouterr().out
    assert "«название» — list, а не строка" in out and "«проект»" not in out
    assert "«связи» — dict, а не список" in out and "«темы» — int" in out and "«ядра»" not in out
    assert g._shaped(None) is None and g._shaped(["x"]) == ["x"]
    assert g._shaped({"название": "Встреча"}) == {"название": "Встреча"}


def test_final_decisions_logs_only_what_it_drops(capsys):
    """Лог «отброшено» — только когда отброшено, None — пустой список без лога о типе
    (выжившие мутанты двери на a7c8e82f, CI #871)."""
    off = _cfg(graph_decision_merge=False)
    assert g._final_decisions(off, {"решения": ["A", "B"]})["решения"] == ["A", "B"]
    assert g._final_decisions(off, {"решения": None})["решения"] == []
    assert capsys.readouterr().out == ""
    assert g._final_decisions(off, {"решения": ["A", 5, " "]})["решения"] == ["A"]
    assert "отброшено не-строк и пустых: 2" in capsys.readouterr().out
