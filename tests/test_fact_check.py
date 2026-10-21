"""Сверка фактов документа со стенограммой.

Случаи взяты с реальной встречи 2026-07-22, где прогон минуток приписал
задачу TASK 3242 — в стенограмме её нет ни разу.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from fact_check import _dates, annotate, unanchored  # noqa: E402

TRANSCRIPT = (
    "[10:32] Собеседник 1: есть задачка TASK 3278, по ней какие-то вопросы?\n"
    "[10:35] Пётр: помечено как релиз сентября, а ждём в август TASK 3318\n"
    "[10:38] Собеседник 3: срок перенесли на 15.08, край — 23 августа\n"
    "[10:41] Пётр: репликацию поднимем 14.08\n"
)


def test_real_facts_pass():
    doc = "- **Пётр** — выкатить TASK 3318 — 15.08\n- TASK 3278 ждёт препрод"
    assert unanchored(doc, TRANSCRIPT) == []


def test_invented_task_caught():
    """Тот самый TASK 3242 из прогона 1."""
    doc = "- Статус задачи TASK 3242 на препроде"
    assert unanchored(doc, TRANSCRIPT) == ["TASK 3242"]


def test_invented_date_caught():
    doc = "- **Пётр** — сдать отчёт — 31.12"
    assert unanchored(doc, TRANSCRIPT) == ["31.12"]


def test_word_date_matches_numeric():
    """«23 августа» в стенограмме и «23.08» в документе — один и тот же день."""
    assert unanchored("срок 23.08", TRANSCRIPT) == []


def test_numeric_date_matches_word():
    assert unanchored("срок 14 августа", TRANSCRIPT) == []


def test_task_without_system_name():
    """STT часто теряет название системы — голого номера достаточно."""
    assert unanchored("задача 3318 в работе", TRANSCRIPT) == []


def test_dash_form():
    assert unanchored("TASK-3318 готова", TRANSCRIPT) == []


def test_case_insensitive():
    assert unanchored("TASK 3318 готова", TRANSCRIPT) == []


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_empty_input_is_safe(empty):
    assert unanchored(empty, TRANSCRIPT) == []
    assert unanchored("TASK 3242", empty) == []


def test_annotate_adds_footnote():
    doc = "# Минутки\n- TASK 3242 на препроде"
    out = annotate(doc, TRANSCRIPT)
    assert "TASK 3242" in out
    assert "⚠️" in out
    assert out.startswith(doc)  # исходный текст не тронут


def test_annotate_keeps_clean_document_intact():
    doc = "# Минутки\n- TASK 3318 к 15.08"
    assert annotate(doc, TRANSCRIPT) == doc


def test_version_number_is_not_a_date():
    """«1.5» в «версия 1.5» — не 1 мая."""
    assert "01.05" not in unanchored("версия 1.5 выкачена", TRANSCRIPT)


def test_multiple_findings_deduplicated():
    doc = "TASK 3242 и снова TASK 3242, срок 31.12"
    assert unanchored(doc, TRANSCRIPT) == ["31.12", "TASK 3242"]


def test_year_is_not_a_task():
    """«до начала 2028» — год, а не задача Начала-2028 (архив минуток, 3 файла)."""
    assert unanchored("запуск до начала 2028", TRANSCRIPT) == []


def test_year_range_boundaries():
    assert unanchored("план на 2100", TRANSCRIPT) == []
    assert unanchored("задача ABC 2101", TRANSCRIPT) == ["ABC 2101"]


# --- даты словами (№762b) ---------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    # число + месяц
    ("23 августа", {"23.08"}),
    ("5 сентября", {"05.09"}),
    ("2 мая", {"02.05"}),
    ("8 марта", {"08.03"}),
    ("до 3 май", {"03.05"}),
    ("23 авг.", {"23.08"}),
    # число с окончанием
    ("4-го октября", {"04.10"}),
    ("4го октября", {"04.10"}),
    ("4-е октября", {"04.10"}),
    ("4-ое октября", {"04.10"}),
    # день словом: четыре падежа, «е» и «ё»
    ("четвёртое октября", {"04.10"}),
    ("четвёртого октября", {"04.10"}),
    ("четвёртому октября", {"04.10"}),
    ("в четвёртом октябре", {"04.10"}),
    ("четвертого октября", {"04.10"}),
    ("первого мая", {"01.05"}),
    ("второго марта", {"02.03"}),
    ("третьего апреля", {"03.04"}),
    ("третье апреля", {"03.04"}),
    ("седьмого ноября", {"07.11"}),
    ("одиннадцатого декабря", {"11.12"}),
    ("девятнадцатого июня", {"19.06"}),
    ("двадцатого июля", {"20.07"}),
    ("тридцатого января", {"30.01"}),
    # составные
    ("двадцать пятого октября", {"25.10"}),
    ("двадцать третьего августа", {"23.08"}),
    ("тридцать первого марта", {"31.03"}),
    ("Двадцать Пятого Октября", {"25.10"}),
    # знак препинания разрывает составное: остаётся «пятого октября»
    ("было двадцать, пятого октября", {"05.10"}),
    # с годом — всё равно ДД.ММ
    ("04.10.2026", {"04.10"}),
    ("срок 15/08/2026", {"15.08"}),
    ("1.5.2026", {"01.05"}),
    ("04.10.2026г.", {"04.10"}),
    ("сдать 15.08г", {"15.08"}),
    ("2026-10-04", {"04.10"}),
    ("перенесли на 12.10-15.10", {"12.10", "15.10"}),
    ("с 12.10.2026 – 15.10.2026", {"12.10", "15.10"}),
    ("сдать 15.10-20.10", {"15.10", "20.10"}),
    ("12.10.-15.10.", {"12.10", "15.10"}),
    ("до 23\u00a0августа", {"23.08"}),
    ("до четвёртого\u00a0октября", {"04.10"}),
    # несколько дат
    ("четвёртого октября или 23 августа", {"04.10", "23.08"}),
])
def test_dates_positive(text, expected):
    assert _dates(text) == expected


@pytest.mark.parametrize("text", [
    "2 масштаба",
    "23 магазина",
    "первого материала",
    "3 маршрута",
    "5 марок",
    "версия 1.5.2",
    "1.15.2",
    "сборка 2.15.3",
    "в списке 3\nмая",
    "2026-13-04",
    "версии 1.5-2.3",
    "v1.5.2",
    "тридцать второго октября",
    "сорок пятого мая",
    "двадцать десятого мая",
    "32 октября",
    "0 мая",
    "тридцать седьмого августа",
    # граница, которую не закрываем: число без месяца
    "четвёртого числа",
    "к четвёртому",
])
def test_dates_negative(text):
    assert _dates(text) == set()


def test_ordinal_word_matches_numeric_document():
    """Дефект 1: «четвёртого октября» в стенограмме, «04.10» в документе."""
    assert unanchored("срок 04.10", "созвонимся четвёртого октября") == []
    assert unanchored("срок 25.10", "двадцать пятого октября сдаём") == []
    assert unanchored("срок 04.10", "до 4-го октября") == []


def test_year_form_matches_word():
    assert unanchored("срок 04.10.2026", "четвёртого октября") == []
    assert unanchored("срок четвёртого октября", "срок 04.10.2026") == []


def test_prefix_month_does_not_hide_invented_date():
    """Дефект 2: «2 масштаба» не должно подтверждать выдуманный срок 02.05."""
    assert unanchored("сдать 02.05", "нам нужно 2 масштаба") == ["02.05"]
    assert unanchored("сдать 23.05", "заведём 23 магазина") == ["23.05"]
    assert unanchored("сдать 02 мая", "нам нужно 2 масштаба") == ["02.05"]


def test_invented_ordinal_date_caught():
    assert unanchored("срок пятого ноября", TRANSCRIPT) == ["05.11"]
