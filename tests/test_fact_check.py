"""Сверка фактов документа со стенограммой.

Случаи взяты с реальной встречи 2026-07-22, где прогон минуток приписал
задачу TASK 3242 — в стенограмме её нет ни разу.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from fact_check import annotate, unanchored  # noqa: E402

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
