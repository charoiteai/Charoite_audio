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


# --- Дата словами: один экстрактор, все формы сводятся к ДД.ММ (№762b) ---

@pytest.mark.parametrize("text,want", [
    # порядковый день: именительный, родительный, дательный, предложный
    ("четвёртое октября", "04.10"),
    ("четвёртого октября", "04.10"),
    ("четвёртому октября", "04.10"),
    ("четвёртом октября", "04.10"),
    # «е» вместо «ё»
    ("четвертое октября", "04.10"),
    ("четвертого октября", "04.10"),
    # составные — только вплотную; запятая оставляет день отдельным
    ("двадцать пятого октября", "25.10"),
    ("тридцать первого октября", "31.10"),
    ("двадцать пятое октября", "25.10"),
    ("было двадцать, пятого октября", "05.10"),
    # день числом с окончанием
    ("4-го октября", "04.10"),
    ("4го октября", "04.10"),
    ("4-е октября", "04.10"),
    ("4-ое октября", "04.10"),
    # ДД.ММ.ГГГГ — это ДД.ММ
    ("04.10.2026", "04.10"),
    # месяц словом и числом, падежные формы мая и марта
    ("15 августа", "15.08"),
    ("15 август", "15.08"),
    ("23 мая", "23.05"),
    ("23 май", "23.05"),
    ("23 мае", "23.05"),
    ("23 маю", "23.05"),
    ("23 марта", "23.03"),
    ("23 март", "23.03"),
    ("23 марте", "23.03"),
    ("23 марту", "23.03"),
])
def test_date_forms_are_recognized(text, want):
    assert _dates(text) == {want}


@pytest.mark.parametrize("month,num", [
    ("января", "01"), ("февраля", "02"), ("марта", "03"), ("апреля", "04"),
    ("мая", "05"), ("июня", "06"), ("июля", "07"), ("августа", "08"),
    ("сентября", "09"), ("октября", "10"), ("ноября", "11"),
    ("декабря", "12"),
])
def test_every_month_genitive(month, num):
    assert _dates(f"5 {month}") == {f"05.{num}"}


@pytest.mark.parametrize("text", [
    "2 масштаба",             # было 02.05 из префикса «ма»
    "23 магазина",            # было 23.05
    "3 маршрута",             # было 03.03 из префикса «мар»
    "первого материала",      # месяц не назван
    "версия 1.5.2",           # версия, а не дата
    "1.5.2",
    "тридцать второго октября",  # день 32 — не дата
    "четвёртого числа",       # месяц не назван — граница не закрывается
])
def test_non_dates_stay_non_dates(text):
    assert _dates(text) == set()


def test_fabricated_date_is_not_hidden_by_a_month_prefix():
    """«2 масштаба» больше не даёт 02.05 — выдуманный срок виден в сноске."""
    assert unanchored("сдать 02.05", "нам нужно 2 масштаба") == ["02.05"]


def test_ordinal_day_matches_numeric_date():
    """«четвёртого октября» в стенограмме снимает сноску с «04.10»."""
    assert unanchored("срок 04.10", "перенесли на четвёртого октября") == []


def test_full_year_date_matches_numeric():
    assert unanchored("срок 04.10.2026", "перенесли на 04.10") == []
